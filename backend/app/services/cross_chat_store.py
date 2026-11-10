"""持久化跨会话投递及其条件状态转换。"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.services.store import store_service


def timestamp(seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class CrossChatStore:
    def list_peers(self, source: dict, query: str, before: tuple[str, str] | None, limit: int) -> list[dict]:
        clauses = [
            "owner_user_id IS ?", "platform_id IS ?", "external_user_id IS ?", "external_org_id IS ?",
            "conversation_id != ?", "visibility = 'normal'", "deleted_at IS NULL", "archived_at IS NULL",
        ]
        params = [source[key] for key in ("owner_user_id", "platform_id", "external_user_id", "external_org_id", "conversation_id")]
        if query:
            clauses.append("instr(lower(title), lower(?)) > 0")
            params.append(query)
        if before is not None:
            clauses.append("(updated_at, conversation_id) < (?, ?)")
            params.extend(before)
        with store_service._connect() as conn:
            rows = conn.execute(
                f"""SELECT conversation_id, session_id, title, updated_at FROM conversations
                    WHERE {' AND '.join(clauses)}
                    ORDER BY updated_at DESC, conversation_id DESC LIMIT ?""", (*params, limit),
            )
            return [dict(row) for row in rows]

    def create(self, delivery_id: str, source: str, source_run: str, target: str, expected: str | None,
               mode: str, message: str) -> None:
        now = timestamp()
        with store_service._connect() as conn:
            conn.execute("""
                INSERT INTO cross_chat_deliveries (
                    delivery_id, dispatch_key, source_conversation_id, source_run_id, target_conversation_id, expected_run_id,
                    mode, message, status, created_at, expires_at, next_attempt_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'awaiting_approval', ?, ?, ?, ?)
                """, (delivery_id, f"cross-chat:{uuid4().hex}", source, source_run, target, expected,
                      mode, message, now, timestamp(3600), now, now))

    def get(self, delivery_id: str) -> dict | None:
        with store_service._connect() as conn:
            row = conn.execute("SELECT * FROM cross_chat_deliveries WHERE delivery_id = ?", (delivery_id,)).fetchone()
            return dict(row) if row else None

    def due(self) -> list[dict]:
        with store_service._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM cross_chat_deliveries
                WHERE status IN ('awaiting_approval', 'queued', 'dispatching') AND next_attempt_at <= ?
                ORDER BY next_attempt_at, created_at, delivery_id LIMIT 32
                """, (timestamp(),))
            return [dict(row) for row in rows]

    def transition(self, delivery_id: str, previous: str, status: str, *, detail: str | None = None,
                   target_run_id: str | None = None, delay: int = 0) -> bool:
        with store_service._connect() as conn:
            changed = conn.execute("""
                UPDATE cross_chat_deliveries
                SET status=?, detail=?, target_run_id=COALESCE(?, target_run_id), next_attempt_at=?, updated_at=?
                WHERE delivery_id=? AND status=?
                """, (status, detail, target_run_id, timestamp(delay), timestamp(), delivery_id, previous))
            return changed.rowcount == 1

    def has_earlier_queued(self, delivery: dict) -> bool:
        with store_service._connect() as conn:
            return conn.execute("""
                SELECT 1 FROM cross_chat_deliveries WHERE target_conversation_id=?
                  AND mode IN ('start', 'queue') AND status IN ('queued', 'dispatching')
                  AND (created_at, delivery_id) < (?, ?) LIMIT 1
                """, (delivery["target_conversation_id"], delivery["created_at"], delivery["delivery_id"])).fetchone() is not None

    def steering(self, target: str, run_id: str) -> list[dict]:
        with store_service._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM cross_chat_deliveries WHERE target_conversation_id=? AND expected_run_id=?
                  AND mode='steer' AND status='queued' AND expires_at > ?
                ORDER BY created_at, delivery_id LIMIT 4
                """, (target, run_id, timestamp()))
            return [dict(row) for row in rows]

    def inbox_cursor(self, target: str) -> str | None:
        with store_service._connect() as conn:
            row = conn.execute("""
                SELECT delivery_id FROM cross_chat_deliveries WHERE target_conversation_id=? AND status='delivered'
                ORDER BY updated_at DESC, delivery_id DESC LIMIT 1
                """, (target,)).fetchone()
            return str(row["delivery_id"]) if row else None

    def list_for_conversation(self, conversation_id: str, before: tuple[str, str] | None, limit: int) -> list[dict]:
        cursor_filter = " AND (created_at, delivery_id) < (?, ?)" if before else ""
        with store_service._connect() as conn:
            rows = conn.execute(
                f"""SELECT * FROM cross_chat_deliveries
                    WHERE (source_conversation_id=? OR target_conversation_id=? AND status='delivered'){cursor_filter}
                    ORDER BY created_at DESC, delivery_id DESC LIMIT ?""",
                (conversation_id, conversation_id, *(before or ()), limit),
            )
            return [dict(row) for row in rows]


cross_chat_store = CrossChatStore()
