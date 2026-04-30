from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from app.core.config import settings
from app.services.password_service import password_service


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StoreUser:
    user_id: int
    account_id: str
    username: str | None
    full_name: str
    email: str | None
    role: str
    provider: str
    password_hash: str | None
    is_active: bool
    provider_user_id: str | None = None
    last_login_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class StoreService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._db_path = settings.metadata_db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL UNIQUE,
                    username TEXT UNIQUE,
                    full_name TEXT NOT NULL,
                    email TEXT,
                    role TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_user_id TEXT,
                    password_hash TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    last_login_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS platforms (
                    platform_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform_key TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    host_type TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    owner_user_id INTEGER NOT NULL,
                    host_secret TEXT NOT NULL UNIQUE,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS platform_admins (
                    platform_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    assigned_by INTEGER,
                    is_primary INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(platform_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS platform_registration_requests (
                    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    applicant_user_id INTEGER NOT NULL,
                    platform_key TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    justification TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    review_comment TEXT NOT NULL DEFAULT '',
                    reviewed_by INTEGER,
                    reviewed_at TEXT,
                    approved_platform_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_registration_requests_active_key
                ON platform_registration_requests(platform_key)
                WHERE status IN ('pending', 'approved');

                CREATE TABLE IF NOT EXISTS platform_llm_configs (
                    platform_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    provider_kind TEXT NOT NULL DEFAULT 'litellm',
                    api_format TEXT NOT NULL DEFAULT 'openai-compatible',
                    base_url TEXT NOT NULL,
                    model TEXT NOT NULL,
                    api_key TEXT,
                    extra_headers_json TEXT NOT NULL DEFAULT '{}',
                    extra_body_json TEXT NOT NULL DEFAULT '{}',
                    network_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS platform_prompt_configs (
                    platform_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    system_prompt TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_llm_configs (
                    user_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    provider_kind TEXT NOT NULL DEFAULT 'litellm',
                    api_format TEXT NOT NULL DEFAULT 'openai-compatible',
                    base_url TEXT NOT NULL,
                    model TEXT NOT NULL,
                    api_key TEXT,
                    extra_headers_json TEXT NOT NULL DEFAULT '{}',
                    extra_body_json TEXT NOT NULL DEFAULT '{}',
                    network_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL UNIQUE,
                    platform_id INTEGER,
                    owner_user_id INTEGER,
                    external_user_id TEXT,
                    external_org_id TEXT,
                    conversation_key TEXT,
                    title TEXT NOT NULL,
                    host_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_message_at TEXT NOT NULL,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_conversations_owner
                ON conversations(owner_user_id, updated_at DESC);

                CREATE INDEX IF NOT EXISTS idx_conversations_platform_user
                ON conversations(platform_id, external_user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS session_runtimes (
                    session_id TEXT PRIMARY KEY,
                    conversation_id TEXT,
                    platform_id INTEGER,
                    owner_user_id INTEGER,
                    external_user_id TEXT,
                    container_name TEXT,
                    container_id TEXT,
                    image TEXT NOT NULL,
                    status TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 0,
                    network_mode TEXT NOT NULL DEFAULT 'none',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_started_at TEXT,
                    last_used_at TEXT,
                    idle_expires_at TEXT,
                    max_expires_at TEXT,
                    destroyed_at TEXT,
                    destroy_reason TEXT,
                    restart_count INTEGER NOT NULL DEFAULT 0,
                    workspace_root TEXT NOT NULL DEFAULT '',
                    home_root TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_session_runtimes_status
                ON session_runtimes(status, last_used_at DESC);
                """
            )
            self._ensure_column(conn, "users", "last_login_at", "TEXT")
            self._ensure_column(conn, "platform_admins", "assigned_by", "INTEGER")
            self._ensure_column(conn, "platform_admins", "is_primary", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "platform_admins", "updated_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "platform_llm_configs", "network_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "user_llm_configs", "network_json", "TEXT NOT NULL DEFAULT '{}'")
            conn.execute("DROP TABLE IF EXISTS admin_whitelist")
            self._migrate_roles(conn)
            self._backfill_platform_admin_metadata(conn)
        self._seed_default_users()
        self._seed_standalone_platform()

    def _ensure_column(self, conn: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
        columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
        if column_name in columns:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")

    def _migrate_roles(self, conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE users SET role = 'user' WHERE role = 'platform_admin'")

    def _backfill_platform_admin_metadata(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            UPDATE platform_admins
            SET updated_at = COALESCE(NULLIF(updated_at, ''), created_at)
            WHERE updated_at IS NULL OR updated_at = ''
            """
        )
        conn.execute(
            """
            UPDATE platform_admins
            SET is_primary = 1
            WHERE is_primary = 0
              AND EXISTS (
                  SELECT 1
                  FROM platforms
                  WHERE platforms.platform_id = platform_admins.platform_id
                    AND platforms.owner_user_id = platform_admins.user_id
              )
            """
        )

    def _seed_default_users(self) -> None:
        self.ensure_local_user(
            username=settings.auth_system_admin_username,
            password=settings.auth_system_admin_password,
            full_name="System Administrator",
            role="system_admin",
        )

    def _seed_standalone_platform(self) -> None:
        admin = self.get_user_by_username(settings.auth_system_admin_username)
        if not admin:
            return
        existing = self.get_platform_by_key("standalone")
        if existing is not None:
            self.update_platform_basics(
                platform_key="standalone",
                display_name="AetherCore",
                host_type="standalone",
                description="Default built-in platform for direct login and internal workbench access.",
            )
            self.add_platform_admin(
                platform_id=existing["platform_id"],
                user_id=admin.user_id,
                assigned_by=admin.user_id,
                is_primary=True,
            )
            return
        self.create_platform(
            platform_key="standalone",
            display_name="AetherCore",
            host_type="standalone",
            description="Default built-in platform for direct login and internal workbench access.",
            owner_user_id=admin.user_id,
            assigned_by=admin.user_id,
        )

    def ensure_local_user(self, *, username: str, password: str, full_name: str, role: str) -> StoreUser:
        existing = self.get_user_by_username(username)
        now = utcnow_iso()
        password_hash = password_service.hash_password(password)
        if existing:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE users
                    SET full_name = ?, role = ?, password_hash = ?, is_active = 1, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (full_name, role, password_hash, now, existing.user_id),
                )
            refreshed = self.get_user_by_username(username)
            if refreshed is None:
                raise RuntimeError("Failed to refresh seeded user")
            return refreshed

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    account_id, username, full_name, email, role, provider,
                    provider_user_id, password_hash, is_active, last_login_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?)
                """,
                (
                    f"acct_{uuid.uuid4().hex[:12]}",
                    username,
                    full_name,
                    None,
                    role,
                    "password",
                    username,
                    password_hash,
                    now,
                    now,
                ),
            )
        user = self.get_user_by_username(username)
        if user is None:
            raise RuntimeError("Failed to initialize seeded user")
        return user

    def get_user_by_username(self, username: str) -> StoreUser | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return self._row_to_user(row)

    def get_user_by_id(self, user_id: int) -> StoreUser | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return self._row_to_user(row)

    def get_user_by_provider(self, provider: str, provider_user_id: str) -> StoreUser | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE provider = ? AND provider_user_id = ?",
                (provider, provider_user_id),
            ).fetchone()
        return self._row_to_user(row)

    def update_user_login_metadata(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE user_id = ?",
                (utcnow_iso(), utcnow_iso(), user_id),
            )

    def create_or_update_oauth_user(
        self,
        *,
        provider: str,
        provider_user_id: str,
        full_name: str,
        email: str | None,
        role: str = "user",
    ) -> StoreUser:
        existing = self.get_user_by_provider(provider, provider_user_id)
        now = utcnow_iso()
        if existing:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE users
                    SET full_name = ?, email = ?, role = ?, is_active = 1, last_login_at = ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (full_name, email, existing.role or role, now, now, existing.user_id),
                )
            refreshed = self.get_user_by_provider(provider, provider_user_id)
            if refreshed is None:
                raise RuntimeError("Failed to refresh OAuth user")
            return refreshed

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    account_id, username, full_name, email, role, provider,
                    provider_user_id, password_hash, is_active, last_login_at, created_at, updated_at
                ) VALUES (?, NULL, ?, ?, ?, ?, ?, NULL, 1, ?, ?, ?)
                """,
                (
                    f"acct_{uuid.uuid4().hex[:12]}",
                    full_name,
                    email,
                    role,
                    provider,
                    provider_user_id,
                    now,
                    now,
                    now,
                ),
            )
        created = self.get_user_by_provider(provider, provider_user_id)
        if created is None:
            raise RuntimeError("Failed to create OAuth user")
        return created

    def list_users(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY user_id ASC").fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            user_id = int(row["user_id"])
            items.append(
                {
                    **dict(row),
                    "is_active": bool(row["is_active"]),
                    "managed_platform_ids": self.list_managed_platform_ids(user_id),
                }
            )
        return items

    def update_user_role(self, *, user_id: int, role: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET role = ?, updated_at = ? WHERE user_id = ?",
                (role, utcnow_iso(), user_id),
            )

    def count_users_with_role(self, role: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM users WHERE role = ?", (role,)).fetchone()
        return int(row["count"]) if row else 0

    def create_platform(
        self,
        *,
        platform_key: str,
        display_name: str,
        host_type: str,
        description: str,
        owner_user_id: int,
        assigned_by: int | None = None,
    ) -> dict[str, Any]:
        now = utcnow_iso()
        host_secret = secrets.token_urlsafe(32)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO platforms(
                    platform_key, display_name, host_type, description,
                    owner_user_id, host_secret, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (platform_key, display_name, host_type, description, owner_user_id, host_secret, now, now),
            )
            platform = conn.execute("SELECT * FROM platforms WHERE platform_key = ?", (platform_key,)).fetchone()
            if platform is None:
                raise RuntimeError("Failed to create platform")
            conn.execute(
                """
                INSERT OR IGNORE INTO platform_admins(
                    platform_id, user_id, assigned_by, is_primary, created_at, updated_at
                ) VALUES (?, ?, ?, 1, ?, ?)
                """,
                (
                    platform["platform_id"],
                    owner_user_id,
                    assigned_by if assigned_by is not None else owner_user_id,
                    now,
                    now,
                ),
            )
        return dict(platform)

    def update_platform_basics(
        self,
        *,
        platform_key: str,
        display_name: str,
        host_type: str,
        description: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE platforms
                SET display_name = ?, host_type = ?, description = ?, updated_at = ?
                WHERE platform_key = ?
                """,
                (display_name, host_type, description, utcnow_iso(), platform_key),
            )

    def update_platform_owner(self, *, platform_id: int, owner_user_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE platforms SET owner_user_id = ?, updated_at = ? WHERE platform_id = ?",
                (owner_user_id, utcnow_iso(), platform_id),
            )
            conn.execute("UPDATE platform_admins SET is_primary = 0, updated_at = ? WHERE platform_id = ?", (utcnow_iso(), platform_id))
            conn.execute(
                """
                INSERT INTO platform_admins(platform_id, user_id, assigned_by, is_primary, created_at, updated_at)
                VALUES (?, ?, NULL, 1, ?, ?)
                ON CONFLICT(platform_id, user_id) DO UPDATE SET
                    is_primary = 1,
                    updated_at = excluded.updated_at
                """,
                (platform_id, owner_user_id, utcnow_iso(), utcnow_iso()),
            )

    def add_platform_admin(
        self,
        *,
        platform_id: int,
        user_id: int,
        assigned_by: int | None = None,
        is_primary: bool = False,
    ) -> None:
        now = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO platform_admins(platform_id, user_id, assigned_by, is_primary, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform_id, user_id) DO UPDATE SET
                    assigned_by = excluded.assigned_by,
                    is_primary = CASE WHEN excluded.is_primary = 1 THEN 1 ELSE platform_admins.is_primary END,
                    updated_at = excluded.updated_at
                """,
                (platform_id, user_id, assigned_by, 1 if is_primary else 0, now, now),
            )

    def remove_platform_admin(self, *, platform_id: int, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM platform_admins WHERE platform_id = ? AND user_id = ?",
                (platform_id, user_id),
            )

    def list_platform_admins(self, platform_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    pa.platform_id,
                    pa.user_id,
                    pa.assigned_by,
                    pa.is_primary,
                    pa.created_at,
                    pa.updated_at,
                    u.full_name,
                    u.email,
                    u.role
                FROM platform_admins pa
                JOIN users u ON u.user_id = pa.user_id
                WHERE pa.platform_id = ?
                ORDER BY pa.is_primary DESC, pa.created_at ASC, pa.user_id ASC
                """,
                (platform_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_managed_platform_ids(self, user_id: int) -> list[int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT platform_id FROM platform_admins WHERE user_id = ? ORDER BY platform_id ASC",
                (user_id,),
            ).fetchall()
        return [int(row["platform_id"]) for row in rows]

    def get_platform_by_key(self, platform_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM platforms WHERE platform_key = ?", (platform_key,)).fetchone()
        return dict(row) if row else None

    def get_platform_by_id(self, platform_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM platforms WHERE platform_id = ?", (platform_id,)).fetchone()
        return dict(row) if row else None

    def get_platform_by_secret(self, host_secret: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM platforms WHERE host_secret = ?", (host_secret,)).fetchone()
        return dict(row) if row else None

    def list_platforms(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM platforms ORDER BY platform_id DESC").fetchall()
        return [dict(row) for row in rows]

    def is_platform_admin(self, *, platform_id: int, user_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM platform_admins WHERE platform_id = ? AND user_id = ?",
                (platform_id, user_id),
            ).fetchone()
        return row is not None

    def create_platform_registration_request(
        self,
        *,
        applicant_user_id: int,
        platform_key: str,
        display_name: str,
        description: str,
        justification: str,
    ) -> dict[str, Any]:
        now = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO platform_registration_requests(
                    applicant_user_id, platform_key, display_name, description, justification,
                    status, review_comment, reviewed_by, reviewed_at, approved_platform_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', '', NULL, NULL, NULL, ?, ?)
                """,
                (applicant_user_id, platform_key, display_name, description, justification, now, now),
            )
            row = conn.execute(
                "SELECT * FROM platform_registration_requests WHERE request_id = last_insert_rowid()"
            ).fetchone()
        return self._inflate_platform_request(dict(row)) if row else {}

    def list_platform_registration_requests(self, *, applicant_user_id: int | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if applicant_user_id is None:
                rows = conn.execute(
                    "SELECT * FROM platform_registration_requests ORDER BY created_at DESC, request_id DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM platform_registration_requests
                    WHERE applicant_user_id = ?
                    ORDER BY created_at DESC, request_id DESC
                    """,
                    (applicant_user_id,),
                ).fetchall()
        return [self._inflate_platform_request(dict(row)) for row in rows]

    def get_platform_registration_request(self, request_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM platform_registration_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return self._inflate_platform_request(dict(row)) if row else None

    def update_platform_registration_request_status(
        self,
        *,
        request_id: int,
        status: str,
        reviewed_by: int,
        review_comment: str,
        approved_platform_id: int | None = None,
    ) -> dict[str, Any] | None:
        now = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE platform_registration_requests
                SET status = ?, review_comment = ?, reviewed_by = ?, reviewed_at = ?, approved_platform_id = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (status, review_comment, reviewed_by, now, approved_platform_id, now, request_id),
            )
            row = conn.execute(
                "SELECT * FROM platform_registration_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return self._inflate_platform_request(dict(row)) if row else None

    def get_platform_llm_config(self, platform_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM platform_llm_configs WHERE platform_id = ?", (platform_id,)).fetchone()
        return self._row_to_llm_config(row)

    def get_platform_prompt_config(self, platform_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM platform_prompt_configs WHERE platform_id = ?", (platform_id,)).fetchone()
        return self._row_to_prompt_config(row)

    def upsert_platform_prompt_config(
        self,
        *,
        platform_id: int,
        enabled: bool,
        system_prompt: str,
    ) -> dict[str, Any]:
        now = utcnow_iso()
        existing = self.get_platform_prompt_config(platform_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO platform_prompt_configs(
                    platform_id, enabled, system_prompt, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(platform_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    system_prompt = excluded.system_prompt,
                    updated_at = excluded.updated_at
                """,
                (
                    platform_id,
                    1 if enabled else 0,
                    system_prompt,
                    existing["created_at"] if existing else now,
                    now,
                ),
            )
        row = self.get_platform_prompt_config(platform_id)
        if row is None:
            raise RuntimeError("Failed to save platform prompt config")
        return row

    def delete_platform_prompt_config(self, platform_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM platform_prompt_configs WHERE platform_id = ?", (platform_id,))

    def upsert_platform_llm_config(
        self,
        *,
        platform_id: int,
        enabled: bool,
        provider_kind: str,
        api_format: str,
        base_url: str,
        model: str,
        api_key: str | None,
        extra_headers: dict[str, Any],
        extra_body: dict[str, Any],
        network: dict[str, Any],
    ) -> dict[str, Any]:
        now = utcnow_iso()
        existing = self.get_platform_llm_config(platform_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO platform_llm_configs(
                    platform_id, enabled, provider_kind, api_format, base_url, model, api_key,
                    extra_headers_json, extra_body_json, network_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    provider_kind = excluded.provider_kind,
                    api_format = excluded.api_format,
                    base_url = excluded.base_url,
                    model = excluded.model,
                    api_key = excluded.api_key,
                    extra_headers_json = excluded.extra_headers_json,
                    extra_body_json = excluded.extra_body_json,
                    network_json = excluded.network_json,
                    updated_at = excluded.updated_at
                """,
                (
                    platform_id,
                    1 if enabled else 0,
                    provider_kind,
                    api_format,
                    base_url,
                    model,
                    api_key,
                    json.dumps(extra_headers, ensure_ascii=False),
                    json.dumps(extra_body, ensure_ascii=False),
                    json.dumps(network, ensure_ascii=False),
                    existing["created_at"] if existing else now,
                    now,
                ),
            )
        row = self.get_platform_llm_config(platform_id)
        if row is None:
            raise RuntimeError("Failed to save platform LLM config")
        return row

    def delete_platform_llm_config(self, platform_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM platform_llm_configs WHERE platform_id = ?", (platform_id,))

    def get_user_llm_config(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM user_llm_configs WHERE user_id = ?", (user_id,)).fetchone()
        return self._row_to_llm_config(row)

    def upsert_user_llm_config(
        self,
        *,
        user_id: int,
        enabled: bool,
        provider_kind: str,
        api_format: str,
        base_url: str,
        model: str,
        api_key: str | None,
        extra_headers: dict[str, Any],
        extra_body: dict[str, Any],
        network: dict[str, Any],
    ) -> dict[str, Any]:
        now = utcnow_iso()
        existing = self.get_user_llm_config(user_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_llm_configs(
                    user_id, enabled, provider_kind, api_format, base_url, model, api_key,
                    extra_headers_json, extra_body_json, network_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    provider_kind = excluded.provider_kind,
                    api_format = excluded.api_format,
                    base_url = excluded.base_url,
                    model = excluded.model,
                    api_key = excluded.api_key,
                    extra_headers_json = excluded.extra_headers_json,
                    extra_body_json = excluded.extra_body_json,
                    network_json = excluded.network_json,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    1 if enabled else 0,
                    provider_kind,
                    api_format,
                    base_url,
                    model,
                    api_key,
                    json.dumps(extra_headers, ensure_ascii=False),
                    json.dumps(extra_body, ensure_ascii=False),
                    json.dumps(network, ensure_ascii=False),
                    existing["created_at"] if existing else now,
                    now,
                ),
            )
        row = self.get_user_llm_config(user_id)
        if row is None:
            raise RuntimeError("Failed to save user LLM config")
        return row

    def delete_user_llm_config(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM user_llm_configs WHERE user_id = ?", (user_id,))

    def create_conversation(
        self,
        *,
        session_id: str,
        title: str,
        host_name: str,
        platform_id: int | None = None,
        owner_user_id: int | None = None,
        external_user_id: str | None = None,
        external_org_id: str | None = None,
        conversation_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        conversation_id = f"conv_{uuid.uuid4().hex}"
        now = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations(
                    conversation_id, session_id, platform_id, owner_user_id, external_user_id,
                    external_org_id, conversation_key, title, host_name, created_at,
                    updated_at, last_message_at, message_count, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    conversation_id,
                    session_id,
                    platform_id,
                    owner_user_id,
                    external_user_id,
                    external_org_id,
                    conversation_key,
                    title,
                    host_name,
                    now,
                    now,
                    now,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            row = conn.execute("SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)).fetchone()
        return dict(row) if row else {}

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)).fetchone()
        return dict(row) if row else None

    def get_conversation_by_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE session_id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def find_host_conversation(
        self,
        *,
        platform_id: int,
        external_user_id: str,
        conversation_key: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any] | None:
        if conversation_id:
            row = self.get_conversation(conversation_id)
            if row and row.get("platform_id") == platform_id and row.get("external_user_id") == external_user_id:
                return row
            return None
        with self._connect() as conn:
            if conversation_key:
                row = conn.execute(
                    """
                    SELECT * FROM conversations
                    WHERE platform_id = ? AND external_user_id = ? AND conversation_key = ?
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (platform_id, external_user_id, conversation_key),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM conversations
                    WHERE platform_id = ? AND external_user_id = ?
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (platform_id, external_user_id),
                ).fetchone()
        return dict(row) if row else None

    def list_conversations_for_user(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE owner_user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_conversations_for_admin(self, user_id: int) -> list[dict[str, Any]]:
        return self.list_conversations_for_user(user_id)

    def list_conversations_for_host_user(self, *, platform_id: int, external_user_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM conversations
                WHERE platform_id = ? AND external_user_id = ?
                ORDER BY updated_at DESC
                """,
                (platform_id, external_user_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_all_conversations(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM conversations ORDER BY updated_at DESC, created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def list_conversations_for_platform_ids(self, platform_ids: list[int]) -> list[dict[str, Any]]:
        normalized = sorted({int(item) for item in platform_ids})
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM conversations
                WHERE platform_id IN ({placeholders})
                ORDER BY updated_at DESC, created_at DESC
                """,
                tuple(normalized),
            ).fetchall()
        return [dict(row) for row in rows]

    def touch_conversation(self, session_id: str, *, title: str | None = None, message_count: int | None = None) -> None:
        current = self.get_conversation_by_session(session_id)
        if current is None:
            return
        now = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE conversations
                SET title = ?, updated_at = ?, last_message_at = ?, message_count = ?
                WHERE session_id = ?
                """,
                (
                    title or current["title"],
                    now,
                    now,
                    message_count if message_count is not None else current["message_count"],
                    session_id,
                ),
            )

    def delete_conversation(self, session_id: str) -> bool:
        with self._connect() as conn:
            result = conn.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
            return result.rowcount > 0

    def update_conversation_title(self, session_id: str, title: str) -> bool:
        now = utcnow_iso()
        with self._connect() as conn:
            result = conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE session_id = ?",
                (title, now, session_id),
            )
            return result.rowcount > 0

    def get_session_runtime(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    sr.*,
                    c.title AS conversation_title,
                    c.host_name AS conversation_host_name,
                    p.display_name AS platform_display_name,
                    u.full_name AS owner_user_name
                FROM session_runtimes sr
                LEFT JOIN conversations c ON c.session_id = sr.session_id
                LEFT JOIN platforms p ON p.platform_id = sr.platform_id
                LEFT JOIN users u ON u.user_id = sr.owner_user_id
                WHERE sr.session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return self._row_to_session_runtime(row)

    def list_session_runtimes(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    sr.*,
                    c.title AS conversation_title,
                    c.host_name AS conversation_host_name,
                    p.display_name AS platform_display_name,
                    u.full_name AS owner_user_name
                FROM session_runtimes sr
                LEFT JOIN conversations c ON c.session_id = sr.session_id
                LEFT JOIN platforms p ON p.platform_id = sr.platform_id
                LEFT JOIN users u ON u.user_id = sr.owner_user_id
                ORDER BY COALESCE(sr.last_used_at, sr.updated_at) DESC, sr.session_id ASC
                """
            ).fetchall()
        return [self._row_to_session_runtime(row) for row in rows if row is not None]

    def upsert_session_runtime(
        self,
        *,
        session_id: str,
        conversation_id: str | None,
        platform_id: int | None,
        owner_user_id: int | None,
        external_user_id: str | None,
        container_name: str | None,
        container_id: str | None,
        image: str,
        status: str,
        generation: int,
        network_mode: str,
        created_at: str,
        updated_at: str,
        last_started_at: str | None,
        last_used_at: str | None,
        idle_expires_at: str | None,
        max_expires_at: str | None,
        destroyed_at: str | None,
        destroy_reason: str | None,
        restart_count: int,
        workspace_root: str,
        home_root: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO session_runtimes(
                    session_id, conversation_id, platform_id, owner_user_id, external_user_id,
                    container_name, container_id, image, status, generation, network_mode,
                    created_at, updated_at, last_started_at, last_used_at,
                    idle_expires_at, max_expires_at, destroyed_at, destroy_reason,
                    restart_count, workspace_root, home_root, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    conversation_id = excluded.conversation_id,
                    platform_id = excluded.platform_id,
                    owner_user_id = excluded.owner_user_id,
                    external_user_id = excluded.external_user_id,
                    container_name = excluded.container_name,
                    container_id = excluded.container_id,
                    image = excluded.image,
                    status = excluded.status,
                    generation = excluded.generation,
                    network_mode = excluded.network_mode,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    last_started_at = excluded.last_started_at,
                    last_used_at = excluded.last_used_at,
                    idle_expires_at = excluded.idle_expires_at,
                    max_expires_at = excluded.max_expires_at,
                    destroyed_at = excluded.destroyed_at,
                    destroy_reason = excluded.destroy_reason,
                    restart_count = excluded.restart_count,
                    workspace_root = excluded.workspace_root,
                    home_root = excluded.home_root,
                    metadata_json = excluded.metadata_json
                """,
                (
                    session_id,
                    conversation_id,
                    platform_id,
                    owner_user_id,
                    external_user_id,
                    container_name,
                    container_id,
                    image,
                    status,
                    generation,
                    network_mode,
                    created_at,
                    updated_at,
                    last_started_at,
                    last_used_at,
                    idle_expires_at,
                    max_expires_at,
                    destroyed_at,
                    destroy_reason,
                    restart_count,
                    workspace_root,
                    home_root,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
        runtime = self.get_session_runtime(session_id)
        if runtime is None:
            raise RuntimeError("Failed to persist session runtime")
        return runtime

    def delete_session_runtime(self, session_id: str) -> bool:
        with self._connect() as conn:
            result = conn.execute("DELETE FROM session_runtimes WHERE session_id = ?", (session_id,))
            return result.rowcount > 0

    def _inflate_platform_request(self, row: dict[str, Any]) -> dict[str, Any]:
        applicant = self.get_user_by_id(int(row["applicant_user_id"]))
        reviewer = self.get_user_by_id(int(row["reviewed_by"])) if row.get("reviewed_by") else None
        return {
            **row,
            "applicant_name": applicant.full_name if applicant else "Unknown User",
            "applicant_email": applicant.email if applicant else None,
            "reviewed_by_name": reviewer.full_name if reviewer else None,
        }

    def _row_to_user(self, row: sqlite3.Row | None) -> StoreUser | None:
        if row is None:
            return None
        return StoreUser(
            user_id=row["user_id"],
            account_id=row["account_id"],
            username=row["username"],
            full_name=row["full_name"],
            email=row["email"],
            role=row["role"],
            provider=row["provider"],
            password_hash=row["password_hash"],
            is_active=bool(row["is_active"]),
            provider_user_id=row["provider_user_id"],
            last_login_at=row["last_login_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_llm_config(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            **dict(row),
            "enabled": bool(row["enabled"]),
            "has_api_key": bool(row["api_key"]),
            "extra_headers": json.loads(row["extra_headers_json"] or "{}"),
            "extra_body": json.loads(row["extra_body_json"] or "{}"),
            "network": json.loads(row["network_json"] or "{}"),
        }

    def _row_to_prompt_config(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            **dict(row),
            "enabled": bool(row["enabled"]),
        }

    def _row_to_session_runtime(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        payload = dict(row)
        payload["generation"] = int(payload.get("generation") or 0)
        payload["restart_count"] = int(payload.get("restart_count") or 0)
        payload["metadata"] = json.loads(payload.pop("metadata_json", "{}") or "{}")
        return payload


store_service = StoreService()
