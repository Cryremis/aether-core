# backend/app/services/store.py
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
    """持久化用户对象。"""

    user_id: int
    account_id: str
    username: str | None
    full_name: str
    email: str | None
    role: str
    provider: str
    password_hash: str | None
    is_active: bool


class StoreService:
    """基于 SQLite 的轻量持久化服务。"""

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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS admin_whitelist (
                    whitelist_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    provider_user_id TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    email TEXT,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(provider, provider_user_id)
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
                    created_at TEXT NOT NULL,
                    UNIQUE(platform_id, user_id)
                );

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
                    host_type TEXT NOT NULL,
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
                """
            )
        self._seed_default_users()
        self._seed_standalone_platform()

    def _seed_default_users(self) -> None:
        self.ensure_local_user(
            username=settings.auth_system_admin_username,
            password=settings.auth_system_admin_password,
            full_name="系统管理员",
            role="system_admin",
        )
        self.ensure_local_user(
            username=settings.auth_debug_username,
            password=settings.auth_debug_password,
            full_name="Debug 用户",
            role="debug",
        )

    def _seed_standalone_platform(self) -> None:
        admin = self.get_user_by_username(settings.auth_system_admin_username)
        if not admin:
            return
        if self.get_platform_by_key("standalone") is not None:
            self.update_platform_basics(
                platform_key="standalone",
                display_name="AetherCore",
                host_type="standalone",
                description="AetherCore 内置默认平台，用于直接登录、debug 调试与平台能力自验证。",
            )
            return
        self.create_platform(
            platform_key="standalone",
            display_name="AetherCore",
            host_type="standalone",
            description="AetherCore 内置默认平台，用于直接登录、debug 调试与平台能力自验证。",
            owner_user_id=admin.user_id,
        )

    def ensure_local_user(self, *, username: str, password: str, full_name: str, role: str) -> StoreUser:
        existing = self.get_user_by_username(username)
        if existing:
            return existing
        now = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    account_id, username, full_name, email, role, provider,
                    provider_user_id, password_hash, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    f"acct_{uuid.uuid4().hex[:12]}",
                    username,
                    full_name,
                    None,
                    role,
                    "password",
                    username,
                    password_service.hash_password(password),
                    now,
                    now,
                ),
            )
        user = self.get_user_by_username(username)
        if user is None:
            raise RuntimeError("默认用户初始化失败")
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

    def create_user_from_whitelist(
        self,
        *,
        provider: str,
        provider_user_id: str,
        full_name: str,
        email: str | None,
        role: str,
    ) -> StoreUser:
        now = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    account_id, username, full_name, email, role, provider,
                    provider_user_id, password_hash, is_active, created_at, updated_at
                ) VALUES (?, NULL, ?, ?, ?, ?, ?, NULL, 1, ?, ?)
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
                ),
            )
        user = self.get_user_by_provider(provider, provider_user_id)
        if user is None:
            raise RuntimeError("白名单用户创建失败")
        return user

    def upsert_admin_whitelist(
        self,
        *,
        provider: str,
        provider_user_id: str,
        full_name: str,
        email: str | None,
        role: str,
    ) -> dict[str, Any]:
        now = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO admin_whitelist(provider, provider_user_id, full_name, email, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, provider_user_id) DO UPDATE SET
                    full_name = excluded.full_name,
                    email = excluded.email,
                    role = excluded.role
                """,
                (provider, provider_user_id, full_name, email, role, now),
            )
            row = conn.execute(
                "SELECT * FROM admin_whitelist WHERE provider = ? AND provider_user_id = ?",
                (provider, provider_user_id),
            ).fetchone()
        return dict(row) if row else {}

    def list_admin_whitelist(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM admin_whitelist ORDER BY whitelist_id DESC").fetchall()
        return [dict(row) for row in rows]

    def get_whitelist_entry(self, provider: str, provider_user_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM admin_whitelist WHERE provider = ? AND provider_user_id = ?",
                (provider, provider_user_id),
            ).fetchone()
        return dict(row) if row else None

    def create_platform(
        self,
        *,
        platform_key: str,
        display_name: str,
        host_type: str,
        description: str,
        owner_user_id: int,
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
            conn.execute(
                "INSERT OR IGNORE INTO platform_admins(platform_id, user_id, created_at) VALUES (?, ?, ?)",
                (platform["platform_id"], owner_user_id, now),
            )
        return dict(platform) if platform else {}

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

    def add_platform_admin(self, *, platform_id: int, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO platform_admins(platform_id, user_id, created_at) VALUES (?, ?, ?)",
                (platform_id, user_id, utcnow_iso()),
            )

    def get_platform_by_key(self, platform_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM platforms WHERE platform_key = ?", (platform_key,)).fetchone()
        return dict(row) if row else None

    def get_platform_by_secret(self, host_secret: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM platforms WHERE host_secret = ?", (host_secret,)).fetchone()
        return dict(row) if row else None

    def list_platforms(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM platforms ORDER BY platform_id DESC").fetchall()
        return [dict(row) for row in rows]

    def get_platform_llm_config(self, platform_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM platform_llm_configs WHERE platform_id = ?", (platform_id,)).fetchone()
        return self._row_to_llm_config(row)

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
    ) -> dict[str, Any]:
        now = utcnow_iso()
        existing = self.get_platform_llm_config(platform_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO platform_llm_configs(
                    platform_id, enabled, provider_kind, api_format, base_url, model, api_key,
                    extra_headers_json, extra_body_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    provider_kind = excluded.provider_kind,
                    api_format = excluded.api_format,
                    base_url = excluded.base_url,
                    model = excluded.model,
                    api_key = excluded.api_key,
                    extra_headers_json = excluded.extra_headers_json,
                    extra_body_json = excluded.extra_body_json,
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
                    existing["created_at"] if existing else now,
                    now,
                ),
            )
        row = self.get_platform_llm_config(platform_id)
        if row is None:
            raise RuntimeError("平台 LLM 配置保存失败")
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
    ) -> dict[str, Any]:
        now = utcnow_iso()
        existing = self.get_user_llm_config(user_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_llm_configs(
                    user_id, enabled, provider_kind, api_format, base_url, model, api_key,
                    extra_headers_json, extra_body_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    provider_kind = excluded.provider_kind,
                    api_format = excluded.api_format,
                    base_url = excluded.base_url,
                    model = excluded.model,
                    api_key = excluded.api_key,
                    extra_headers_json = excluded.extra_headers_json,
                    extra_body_json = excluded.extra_body_json,
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
                    existing["created_at"] if existing else now,
                    now,
                ),
            )
        row = self.get_user_llm_config(user_id)
        if row is None:
            raise RuntimeError("用户 LLM 配置保存失败")
        return row

    def delete_user_llm_config(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM user_llm_configs WHERE user_id = ?", (user_id,))

    def is_platform_admin(self, *, platform_id: int, user_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM platform_admins WHERE platform_id = ? AND user_id = ?",
                (platform_id, user_id),
            ).fetchone()
        return row is not None

    def create_conversation(
        self,
        *,
        session_id: str,
        title: str,
        host_name: str,
        host_type: str,
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
                    external_org_id, conversation_key, title, host_name, host_type, created_at,
                    updated_at, last_message_at, message_count, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
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
                    host_type,
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

    def list_conversations_for_admin(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE owner_user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

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
        }


store_service = StoreService()
