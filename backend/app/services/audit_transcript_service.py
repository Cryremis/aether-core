"""可重建的持久化审计展示索引；热路径只解码当前页消息。"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core.config import settings
from app.services.context.history_store import history_store
from app.services.session_identity import identity_updates
from app.services.session_service import session_service
from app.services.store import store_service
from app.services.transcript_service import transcript_service


class AuditTranscriptService:
    # 展示投影规则改变时提升版本，已有索引会按需自动重建。
    FORMAT_VERSION = 1

    def _source(self, conn, session_id: str) -> tuple[str, dict[str, Any]] | None:
        row = conn.execute("""SELECT a.*, s.revision AS current_revision
            FROM audit_transcript_sources a LEFT JOIN context_snapshots s USING(session_id)
            WHERE a.session_id=?""", (session_id,)).fetchone()
        # 无账本的旧 JSON 会话使用 revision=0；账本出现或删除后必须重新取来源。
        if row is None or row["snapshot_revision"] != (row["current_revision"] or 0):
            return None
        metadata = history_store._decode(row["metadata"])
        if row["snapshot_revision"] == 0:
            path = session_service._metadata_path(session_id)
            if not path.exists():
                raise LookupError("会话历史不存在")
            stat = path.stat()
            if metadata.get("_audit_legacy_stamp") != [stat.st_mtime_ns, stat.st_size]:
                return None
        # 仅纳入可恢复响应的轻量版本与 run 状态，活跃流的每个 token 不重建历史。
        attempts = conn.execute("""SELECT a.attempt_id, a.status, COALESCE(v.revision,0), r.status
            FROM context_attempts a
            LEFT JOIN audit_attempt_versions v USING(session_id,attempt_id)
            LEFT JOIN agent_runs r ON r.run_id=json_extract(a.manifest,'$.run_id')
            WHERE a.session_id=? AND a.committed=0 AND a.status!='invalid'
              AND COALESCE(json_extract(a.manifest,'$.purpose'),'conversation')='conversation'
              AND (r.status IS NULL OR r.status NOT IN ('queued','running','waiting_input'))
            ORDER BY a.rowid""", (session_id,)).fetchall()
        key = hashlib.sha256(json.dumps([
            self.FORMAT_VERSION, row["fingerprint"], [list(item) for item in attempts],
            bool(settings.capability_secret_encryption_key),
        ]).encode()).hexdigest()
        return key, metadata

    def _bootstrap_source(self, conversation: dict[str, Any]) -> None:
        session_id = conversation["session_id"]
        payload = history_store.snapshot(session_id)
        revision = int(payload.get("ledger_revision", 0)) if payload else 0
        if payload is None:
            session = session_service.read_for_audit(conversation)
            payload = vars(session).copy()
            stat = session_service._metadata_path(session_id).stat()
            payload["_audit_legacy_stamp"] = [stat.st_mtime_ns, stat.st_size]
        identity_updates(session_id, payload, conversation)
        with store_service._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT revision FROM context_snapshots WHERE session_id=?", (session_id,)).fetchone()
            if (int(current[0]) if current else 0) == revision:
                history_store.sync_audit_source(conn, session_id, payload, revision)

    def _page(self, conn, session_id: str, source_key: str, *, limit: int, before: int | None) -> dict | None:
        head = conn.execute("SELECT total FROM audit_transcript_heads WHERE session_id=? AND source_key=?",
                            (session_id, source_key)).fetchone()
        if head is None:
            return None
        total = int(head["total"])
        end = min(before, total) if before is not None else total
        start = max(0, end - limit)
        # 联合主键直接定位区间，查询与解码量只随当前页大小增长。
        rows = conn.execute("""SELECT payload FROM audit_transcript_items
            WHERE session_id=? AND position>=? AND position<? ORDER BY position""",
            (session_id, start, end)).fetchall()
        return {"transcript": [history_store._decode(row["payload"]) for row in rows],
                "transcript_page": {"total": total, "start": start, "end": end, "has_more": start > 0}}

    def _cached_page(self, conversation: dict[str, Any], *, limit: int, before: int | None):
        session_id = conversation["session_id"]
        with store_service._connect() as conn:
            conn.execute("BEGIN")
            source = self._source(conn, session_id)
            if source is None:
                return None, None
            source_key, metadata = source
            identity_updates(session_id, metadata, conversation)
            if int(metadata.get("message_schema_version", 1)) > 3:
                raise RuntimeError("当前版本不能读取更新版本的会话格式")
            page = self._page(conn, session_id, source_key, limit=limit, before=before)
            return source, {**page, "metadata": metadata} if page is not None else None

    def read_page(self, conversation: dict[str, Any], *, limit: int, before: int | None = None) -> dict[str, Any]:
        session_id = conversation["session_id"]
        with store_service._connect() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='audit_transcript_sources'").fetchone():
                history_store._ensure(conn)
        # 命中索引时不等会话元数据写锁，避免等待 JSON 写盘与文件替换重试。
        _, page = self._cached_page(conversation, limit=limit, before=before)
        if page is not None:
            return page
        # 构建与同进程持久化共用锁；跨进程以 source_key 校验，旧构建不能覆盖新索引。
        with session_service._metadata_lock(session_id):
            for _ in range(3):
                source, page = self._cached_page(conversation, limit=limit, before=before)
                if page is not None:
                    return page
                if source is None:
                    self._bootstrap_source(conversation)
                    continue
                source_key, _ = source
                session = session_service.read_for_audit(conversation)
                transcript = transcript_service.build_audit_transcript(session)
                encoded = [(session_id, position, history_store._encode(item)) for position, item in enumerate(transcript)]
                with store_service._connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    current = self._source(conn, session_id)
                    if current is None or current[0] != source_key:
                        continue
                    conn.execute("DELETE FROM audit_transcript_items WHERE session_id=?", (session_id,))
                    conn.executemany("INSERT INTO audit_transcript_items VALUES(?,?,?)", encoded)
                    conn.execute("""INSERT INTO audit_transcript_heads VALUES(?,?,?)
                        ON CONFLICT(session_id) DO UPDATE SET source_key=excluded.source_key,total=excluded.total""",
                        (session_id, source_key, len(transcript)))
                    page = self._page(conn, session_id, source_key, limit=limit, before=before)
                    return {**page, "metadata": current[1]}
        raise RuntimeError("会话正在持续更新，请稍后刷新审计详情")


audit_transcript_service = AuditTranscriptService()
