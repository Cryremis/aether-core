from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.session_service import session_service
from app.services.session_types import AgentSession


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ToolExecutionRecord:
    execution_id: str
    session_id: str
    tool_call_id: str
    tool_name: str
    status: str
    created_at: str
    updated_at: str
    output_path: str
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    preview_text: str = ""
    exit_code: int | None = None
    started_at: str | None = None
    finished_at: str | None = None


@dataclass
class ToolOutputEvent:
    tool_call_id: str
    tool_name: str
    stream: str
    text: str
    seq: int
    timestamp: str


class ToolExecutionService:
    """Persist live tool output for UI streaming without polluting transcript history."""

    _PREVIEW_LIMIT = 4000

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._seq_by_tool_call: dict[str, int] = {}

    def begin_execution(self, session: AgentSession, *, tool_call_id: str, tool_name: str) -> ToolExecutionRecord:
        workspace = session.workspace
        if workspace is None:
            raise RuntimeError("会话工作区尚未初始化。")
        execution_id = f"exec_{uuid.uuid4().hex}"
        output_filename = f"tool_output_{tool_call_id}.log"
        output_path = workspace.logs_dir / output_filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8")
        record = ToolExecutionRecord(
            execution_id=execution_id,
            session_id=session.session_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status="running",
            created_at=_utcnow_iso(),
            updated_at=_utcnow_iso(),
            started_at=_utcnow_iso(),
            output_path=str(output_path.relative_to(workspace.root)),
        )
        executions = self._get_session_execution_map(session)
        executions[tool_call_id] = self._dump_record(record)
        self._seq_by_tool_call[tool_call_id] = 0
        session_service.persist(session)
        return record

    async def append_output(
        self,
        session: AgentSession,
        *,
        tool_call_id: str,
        tool_name: str,
        stream: str,
        text: str,
    ) -> ToolOutputEvent:
        if not text:
            raise RuntimeError("append_output requires non-empty text")
        async with self._lock:
            executions = self._get_session_execution_map(session)
            raw_record = executions.get(tool_call_id)
            if not isinstance(raw_record, dict):
                raise RuntimeError(f"tool execution not found: {tool_call_id}")
            record = self._load_record(raw_record)
            workspace = session.workspace
            if workspace is None:
                raise RuntimeError("会话工作区尚未初始化。")
            output_file = workspace.root / record.output_path
            output_file.parent.mkdir(parents=True, exist_ok=True)
            with output_file.open("a", encoding="utf-8", errors="replace") as handle:
                handle.write(text)
            if stream == "stderr":
                record.stderr_bytes += len(text.encode("utf-8", errors="replace"))
            else:
                record.stdout_bytes += len(text.encode("utf-8", errors="replace"))
            record.preview_text = self._merge_preview(record.preview_text, text)
            record.updated_at = _utcnow_iso()
            seq = self._seq_by_tool_call.get(tool_call_id, 0) + 1
            self._seq_by_tool_call[tool_call_id] = seq
            executions[tool_call_id] = self._dump_record(record)
            session_service.persist(session)
            return ToolOutputEvent(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                stream=stream,
                text=text,
                seq=seq,
                timestamp=_utcnow_iso(),
            )

    def finish_execution(
        self,
        session: AgentSession,
        *,
        tool_call_id: str,
        exit_code: int | None,
        final_output: Any,
        status: str = "completed",
    ) -> dict[str, Any] | None:
        executions = self._get_session_execution_map(session)
        raw_record = executions.get(tool_call_id)
        if not isinstance(raw_record, dict):
            return None
        record = self._load_record(raw_record)
        record.status = status
        record.exit_code = exit_code
        record.finished_at = _utcnow_iso()
        record.updated_at = record.finished_at
        executions[tool_call_id] = self._dump_record(record)
        self._seq_by_tool_call.pop(tool_call_id, None)

        history = self._get_session_execution_history(session)
        history.append(
            {
                "tool_call_id": tool_call_id,
                "tool_name": record.tool_name,
                "status": record.status,
                "started_at": record.started_at,
                "finished_at": record.finished_at,
                "exit_code": record.exit_code,
                "preview_text": record.preview_text,
                "output_path": record.output_path,
                "final_output": self._normalize_final_output(final_output),
            }
        )

        executions.pop(tool_call_id, None)
        session_service.persist(session)
        return history[-1]

    def fail_execution(
        self,
        session: AgentSession,
        *,
        tool_call_id: str,
        error: str,
    ) -> dict[str, Any] | None:
        return self.finish_execution(
            session,
            tool_call_id=tool_call_id,
            exit_code=None,
            final_output={"error": error, "summary": f"工具执行失败: {error}"},
            status="failed",
        )

    def abort_execution(
        self,
        session: AgentSession,
        *,
        tool_call_id: str,
    ) -> dict[str, Any] | None:
        return self.finish_execution(
            session,
            tool_call_id=tool_call_id,
            exit_code=None,
            final_output={"summary": "工具执行已停止", "aborted": True},
            status="aborted",
        )

    def snapshot_for_session(self, session: AgentSession) -> list[dict[str, Any]]:
        executions = self._get_session_execution_map(session)
        return [dict(item) for item in executions.values()]

    def read_active_output(self, session: AgentSession, *, tool_call_id: str) -> str:
        executions = self._get_session_execution_map(session)
        raw_record = executions.get(tool_call_id)
        if not isinstance(raw_record, dict):
            return ""
        record = self._load_record(raw_record)
        workspace = session.workspace
        if workspace is None:
            return ""
        output_file = workspace.root / record.output_path
        try:
            return output_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _get_session_execution_map(self, session: AgentSession) -> dict[str, Any]:
        metadata = session.context_state.setdefault("tool_execution_state", {})
        active = metadata.setdefault("active", {})
        if not isinstance(active, dict):
            metadata["active"] = {}
            active = metadata["active"]
        return active

    def _get_session_execution_history(self, session: AgentSession) -> list[dict[str, Any]]:
        metadata = session.context_state.setdefault("tool_execution_state", {})
        history = metadata.setdefault("history", [])
        if not isinstance(history, list):
            metadata["history"] = []
            history = metadata["history"]
        return history

    def _merge_preview(self, previous: str, text: str) -> str:
        merged = f"{previous}{text}"
        if len(merged) <= self._PREVIEW_LIMIT:
            return merged
        return merged[-self._PREVIEW_LIMIT :]

    def _dump_record(self, record: ToolExecutionRecord) -> dict[str, Any]:
        return {
            "execution_id": record.execution_id,
            "session_id": record.session_id,
            "tool_call_id": record.tool_call_id,
            "tool_name": record.tool_name,
            "status": record.status,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "output_path": record.output_path,
            "stdout_bytes": record.stdout_bytes,
            "stderr_bytes": record.stderr_bytes,
            "preview_text": record.preview_text,
            "exit_code": record.exit_code,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
        }

    def _load_record(self, raw: dict[str, Any]) -> ToolExecutionRecord:
        return ToolExecutionRecord(
            execution_id=str(raw.get("execution_id") or ""),
            session_id=str(raw.get("session_id") or ""),
            tool_call_id=str(raw.get("tool_call_id") or ""),
            tool_name=str(raw.get("tool_name") or ""),
            status=str(raw.get("status") or "running"),
            created_at=str(raw.get("created_at") or _utcnow_iso()),
            updated_at=str(raw.get("updated_at") or _utcnow_iso()),
            output_path=str(raw.get("output_path") or ""),
            stdout_bytes=int(raw.get("stdout_bytes") or 0),
            stderr_bytes=int(raw.get("stderr_bytes") or 0),
            preview_text=str(raw.get("preview_text") or ""),
            exit_code=int(raw["exit_code"]) if raw.get("exit_code") is not None else None,
            started_at=str(raw.get("started_at") or "") or None,
            finished_at=str(raw.get("finished_at") or "") or None,
        )

    def _normalize_final_output(self, value: Any) -> Any:
        if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
            return value
        try:
            json.dumps(value, ensure_ascii=False)
            return value
        except TypeError:
            return str(value)


tool_execution_service = ToolExecutionService()
