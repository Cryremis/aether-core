from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
        # WAL 模式:读写不互斥。默认 journal 模式下 agent 持续写库会阻塞所有读请求
        # (含 admin 审计查询),是生产环境接口偶发卡死的根因。WAL 为 DB 级持久属性。
        with self._lock:
            wal_conn = sqlite3.connect(self._db_path)
            try:
                wal_conn.execute("PRAGMA journal_mode=WAL")
                wal_conn.execute("PRAGMA synchronous=NORMAL")
            finally:
                wal_conn.close()

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

                CREATE TABLE IF NOT EXISTS embed_user_llm_configs (
                    platform_id INTEGER NOT NULL,
                    external_user_id TEXT NOT NULL,
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
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (platform_id, external_user_id)
                );

                CREATE TABLE IF NOT EXISTS agent_workspaces (
                    workspace_id TEXT PRIMARY KEY,
                    owner_session_id TEXT NOT NULL UNIQUE,
                    owner_conversation_id TEXT,
                    root_relative_path TEXT NOT NULL UNIQUE,
                    baseline_root TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workspace_members (
                    session_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    conversation_id TEXT,
                    role TEXT NOT NULL,
                    parent_session_id TEXT,
                    joined_at TEXT NOT NULL,
                    last_active_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_workspace_members_workspace
                ON workspace_members(workspace_id, joined_at ASC);

                CREATE TABLE IF NOT EXISTS workspace_migration_journal (
                    session_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    source_root TEXT NOT NULL,
                    destination_root TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_text TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL UNIQUE,
                    workspace_id TEXT,
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
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    visibility TEXT NOT NULL DEFAULT 'normal',
                    archived_at TEXT,
                    pinned_at TEXT,
                    deleted_at TEXT,
                    revision INTEGER NOT NULL DEFAULT 0,
                    last_run_id TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_conversations_owner
                ON conversations(owner_user_id, updated_at DESC);

                CREATE INDEX IF NOT EXISTS idx_conversations_platform_user
                ON conversations(platform_id, external_user_id, updated_at DESC);

                -- 审计趋势按天聚合与平台过滤的支撑索引
                CREATE INDEX IF NOT EXISTS idx_conversations_created_at
                ON conversations(created_at);

                CREATE INDEX IF NOT EXISTS idx_conversations_platform_created
                ON conversations(platform_id, created_at);

                CREATE INDEX IF NOT EXISTS idx_users_created_at
                ON users(created_at);

                CREATE TABLE IF NOT EXISTS workspace_runtimes (
                    workspace_id TEXT PRIMARY KEY,
                    owner_session_id TEXT NOT NULL,
                    owner_conversation_id TEXT,
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

                CREATE INDEX IF NOT EXISTS idx_workspace_runtimes_status
                ON workspace_runtimes(status, last_used_at DESC);

                CREATE TABLE IF NOT EXISTS extension_entries (
                    entry_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    submitter_user_id INTEGER NOT NULL,
                    visibility TEXT NOT NULL DEFAULT 'public',
                    current_version TEXT NOT NULL,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(kind, name)
                );

                CREATE TABLE IF NOT EXISTS extension_versions (
                    entry_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    artifact_path TEXT NOT NULL,
                    manifest_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (entry_id, version)
                );

                CREATE TABLE IF NOT EXISTS capability_secrets (
                    secret_id TEXT PRIMARY KEY,
                    ciphertext TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    conversation_id TEXT,
                    parent_run_id TEXT,
                    root_run_id TEXT,
                    agent_kind TEXT NOT NULL DEFAULT 'main',
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT,
                    error_json TEXT,
                    event_cursor INTEGER NOT NULL DEFAULT 0,
                    idempotency_key TEXT,
                    trace_id TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_agent_runs_session
                ON agent_runs(session_id, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_agent_runs_parent
                ON agent_runs(parent_run_id, created_at DESC);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_active_session
                ON agent_runs(session_id)
                WHERE status IN ('queued', 'running', 'waiting_input');

                CREATE TABLE IF NOT EXISTS run_events (
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    visibility TEXT NOT NULL DEFAULT 'visible',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, seq),
                    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS subagent_runs (
                    child_run_id TEXT PRIMARY KEY,
                    workspace_id TEXT,
                    parent_run_id TEXT NOT NULL,
                    parent_session_id TEXT NOT NULL,
                    child_session_id TEXT NOT NULL UNIQUE,
                    latest_run_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    task TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_text TEXT,
                    error_text TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_subagent_runs_parent_session
                ON subagent_runs(parent_session_id, created_at DESC);
                """
            )
            self._ensure_column(conn, "conversations", "visibility", "TEXT NOT NULL DEFAULT 'normal'")
            self._ensure_column(conn, "conversations", "workspace_id", "TEXT")
            self._ensure_column(conn, "conversations", "archived_at", "TEXT")
            self._ensure_column(conn, "conversations", "pinned_at", "TEXT")
            self._ensure_column(conn, "conversations", "deleted_at", "TEXT")
            self._ensure_column(conn, "conversations", "revision", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "conversations", "last_run_id", "TEXT")
            self._ensure_column(conn, "agent_runs", "idempotency_key", "TEXT")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_conversation_idempotency
                ON agent_runs(conversation_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL
                """
            )
            self._ensure_column(conn, "subagent_runs", "latest_run_id", "TEXT")
            self._ensure_column(conn, "subagent_runs", "workspace_id", "TEXT")
            self._ensure_column(conn, "users", "last_login_at", "TEXT")
            self._ensure_column(conn, "platform_admins", "assigned_by", "INTEGER")
            self._ensure_column(conn, "platform_admins", "is_primary", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "platform_admins", "updated_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "platform_llm_configs", "network_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "user_llm_configs", "network_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "embed_user_llm_configs", "network_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "platform_llm_configs", "sampling_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "user_llm_configs", "sampling_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "embed_user_llm_configs", "sampling_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "platforms", "sandbox_image", "TEXT")
            self._ensure_column(conn, "platforms", "sandbox_image_updated_at", "TEXT")
            self._ensure_column(conn, "platforms", "sandbox_proxy_enabled", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "platforms", "sandbox_proxy_http", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "platforms", "sandbox_proxy_https", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "platforms", "sandbox_proxy_all", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "platforms", "sandbox_proxy_no_proxy", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "platforms", "sandbox_proxy_inherit_host_proxy", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(conn, "platforms", "sandbox_proxy_updated_at", "TEXT")
            conn.execute("DROP TABLE IF EXISTS admin_whitelist")
            self._migrate_workspaces(conn)
            conn.execute("DROP TABLE IF EXISTS session_runtimes")
            self._migrate_roles(conn)
            self._backfill_platform_admin_metadata(conn)
        # 文件迁移放在 DB schema 迁移之后、服务可用之前；失败必须阻断启动，避免双轨读取。
        from app.services.workspace_service import workspace_service

        workspace_service.migrate_legacy_layout()
        self._seed_default_users()
        self._seed_standalone_platform()

    def _ensure_column(self, conn: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
        columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
        if column_name in columns:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")

    def _migrate_workspaces(self, conn: sqlite3.Connection) -> None:
        """一次性把会话归属迁移为共享 Workspace 归属。"""
        rows = conn.execute("SELECT * FROM conversations").fetchall()
        conversations = {str(row["session_id"]): dict(row) for row in rows}
        workspace_by_session = {
            session_id: str(row["workspace_id"])
            for session_id, row in conversations.items()
            if row.get("workspace_id")
        }

        def ensure_workspace(session_id: str, baseline_root: str = "") -> str:
            existing = workspace_by_session.get(session_id)
            if existing:
                return existing
            workspace_id = f"ws_{uuid.uuid4().hex}"
            now = utcnow_iso()
            conn.execute(
                """
                INSERT INTO agent_workspaces(
                    workspace_id, owner_session_id, owner_conversation_id, root_relative_path,
                    baseline_root, status, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', 1, ?, ?)
                """,
                (
                    workspace_id,
                    session_id,
                    conversations[session_id].get("conversation_id"),
                    f"workspaces/{workspace_id}",
                    baseline_root,
                    now,
                    now,
                ),
            )
            workspace_by_session[session_id] = workspace_id
            return workspace_id

        def attach_member(
            session_id: str,
            workspace_id: str,
            role: str,
            parent_session_id: str | None,
        ) -> None:
            now = utcnow_iso()
            conn.execute(
                """
                INSERT INTO workspace_members(
                    session_id, workspace_id, conversation_id, role, parent_session_id,
                    joined_at, last_active_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    workspace_id = excluded.workspace_id,
                    conversation_id = excluded.conversation_id,
                    role = excluded.role,
                    parent_session_id = excluded.parent_session_id,
                    last_active_at = excluded.last_active_at
                """,
                (
                    session_id,
                    workspace_id,
                    conversations[session_id].get("conversation_id"),
                    role,
                    parent_session_id,
                    now,
                    now,
                ),
            )

        for row in rows:
            session_id = str(row["session_id"])
            metadata = json.loads(row["metadata_json"] or "{}")
            if not bool(metadata.get("subagent")):
                workspace_id = ensure_workspace(session_id, self._legacy_session_baseline(session_id))
                conn.execute(
                    "UPDATE conversations SET workspace_id = ? WHERE session_id = ?",
                    (workspace_id, session_id),
                )
                conversations[session_id]["workspace_id"] = workspace_id
                attach_member(session_id, workspace_id, "owner", None)

        for row in rows:
            session_id = str(row["session_id"])
            metadata = json.loads(row["metadata_json"] or "{}")
            if not bool(metadata.get("subagent")):
                continue
            parent_session_id = str(metadata.get("parent_session_id") or "")
            parent_workspace_id = workspace_by_session.get(parent_session_id)
            if parent_workspace_id is None:
                workspace_id = ensure_workspace(session_id, self._legacy_session_baseline(session_id))
                role = "orphan_subagent"
            else:
                workspace_id = parent_workspace_id
                role = "subagent"
            conn.execute(
                "UPDATE conversations SET workspace_id = ? WHERE session_id = ?",
                (workspace_id, session_id),
            )
            conversations[session_id]["workspace_id"] = workspace_id
            attach_member(
                session_id,
                workspace_id,
                role,
                parent_session_id or None,
            )

        conn.execute(
            """
            UPDATE subagent_runs
            SET workspace_id = (
                SELECT c.workspace_id FROM conversations c
                WHERE c.session_id = subagent_runs.parent_session_id
            )
            WHERE workspace_id IS NULL OR workspace_id = ''
            """
        )
        self._migrate_workspace_runtimes(conn, workspace_by_session)

    def _legacy_session_baseline(self, session_id: str) -> str:
        metadata_path = settings.sessions_root / session_id / "sandbox" / "metadata" / "session.json"
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            return str(payload.get("baseline_root") or "")
        except (OSError, json.JSONDecodeError):
            return ""

    def _migrate_workspace_runtimes(
        self,
        conn: sqlite3.Connection,
        workspace_by_session: dict[str, str],
    ) -> None:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'session_runtimes'"
        ).fetchone()
        if not table_exists:
            return
        rows = conn.execute("SELECT * FROM session_runtimes").fetchall()
        best_by_workspace: dict[str, sqlite3.Row] = {}
        for row in rows:
            workspace_id = workspace_by_session.get(str(row["session_id"]))
            if workspace_id is None:
                continue
            current = best_by_workspace.get(workspace_id)
            if current is None or str(row["updated_at"] or "") > str(current["updated_at"] or ""):
                best_by_workspace[workspace_id] = row
        for workspace_id, row in best_by_workspace.items():
            conn.execute(
                """
                INSERT OR IGNORE INTO workspace_runtimes(
                    workspace_id, owner_session_id, owner_conversation_id, platform_id,
                    owner_user_id, external_user_id, container_name, container_id, image,
                    status, generation, network_mode, created_at, updated_at,
                    last_started_at, last_used_at, idle_expires_at, max_expires_at,
                    destroyed_at, destroy_reason, restart_count, workspace_root, home_root,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    str(row["session_id"]),
                    row["conversation_id"],
                    row["platform_id"],
                    row["owner_user_id"],
                    row["external_user_id"],
                    row["container_name"],
                    row["container_id"],
                    row["image"],
                    row["status"],
                    row["generation"],
                    row["network_mode"],
                    row["created_at"],
                    row["updated_at"],
                    row["last_started_at"],
                    row["last_used_at"],
                    row["idle_expires_at"],
                    row["max_expires_at"],
                    row["destroyed_at"],
                    row["destroy_reason"],
                    row["restart_count"],
                    row["workspace_root"],
                    row["home_root"],
                    row["metadata_json"],
                ),
            )

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

    def get_users_by_ids(self, user_ids: list[int]) -> dict[int, StoreUser]:
        normalized_ids = sorted({int(user_id) for user_id in user_ids if int(user_id) > 0})
        if not normalized_ids:
            return {}
        placeholders = ", ".join("?" for _ in normalized_ids)
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM users WHERE user_id IN ({placeholders})", tuple(normalized_ids)).fetchall()
        users: dict[int, StoreUser] = {}
        for row in rows:
            user = self._row_to_user(row)
            if user is not None:
                users[user.user_id] = user
        return users

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

    def list_platform_admins_for_platforms(self, platform_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        normalized_ids = sorted({int(platform_id) for platform_id in platform_ids if int(platform_id) > 0})
        if not normalized_ids:
            return {}
        placeholders = ", ".join("?" for _ in normalized_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
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
                WHERE pa.platform_id IN ({placeholders})
                ORDER BY pa.platform_id ASC, pa.is_primary DESC, pa.created_at ASC, pa.user_id ASC
                """,
                tuple(normalized_ids),
            ).fetchall()
        grouped: dict[int, list[dict[str, Any]]] = {platform_id: [] for platform_id in normalized_ids}
        for row in rows:
            grouped.setdefault(int(row["platform_id"]), []).append(dict(row))
        return grouped

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

    def update_platform_sandbox_image(self, *, platform_id: int, image: str | None) -> dict[str, Any] | None:
        normalized = image.strip() if image else None
        now = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE platforms
                SET sandbox_image = ?, sandbox_image_updated_at = ?, updated_at = ?
                WHERE platform_id = ?
                """,
                (normalized, now, now, platform_id),
            )
        return self.get_platform_by_id(platform_id)

    def update_platform_sandbox_proxy_config(
        self,
        *,
        platform_id: int,
        enabled: bool,
        http_proxy: str,
        https_proxy: str,
        all_proxy: str,
        no_proxy: str,
        inherit_host_proxy: bool,
    ) -> dict[str, Any] | None:
        now = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE platforms
                SET sandbox_proxy_enabled = ?,
                    sandbox_proxy_http = ?,
                    sandbox_proxy_https = ?,
                    sandbox_proxy_all = ?,
                    sandbox_proxy_no_proxy = ?,
                    sandbox_proxy_inherit_host_proxy = ?,
                    sandbox_proxy_updated_at = ?,
                    updated_at = ?
                WHERE platform_id = ?
                """,
                (
                    1 if enabled else 0,
                    http_proxy.strip(),
                    https_proxy.strip(),
                    all_proxy.strip(),
                    no_proxy.strip(),
                    1 if inherit_host_proxy else 0,
                    now,
                    now,
                    platform_id,
                ),
            )
        return self.get_platform_by_id(platform_id)

    def clear_platform_sandbox_proxy_config(self, *, platform_id: int) -> dict[str, Any] | None:
        now = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE platforms
                SET sandbox_proxy_enabled = 0,
                    sandbox_proxy_http = '',
                    sandbox_proxy_https = '',
                    sandbox_proxy_all = '',
                    sandbox_proxy_no_proxy = '',
                    sandbox_proxy_inherit_host_proxy = 1,
                    sandbox_proxy_updated_at = ?,
                    updated_at = ?
                WHERE platform_id = ?
                """,
                (now, now, platform_id),
            )
        return self.get_platform_by_id(platform_id)

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
        sampling: dict[str, Any],
    ) -> dict[str, Any]:
        now = utcnow_iso()
        existing = self.get_platform_llm_config(platform_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO platform_llm_configs(
                    platform_id, enabled, provider_kind, api_format, base_url, model, api_key,
                    extra_headers_json, extra_body_json, network_json, sampling_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    sampling_json = excluded.sampling_json,
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
                    json.dumps(sampling, ensure_ascii=False),
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

    def get_embed_user_llm_config(self, platform_id: int, external_user_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM embed_user_llm_configs WHERE platform_id = ? AND external_user_id = ?",
                (platform_id, external_user_id),
            ).fetchone()
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
        sampling: dict[str, Any],
    ) -> dict[str, Any]:
        now = utcnow_iso()
        existing = self.get_user_llm_config(user_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_llm_configs(
                    user_id, enabled, provider_kind, api_format, base_url, model, api_key,
                    extra_headers_json, extra_body_json, network_json, sampling_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    sampling_json = excluded.sampling_json,
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
                    json.dumps(sampling, ensure_ascii=False),
                    existing["created_at"] if existing else now,
                    now,
                ),
            )
        row = self.get_user_llm_config(user_id)
        if row is None:
            raise RuntimeError("Failed to save user LLM config")
        return row

    def upsert_embed_user_llm_config(
        self,
        *,
        platform_id: int,
        external_user_id: str,
        enabled: bool,
        provider_kind: str,
        api_format: str,
        base_url: str,
        model: str,
        api_key: str | None,
        extra_headers: dict[str, Any],
        extra_body: dict[str, Any],
        network: dict[str, Any],
        sampling: dict[str, Any],
    ) -> dict[str, Any]:
        now = utcnow_iso()
        existing = self.get_embed_user_llm_config(platform_id, external_user_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO embed_user_llm_configs(
                    platform_id, external_user_id, enabled, provider_kind, api_format, base_url, model, api_key,
                    extra_headers_json, extra_body_json, network_json, sampling_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform_id, external_user_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    provider_kind = excluded.provider_kind,
                    api_format = excluded.api_format,
                    base_url = excluded.base_url,
                    model = excluded.model,
                    api_key = excluded.api_key,
                    extra_headers_json = excluded.extra_headers_json,
                    extra_body_json = excluded.extra_body_json,
                    network_json = excluded.network_json,
                    sampling_json = excluded.sampling_json,
                    updated_at = excluded.updated_at
                """,
                (
                    platform_id,
                    external_user_id,
                    1 if enabled else 0,
                    provider_kind,
                    api_format,
                    base_url,
                    model,
                    api_key,
                    json.dumps(extra_headers, ensure_ascii=False),
                    json.dumps(extra_body, ensure_ascii=False),
                    json.dumps(network, ensure_ascii=False),
                    json.dumps(sampling, ensure_ascii=False),
                    existing["created_at"] if existing else now,
                    now,
                ),
            )
        row = self.get_embed_user_llm_config(platform_id, external_user_id)
        if row is None:
            raise RuntimeError("Failed to save embed user LLM config")
        return row

    def delete_user_llm_config(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM user_llm_configs WHERE user_id = ?", (user_id,))

    def delete_embed_user_llm_config(self, platform_id: int, external_user_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM embed_user_llm_configs WHERE platform_id = ? AND external_user_id = ?",
                (platform_id, external_user_id),
            )

    def create_conversation(
        self,
        *,
        session_id: str,
        workspace_id: str | None = None,
        title: str,
        host_name: str,
        platform_id: int | None = None,
        owner_user_id: int | None = None,
        external_user_id: str | None = None,
        external_org_id: str | None = None,
        conversation_key: str | None = None,
        visibility: str = "normal",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        conversation_id = f"conv_{uuid.uuid4().hex}"
        now = utcnow_iso()
        with self._connect() as conn:
            if workspace_id is None:
                member = conn.execute(
                    "SELECT workspace_id FROM workspace_members WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                workspace_id = str(member["workspace_id"]) if member else None
            conn.execute(
                """
                INSERT INTO conversations(
                    conversation_id, session_id, workspace_id, platform_id, owner_user_id, external_user_id,
                    external_org_id, conversation_key, title, host_name, created_at,
                    updated_at, last_message_at, message_count, metadata_json, visibility
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(session_id) DO NOTHING
                """,
                (
                    conversation_id,
                    session_id,
                    workspace_id,
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
                    visibility if visibility in {"normal", "hidden"} else "normal",
                ),
            )
            # 命中 session_id 冲突时 INSERT 被跳过,按 session_id 读回已有行,保证返回一致
            row = conn.execute(
                "SELECT * FROM conversations WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is not None:
                conn.execute(
                    """
                    UPDATE workspace_members
                    SET conversation_id = ?, last_active_at = ?
                    WHERE session_id = ?
                    """,
                    (row["conversation_id"], now, session_id),
                )
                conn.execute(
                    """
                    UPDATE agent_workspaces
                    SET owner_conversation_id = ?, updated_at = ?
                    WHERE owner_session_id = ?
                    """,
                    (row["conversation_id"], now, session_id),
                )
        return dict(row) if row else {}

    def create_workspace(
        self,
        *,
        owner_session_id: str,
        owner_conversation_id: str | None = None,
        baseline_root: str = "",
    ) -> dict[str, Any]:
        workspace_id = f"ws_{uuid.uuid4().hex}"
        now = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_workspaces(
                    workspace_id, owner_session_id, owner_conversation_id, root_relative_path,
                    baseline_root, status, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', 1, ?, ?)
                """,
                (
                    workspace_id,
                    owner_session_id,
                    owner_conversation_id,
                    f"workspaces/{workspace_id}",
                    baseline_root,
                    now,
                    now,
                ),
            )
        return self.get_workspace(workspace_id) or {}

    def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_workspaces WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_workspace_by_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT w.* FROM agent_workspaces w
                JOIN workspace_members m ON m.workspace_id = w.workspace_id
                WHERE m.session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_workspace_member(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspace_members WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def attach_workspace_session(
        self,
        *,
        workspace_id: str,
        session_id: str,
        conversation_id: str | None = None,
        role: str,
        parent_session_id: str | None = None,
    ) -> dict[str, Any]:
        now = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workspace_members(
                    session_id, workspace_id, conversation_id, role, parent_session_id,
                    joined_at, last_active_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    workspace_id = excluded.workspace_id,
                    conversation_id = excluded.conversation_id,
                    role = excluded.role,
                    parent_session_id = excluded.parent_session_id,
                    last_active_at = excluded.last_active_at
                """,
                (
                    session_id,
                    workspace_id,
                    conversation_id,
                    role,
                    parent_session_id,
                    now,
                    now,
                ),
            )
        return self.get_workspace_member(session_id) or {}

    def update_workspace(
        self,
        workspace_id: str,
        *,
        owner_conversation_id: str | None = None,
        baseline_root: str | None = None,
    ) -> dict[str, Any] | None:
        now = utcnow_iso()
        with self._connect() as conn:
            if owner_conversation_id is not None:
                conn.execute(
                    "UPDATE agent_workspaces SET owner_conversation_id = ?, updated_at = ? WHERE workspace_id = ?",
                    (owner_conversation_id, now, workspace_id),
                )
            if baseline_root is not None:
                conn.execute(
                    "UPDATE agent_workspaces SET baseline_root = ?, updated_at = ? WHERE workspace_id = ?",
                    (baseline_root, now, workspace_id),
                )
        return self.get_workspace(workspace_id)

    def touch_workspace_session(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE workspace_members SET last_active_at = ? WHERE session_id = ?",
                (utcnow_iso(), session_id),
            )

    def list_workspace_members(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT m.*, c.title, c.visibility, c.deleted_at
                FROM workspace_members m
                LEFT JOIN conversations c ON c.session_id = m.session_id
                WHERE m.workspace_id = ?
                ORDER BY m.joined_at ASC
                """,
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_workspace_members(self, workspace_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM workspace_members WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
        return int(row["count"])

    def delete_workspace_session(self, session_id: str) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                "DELETE FROM workspace_members WHERE session_id = ?",
                (session_id,),
            )
            return result.rowcount > 0

    def workspace_has_active_work(self, workspace_id: str) -> bool:
        with self._connect() as conn:
            run = conn.execute(
                """
                SELECT 1
                FROM agent_runs ar
                JOIN conversations c ON c.conversation_id = ar.conversation_id
                WHERE c.workspace_id = ?
                  AND ar.status IN ('queued', 'running', 'waiting_input')
                LIMIT 1
                """,
                (workspace_id,),
            ).fetchone()
            if run is not None:
                return True
            subagent = conn.execute(
                """
                SELECT 1 FROM subagent_runs
                WHERE workspace_id = ? AND status = 'running'
                LIMIT 1
                """,
                (workspace_id,),
            ).fetchone()
            return subagent is not None

    def upsert_workspace_migration_journal(
        self,
        *,
        session_id: str,
        workspace_id: str,
        source_root: str,
        destination_root: str,
        mode: str,
        status: str,
        error_text: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workspace_migration_journal(
                    session_id, workspace_id, source_root, destination_root,
                    mode, status, error_text, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    workspace_id = excluded.workspace_id,
                    source_root = excluded.source_root,
                    destination_root = excluded.destination_root,
                    mode = excluded.mode,
                    status = excluded.status,
                    error_text = excluded.error_text,
                    updated_at = excluded.updated_at
                """,
                (
                    session_id,
                    workspace_id,
                    source_root,
                    destination_root,
                    mode,
                    status,
                    error_text,
                    utcnow_iso(),
                ),
            )

    def list_workspace_migration_journal(self, *, only_pending: bool = False) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if only_pending:
                rows = conn.execute(
                    "SELECT * FROM workspace_migration_journal WHERE status = 'pending' ORDER BY updated_at ASC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM workspace_migration_journal ORDER BY updated_at ASC"
                ).fetchall()
        return [dict(row) for row in rows]

    def mark_workspace_deleted(self, workspace_id: str) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                "UPDATE agent_workspaces SET status = 'deleted', updated_at = ? WHERE workspace_id = ?",
                (utcnow_iso(), workspace_id),
            )
            return result.rowcount > 0

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
            if (
                row
                and row.get("platform_id") == platform_id
                and row.get("external_user_id") == external_user_id
                and row.get("deleted_at") is None
            ):
                return row
            return None
        with self._connect() as conn:
            if conversation_key:
                row = conn.execute(
                    """
                    SELECT * FROM conversations
                    WHERE platform_id = ? AND external_user_id = ? AND conversation_key = ? AND deleted_at IS NULL
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (platform_id, external_user_id, conversation_key),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM conversations
                    WHERE platform_id = ? AND external_user_id = ? AND deleted_at IS NULL
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (platform_id, external_user_id),
                ).fetchone()
        return dict(row) if row else None

    def list_conversations_for_user(
        self,
        user_id: int,
        *,
        include_hidden: bool = False,
        include_archived: bool = False,
        include_deleted: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            clauses = ["owner_user_id = ?"]
            parameters: list[Any] = [user_id]
            self._apply_conversation_visibility_filters(
                clauses,
                parameters,
                include_hidden=include_hidden,
                include_archived=include_archived,
                include_deleted=include_deleted,
            )
            rows = self._execute_conversation_page(conn, clauses, parameters, limit=limit, offset=offset)
        return [dict(row) for row in rows]

    def list_conversations_for_admin(self, user_id: int) -> list[dict[str, Any]]:
        return self.list_conversations_for_user(user_id)

    def list_conversations_for_host_user(
        self,
        *,
        platform_id: int,
        external_user_id: str,
        include_hidden: bool = False,
        include_archived: bool = False,
        include_deleted: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            clauses = ["platform_id = ?", "external_user_id = ?"]
            parameters: list[Any] = [platform_id, external_user_id]
            self._apply_conversation_visibility_filters(
                clauses,
                parameters,
                include_hidden=include_hidden,
                include_archived=include_archived,
                include_deleted=include_deleted,
            )
            rows = self._execute_conversation_page(conn, clauses, parameters, limit=limit, offset=offset)
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

    @staticmethod
    def _apply_conversation_visibility_filters(
        clauses: list[str],
        parameters: list[Any],
        *,
        include_hidden: bool,
        include_archived: bool,
        include_deleted: bool,
    ) -> None:
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        if not include_hidden:
            clauses.append("visibility = 'normal'")
        if not include_archived:
            clauses.append("archived_at IS NULL")

    @staticmethod
    def _execute_conversation_page(
        conn: sqlite3.Connection,
        clauses: list[str],
        parameters: list[Any],
        *,
        limit: int | None,
        offset: int,
    ) -> list[sqlite3.Row]:
        sql = f"""
            SELECT * FROM conversations
            WHERE {' AND '.join(clauses)}
            ORDER BY COALESCE(pinned_at, '') DESC, updated_at DESC, created_at DESC
        """
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            parameters.extend((max(1, int(limit)), max(0, int(offset))))
        return list(conn.execute(sql, tuple(parameters)).fetchall())

    def update_conversation(
        self,
        conversation_id: str,
        *,
        expected_revision: int | None = None,
        title: str | None = None,
        visibility: str | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_conversation(conversation_id)
        if current is None:
            return None
        if current.get("deleted_at") is not None:
            return None
        if expected_revision is not None and int(current.get("revision") or 0) != int(expected_revision):
            raise ValueError("conversation revision conflict")

        updates: dict[str, Any] = {"updated_at": utcnow_iso(), "revision": int(current.get("revision") or 0) + 1}
        if title is not None:
            updates["title"] = title.strip() or str(current.get("title") or "新对话")
        if visibility is not None:
            if visibility not in {"normal", "hidden"}:
                raise ValueError("visibility must be normal or hidden")
            updates["visibility"] = visibility
        if pinned is not None:
            updates["pinned_at"] = utcnow_iso() if pinned else None
        if archived is not None:
            updates["archived_at"] = utcnow_iso() if archived else None
        if metadata is not None:
            merged = json.loads(current.get("metadata_json") or "{}")
            if not isinstance(merged, dict):
                merged = {}
            merged.update(metadata)
            updates["metadata_json"] = json.dumps(merged, ensure_ascii=False)

        set_sql = ", ".join(f"{key} = ?" for key in updates)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE conversations SET {set_sql} WHERE conversation_id = ?",
                (*updates.values(), conversation_id),
            )
        return self.get_conversation(conversation_id)

    def soft_delete_conversation(self, conversation_id: str) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE conversations
                SET deleted_at = ?, revision = revision + 1
                WHERE conversation_id = ? AND deleted_at IS NULL
                """,
                (utcnow_iso(), conversation_id),
            )
            return result.rowcount > 0

    def set_conversation_last_run(self, conversation_id: str, run_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET last_run_id = ? WHERE conversation_id = ?",
                (run_id, conversation_id),
            )

    def create_agent_run(
        self,
        *,
        run_id: str,
        session_id: str,
        conversation_id: str | None = None,
        parent_run_id: str | None = None,
        root_run_id: str | None = None,
        agent_kind: str = "main",
        status: str = "running",
        request: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        now = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_runs(
                    run_id, session_id, conversation_id, parent_run_id, root_run_id,
                    agent_kind, status, request_json, idempotency_key, trace_id, created_at, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    session_id,
                    conversation_id,
                    parent_run_id,
                    root_run_id or parent_run_id or run_id,
                    agent_kind,
                    status,
                    json.dumps(request or {}, ensure_ascii=False),
                    idempotency_key,
                    trace_id,
                    now,
                    now if status in {"running", "waiting_input"} else None,
                ),
            )
        return self.get_agent_run(run_id) or {}

    def find_agent_run_by_idempotency(self, conversation_id: str, idempotency_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM agent_runs
                WHERE conversation_id = ? AND idempotency_key = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (conversation_id, idempotency_key),
            ).fetchone()
        return dict(row) if row else None

    def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def get_agent_runs(self, run_ids: list[str]) -> list[dict[str, Any]]:
        normalized = [str(item) for item in dict.fromkeys(run_ids) if item]
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM agent_runs WHERE run_id IN ({placeholders})",
                tuple(normalized),
            ).fetchall()
        return [dict(row) for row in rows]

    def append_agent_run_event(
        self,
        *,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        visibility: str = "visible",
        status: str | None = None,
        result: Any | None = None,
        error: Any | None = None,
    ) -> int:
        now = utcnow_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT event_cursor FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("run does not exist")
            seq = int(row["event_cursor"]) + 1
            conn.execute(
                """
                INSERT INTO run_events(run_id, seq, type, payload_json, visibility, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    seq,
                    event_type,
                    json.dumps(payload, ensure_ascii=False),
                    visibility,
                    now,
                ),
            )
            updates: dict[str, Any] = {"event_cursor": seq}
            if status is not None:
                updates["status"] = status
            if result is not None:
                updates["result_json"] = json.dumps(result, ensure_ascii=False)
            if error is not None:
                updates["error_json"] = json.dumps(error, ensure_ascii=False)
            if status in {"completed", "failed", "cancelled", "timed_out"}:
                updates["finished_at"] = now
            set_sql = ", ".join(f"{key} = ?" for key in updates)
            conn.execute(
                f"UPDATE agent_runs SET {set_sql} WHERE run_id = ?",
                (*updates.values(), run_id),
            )
        return seq

    def list_agent_run_events(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM run_events
                WHERE run_id = ? AND seq > ?
                ORDER BY seq ASC LIMIT ?
                """,
                (run_id, after_seq, max(1, int(limit))),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            result.append(item)
        return result

    def create_subagent_run(
        self,
        *,
        child_run_id: str,
        workspace_id: str,
        parent_run_id: str,
        parent_session_id: str,
        child_session_id: str,
        name: str,
        task: str,
    ) -> dict[str, Any]:
        now = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO subagent_runs(
                    child_run_id, workspace_id, parent_run_id, parent_session_id, child_session_id,
                    latest_run_id, name, task, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    child_run_id,
                    workspace_id,
                    parent_run_id,
                    parent_session_id,
                    child_session_id,
                    child_run_id,
                    name,
                    task,
                    now,
                    now,
                ),
            )
        return self.get_subagent_run(child_run_id) or {}

    def get_subagent_run(self, child_run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM subagent_runs WHERE child_run_id = ?",
                (child_run_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_subagent_run(
        self,
        child_run_id: str,
        *,
        status: str,
        result_text: str | None = None,
        error_text: str | None = None,
    ) -> dict[str, Any] | None:
        now = utcnow_iso()
        terminal = status in {"completed", "failed", "cancelled", "timed_out"}
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE subagent_runs
                SET status = ?, result_text = COALESCE(?, result_text), error_text = COALESCE(?, error_text),
                    updated_at = ?, finished_at = CASE WHEN ? THEN ? ELSE finished_at END
                WHERE child_run_id = ?
                """,
                (
                    status,
                    result_text,
                    error_text,
                    now,
                    1 if terminal else 0,
                    now,
                    child_run_id,
                ),
            )
        return self.get_subagent_run(child_run_id)

    def set_subagent_latest_run(self, child_run_id: str, latest_run_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE subagent_runs
                SET latest_run_id = ?, status = 'running', updated_at = ?
                WHERE child_run_id = ?
                """,
                (latest_run_id, utcnow_iso(), child_run_id),
            )

    def list_subagent_runs_for_session(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM subagent_runs
                WHERE parent_session_id = ?
                ORDER BY created_at DESC
                """,
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def backfill_conversation_metadata(self, session_id: str, updates: dict[str, Any]) -> bool:
        """补全会话 metadata 中缺失的字段,已有非空值不会被覆盖。

        用于 bind 复用由 engine 运行时自动创建的会话时,回填 external_user_name 等身份信息。
        """
        current = self.get_conversation_by_session(session_id)
        if current is None:
            return False
        metadata = json.loads(current.get("metadata_json") or "{}")
        changed = False
        for key, value in updates.items():
            if not metadata.get(key) and value:
                metadata[key] = value
                changed = True
        if not changed:
            return False
        now = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET metadata_json = ?, updated_at = ? WHERE session_id = ?",
                (json.dumps(metadata, ensure_ascii=False), now, session_id),
            )
        return True

    def get_workspace_runtime(self, workspace_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    wr.*,
                    c.title AS conversation_title,
                    c.host_name AS conversation_host_name,
                    p.display_name AS platform_display_name,
                    u.full_name AS owner_user_name
                FROM workspace_runtimes wr
                LEFT JOIN conversations c ON c.conversation_id = wr.owner_conversation_id
                LEFT JOIN platforms p ON p.platform_id = wr.platform_id
                LEFT JOIN users u ON u.user_id = wr.owner_user_id
                WHERE wr.workspace_id = ?
                """,
                (workspace_id,),
            ).fetchone()
        return self._row_to_workspace_runtime(row)

    def list_workspace_runtimes(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    wr.*,
                    c.title AS conversation_title,
                    c.host_name AS conversation_host_name,
                    p.display_name AS platform_display_name,
                    u.full_name AS owner_user_name
                FROM workspace_runtimes wr
                LEFT JOIN conversations c ON c.conversation_id = wr.owner_conversation_id
                LEFT JOIN platforms p ON p.platform_id = wr.platform_id
                LEFT JOIN users u ON u.user_id = wr.owner_user_id
                ORDER BY COALESCE(wr.last_used_at, wr.updated_at) DESC, wr.workspace_id ASC
                """
            ).fetchall()
        return [self._row_to_workspace_runtime(row) for row in rows if row is not None]

    def get_system_audit_overview(self) -> dict[str, Any]:
        with self._connect() as conn:
            platform_rows = conn.execute(
                """
                SELECT
                    p.platform_id,
                    p.platform_key,
                    p.display_name,
                    p.owner_user_id,
                    p.updated_at,
                    u.full_name AS owner_name
                FROM platforms p
                LEFT JOIN users u ON u.user_id = p.owner_user_id
                WHERE p.host_type = 'embedded'
                ORDER BY p.created_at DESC, p.platform_id DESC
                """
            ).fetchall()
            user_count_row = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()
            pending_request_row = conn.execute(
                "SELECT COUNT(*) AS count FROM platform_registration_requests WHERE status = 'pending'"
            ).fetchone()
            admin_rows = conn.execute(
                """
                SELECT platform_id, COUNT(*) AS count
                FROM platform_admins
                GROUP BY platform_id
                """
            ).fetchall()
            conversation_rows = conn.execute(
                """
                SELECT
                    c.platform_id,
                    c.owner_user_id,
                    c.external_user_id,
                    c.message_count,
                    c.updated_at
                FROM conversations c
                JOIN platforms p ON p.platform_id = c.platform_id
                WHERE p.host_type = 'embedded'
                ORDER BY c.updated_at DESC
                """
            ).fetchall()
            runtime_rows = conn.execute(
                """
                SELECT
                    wr.platform_id,
                    wr.status,
                    COALESCE(wr.last_used_at, wr.updated_at) AS activity_at
                FROM workspace_runtimes wr
                JOIN platforms p ON p.platform_id = wr.platform_id
                WHERE p.host_type = 'embedded'
                ORDER BY COALESCE(wr.last_used_at, wr.updated_at) DESC
                """
            ).fetchall()

        platform_metrics: dict[int, dict[str, Any]] = {
            int(row["platform_id"]): {
                "platform_id": int(row["platform_id"]),
                "platform_key": str(row["platform_key"]),
                "display_name": str(row["display_name"]),
                "owner_user_id": int(row["owner_user_id"]),
                "owner_name": str(row["owner_name"] or "未知负责人"),
                "admin_count": 0,
                "hosted_user_count": 0,
                "conversation_count": 0,
                "message_count": 0,
                "active_runtime_count": 0,
                "runtime_count": 0,
                "last_activity_at": row["updated_at"],
                "_hosted_users": set(),
            }
            for row in platform_rows
        }
        global_hosted_users: set[str] = set()

        for row in admin_rows:
            platform_id = int(row["platform_id"])
            if platform_id in platform_metrics:
                platform_metrics[platform_id]["admin_count"] = int(row["count"] or 0)

        for row in conversation_rows:
            platform_id = int(row["platform_id"])
            metric = platform_metrics.get(platform_id)
            if metric is None:
                continue
            metric["conversation_count"] += 1
            metric["message_count"] += int(row["message_count"] or 0)
            activity_at = row["updated_at"]
            if activity_at and (not metric["last_activity_at"] or activity_at > metric["last_activity_at"]):
                metric["last_activity_at"] = activity_at
            identity = self._build_hosted_user_identity(
                owner_user_id=row["owner_user_id"],
                external_user_id=row["external_user_id"],
            )
            if identity:
                metric["_hosted_users"].add(identity)
                global_hosted_users.add(identity)

        for row in runtime_rows:
            platform_id = int(row["platform_id"])
            metric = platform_metrics.get(platform_id)
            if metric is None:
                continue
            metric["runtime_count"] += 1
            if str(row["status"]) in {"provisioning", "running", "busy"}:
                metric["active_runtime_count"] += 1
            activity_at = row["activity_at"]
            if activity_at and (not metric["last_activity_at"] or activity_at > metric["last_activity_at"]):
                metric["last_activity_at"] = activity_at

        platforms: list[dict[str, Any]] = []
        for metric in platform_metrics.values():
            metric["hosted_user_count"] = len(metric.pop("_hosted_users"))
            platforms.append(metric)

        platforms.sort(
            key=lambda item: (
                item["conversation_count"],
                item["active_runtime_count"],
                item["message_count"],
                item["platform_id"],
            ),
            reverse=True,
        )

        return {
            "platform_count": len(platforms),
            "platforms_with_traffic_count": sum(1 for item in platforms if item["conversation_count"] > 0),
            "hosted_user_count": len(global_hosted_users),
            "internal_user_count": int(user_count_row["count"] or 0) if user_count_row else 0,
            "platform_admin_assignment_count": sum(int(item["admin_count"]) for item in platforms),
            "pending_registration_request_count": int(pending_request_row["count"] or 0) if pending_request_row else 0,
            "conversation_count": sum(int(item["conversation_count"]) for item in platforms),
            "message_count": sum(int(item["message_count"]) for item in platforms),
            "active_runtime_count": sum(int(item["active_runtime_count"]) for item in platforms),
            "runtime_count": sum(int(item["runtime_count"]) for item in platforms),
            "platforms": platforms,
        }

    def get_audit_trends(self, days: int = 30, platform_id: int | None = None, breakdown: bool = False) -> dict[str, Any]:
        """按天聚合审计趋势:日增用户/会话/消息 + 累计曲线。

        platform_id 为空时统计全部来源(独立工作台 + 宿主平台):
        - 用户基于 users 表(内部账号)精确计数
        platform_id 指定时按该平台过滤:
        - 会话/消息取该平台 conversations 过滤聚合
        - 用户按"首次会话日归因"的活跃去重用户计数
        消息无逐条时间戳,日增消息按会话创建日归因估算。
        """
        days = max(1, min(days, 365))
        today = datetime.now(timezone.utc).date()
        start_date = today - timedelta(days=days - 1)
        start_iso = start_date.isoformat()

        daily_users: dict[str, int] = {}
        daily_conversations: dict[str, int] = {}
        daily_messages: dict[str, int] = {}
        base_users = 0
        base_conversations = 0
        base_messages = 0

        with self._connect() as conn:
            if platform_id is None:
                base_users = conn.execute(
                    "SELECT COUNT(*) FROM users WHERE created_at < ?", (start_iso,)
                ).fetchone()[0]
                base_conversations = conn.execute(
                    "SELECT COUNT(*) FROM conversations WHERE created_at < ?", (start_iso,)
                ).fetchone()[0]
                base_messages = conn.execute(
                    "SELECT COALESCE(SUM(message_count), 0) FROM conversations WHERE created_at < ?",
                    (start_iso,),
                ).fetchone()[0]
                for row in conn.execute(
                    "SELECT substr(created_at, 1, 10) AS day, COUNT(*) FROM users "
                    "WHERE created_at >= ? GROUP BY day ORDER BY day",
                    (start_iso,),
                ):
                    daily_users[str(row[0])] = int(row[1])
                conv_rows = conn.execute(
                    "SELECT substr(created_at, 1, 10) AS day, COUNT(*), COALESCE(SUM(message_count), 0) "
                    "FROM conversations WHERE created_at >= ? GROUP BY day ORDER BY day",
                    (start_iso,),
                ).fetchall()
            else:
                # 平台维度:用户按首次会话日归因(COALESCE 外部/内部身份)
                base_users = conn.execute(
                    "SELECT COUNT(*) FROM ("
                    "  SELECT COALESCE(external_user_id, 'internal:' || owner_user_id) AS uid, MIN(created_at) AS first_at "
                    "  FROM conversations WHERE platform_id = ? GROUP BY uid"
                    ") WHERE substr(first_at, 1, 10) < ?",
                    (platform_id, start_iso),
                ).fetchone()[0]
                base_conversations = conn.execute(
                    "SELECT COUNT(*) FROM conversations WHERE platform_id = ? AND created_at < ?",
                    (platform_id, start_iso),
                ).fetchone()[0]
                base_messages = conn.execute(
                    "SELECT COALESCE(SUM(message_count), 0) FROM conversations "
                    "WHERE platform_id = ? AND created_at < ?",
                    (platform_id, start_iso),
                ).fetchone()[0]
                for row in conn.execute(
                    "SELECT substr(first_at, 1, 10) AS day, COUNT(*) FROM ("
                    "  SELECT COALESCE(external_user_id, 'internal:' || owner_user_id) AS uid, MIN(created_at) AS first_at "
                    "  FROM conversations WHERE platform_id = ? GROUP BY uid"
                    ") WHERE substr(first_at, 1, 10) >= ? GROUP BY day ORDER BY day",
                    (platform_id, start_iso),
                ):
                    daily_users[str(row[0])] = int(row[1])
                conv_rows = conn.execute(
                    "SELECT substr(created_at, 1, 10) AS day, COUNT(*), COALESCE(SUM(message_count), 0) "
                    "FROM conversations WHERE platform_id = ? AND created_at >= ? GROUP BY day ORDER BY day",
                    (platform_id, start_iso),
                ).fetchall()

            for row in conv_rows:
                daily_conversations[str(row[0])] = int(row[1])
                daily_messages[str(row[0])] = int(row[2])

        points: list[dict[str, Any]] = []
        total_users = int(base_users)
        total_conversations = int(base_conversations)
        total_messages = int(base_messages)
        for offset in range(days):
            day = (start_date + timedelta(days=offset)).isoformat()
            new_users = daily_users.get(day, 0)
            new_conversations = daily_conversations.get(day, 0)
            new_messages = daily_messages.get(day, 0)
            total_users += new_users
            total_conversations += new_conversations
            total_messages += new_messages
            points.append(
                {
                    "date": day,
                    "new_users": new_users,
                    "new_conversations": new_conversations,
                    "new_messages": new_messages,
                    "total_users": total_users,
                    "total_conversations": total_conversations,
                    "total_messages": total_messages,
                }
            )

        result: dict[str, Any] = {"points": points}

        # 平台对比堆叠视图: 按平台分解的日增/累计时序(仅全部来源时有意义)
        if breakdown and platform_id is None:
            result["platform_series"] = self._audit_platform_series(conn_days=days, start_iso=start_iso, start_date=start_date)
        return result

    def _audit_platform_series(self, *, conn_days: int, start_iso: str, start_date) -> list[dict[str, Any]]:
        """按平台 × 天聚合会话/消息/用户(当日活跃去重),并逐日累计算累计值。"""
        with self._connect() as conn:
            platforms = {
                int(row["platform_id"]): str(row["display_name"])
                for row in conn.execute("SELECT platform_id, display_name FROM platforms")
            }
            daily: dict[int, dict[str, dict[str, int]]] = {pid: {} for pid in platforms}
            for row in conn.execute(
                "SELECT platform_id, substr(created_at, 1, 10) AS day, COUNT(*), "
                "COALESCE(SUM(message_count), 0), "
                "COUNT(DISTINCT COALESCE(external_user_id, 'internal:' || owner_user_id)) "
                "FROM conversations WHERE created_at >= ? AND platform_id IS NOT NULL "
                "GROUP BY platform_id, day ORDER BY day",
                (start_iso,),
            ):
                pid = int(row[0])
                day = str(row[1])
                daily.setdefault(pid, {})[day] = {
                    "conversations": int(row[2]),
                    "messages": int(row[3]),
                    "users": int(row[4]),
                }

        series: list[dict[str, Any]] = []
        for pid, name in platforms.items():
            per_day = daily.get(pid, {})
            platform_points: list[dict[str, Any]] = []
            cum_conversations = cum_messages = cum_users = 0
            for offset in range(conn_days):
                day = (start_date + timedelta(days=offset)).isoformat()
                entry = per_day.get(day, {"conversations": 0, "messages": 0, "users": 0})
                cum_conversations += entry["conversations"]
                cum_messages += entry["messages"]
                cum_users += entry["users"]
                platform_points.append(
                    {
                        "date": day,
                        "new_conversations": entry["conversations"],
                        "new_messages": entry["messages"],
                        "new_users": entry["users"],
                        "total_conversations": cum_conversations,
                        "total_messages": cum_messages,
                        "total_users": cum_users,
                    }
                )
            if cum_conversations > 0 or cum_messages > 0 or cum_users > 0:
                series.append(
                    {"platform_id": pid, "display_name": name, "points": platform_points}
                )
        series.sort(key=lambda item: -item["points"][-1]["total_messages"])
        return series

    def upsert_workspace_runtime(
        self,
        *,
        workspace_id: str,
        owner_session_id: str,
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
                INSERT INTO workspace_runtimes(
                    workspace_id, owner_session_id, owner_conversation_id,
                    platform_id, owner_user_id, external_user_id,
                    container_name, container_id, image, status, generation, network_mode,
                    created_at, updated_at, last_started_at, last_used_at,
                    idle_expires_at, max_expires_at, destroyed_at, destroy_reason,
                    restart_count, workspace_root, home_root, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    owner_session_id = excluded.owner_session_id,
                    owner_conversation_id = excluded.owner_conversation_id,
                    platform_id = excluded.platform_id,
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
                    workspace_id,
                    owner_session_id,
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
        runtime = self.get_workspace_runtime(workspace_id)
        if runtime is None:
            raise RuntimeError("Failed to persist workspace runtime")
        return runtime

    def delete_workspace_runtime(self, workspace_id: str) -> bool:
        with self._connect() as conn:
            result = conn.execute("DELETE FROM workspace_runtimes WHERE workspace_id = ?", (workspace_id,))
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

    def _build_hosted_user_identity(
        self,
        *,
        owner_user_id: Any | None,
        external_user_id: Any | None,
    ) -> str | None:
        if external_user_id:
            return f"external:{external_user_id}"
        if owner_user_id:
            return f"internal:{owner_user_id}"
        return None

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
            "sampling": json.loads(row["sampling_json"] or "{}"),
        }

    def _row_to_prompt_config(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            **dict(row),
            "enabled": bool(row["enabled"]),
        }

    def _row_to_workspace_runtime(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        payload = dict(row)
        payload["generation"] = int(payload.get("generation") or 0)
        payload["restart_count"] = int(payload.get("restart_count") or 0)
        payload["metadata"] = json.loads(payload.pop("metadata_json", "{}") or "{}")
        return payload


store_service = StoreService()
