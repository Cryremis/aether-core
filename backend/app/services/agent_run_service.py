from __future__ import annotations

import asyncio
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.runtime.engine import agent_engine
from app.runtime.event_protocol import make_event
from app.schemas.agent import AgentEvent
from app.services.session_service import session_service
from app.services.session_types import AgentSession
from app.services.tool_execution_service import tool_execution_service


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LiveRunState:
    run_id: str
    session_id: str
    task: asyncio.Task[None]
    subscribers: set[asyncio.Queue[AgentEvent | None]] = field(default_factory=set)
    history: list[AgentEvent] = field(default_factory=list)
    completed: bool = False


class AgentRunService:
    """Manage decoupled background agent runs and SSE subscribers."""

    _LIVE_OUTPUT_PREVIEW_LIMIT = 4000

    def __init__(self) -> None:
        self._runs: dict[str, LiveRunState] = {}
        self._session_runs: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def start_chat_run(
        self,
        session: AgentSession,
        message: str,
        *,
        replace_last_user_message: bool = False,
        client_message_id: str | None = None,
    ) -> str:
        async with self._lock:
            existing_run_id = self._session_runs.get(session.session_id)
            if existing_run_id and existing_run_id in self._runs:
                raise RuntimeError("当前会话已有执行中的任务，请等待当前任务结束后再继续。")

            run_id = f"run_{uuid.uuid4().hex}"
            started_at = _utcnow_iso()
            session.active_run_view = {
                "run_id": run_id,
                "session_id": session.session_id,
                "status": "running",
                "started_at": started_at,
                "updated_at": started_at,
                "assistant": {
                    "id": f"live-{run_id}",
                    "role": "assistant",
                    "blocks": [],
                    "elapsedMs": None,
                    "streaming": True,
                    "response_started_at": None,
                },
            }
            session_service.persist(session)

            task = asyncio.create_task(
                self._drive_run(
                    run_id,
                    session,
                    message,
                    replace_last_user_message=replace_last_user_message,
                    client_message_id=client_message_id,
                )
            )
            state = LiveRunState(run_id=run_id, session_id=session.session_id, task=task)
            self._runs[run_id] = state
            self._session_runs[session.session_id] = run_id
            return run_id

    async def subscribe(self, run_id: str, *, replay_history: bool = True) -> asyncio.Queue[AgentEvent | None]:
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                raise RuntimeError("目标运行不存在，可能已经结束。")
            queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
            state.subscribers.add(queue)
            history = list(state.history) if replay_history else []
        for item in history:
            await queue.put(item)
        return queue

    async def unsubscribe(self, run_id: str, queue: asyncio.Queue[AgentEvent | None]) -> None:
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return
            state.subscribers.discard(queue)

    def get_active_run_view(self, session: AgentSession) -> dict[str, Any] | None:
        return session.active_run_view

    def get_session_run_id(self, session_id: str) -> str | None:
        return self._session_runs.get(session_id)

    async def _drive_run(
        self,
        run_id: str,
        session: AgentSession,
        message: str,
        *,
        replace_last_user_message: bool = False,
        client_message_id: str | None = None,
    ) -> None:
        terminal_event_sent = False
        try:
            async for event in agent_engine.stream_chat(
                session,
                message,
                run_id=run_id,
                replace_last_user_message=replace_last_user_message,
                client_message_id=client_message_id,
            ):
                self._apply_event_to_active_view(session, event)
                await self._publish(run_id, event)
                if event.type == "completed":
                    terminal_event_sent = True
        except Exception as exc:  # noqa: BLE001
            error_event = make_event(
                session,
                "error",
                message=str(exc),
                traceback=traceback.format_exc(),
            )
            self._apply_event_to_active_view(session, error_event)
            await self._publish(run_id, error_event)
            completed_event = make_event(
                session,
                "completed",
                elapsed_ms=self._active_elapsed_ms(session),
                subtype="error",
            )
            self._apply_event_to_active_view(session, completed_event)
            await self._publish(run_id, completed_event)
            terminal_event_sent = True
        finally:
            if not terminal_event_sent:
                completed_event = make_event(
                    session,
                    "completed",
                    elapsed_ms=self._active_elapsed_ms(session),
                    subtype="completed",
                )
                self._apply_event_to_active_view(session, completed_event)
                await self._publish(run_id, completed_event)
            session.finish_run(run_id)
            session.active_run_view = None
            session_service.persist(session)
            await self._close_run(run_id, session.session_id)

    async def _publish(self, run_id: str, event: AgentEvent) -> None:
        subscribers: list[asyncio.Queue[AgentEvent | None]] = []
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return
            state.history.append(event)
            subscribers = list(state.subscribers)
            if event.type == "completed":
                state.completed = True
        for queue in subscribers:
            await queue.put(event)

    async def _close_run(self, run_id: str, session_id: str) -> None:
        async with self._lock:
            state = self._runs.pop(run_id, None)
            if self._session_runs.get(session_id) == run_id:
                self._session_runs.pop(session_id, None)
            subscribers = list(state.subscribers) if state is not None else []
        for queue in subscribers:
            await queue.put(None)

    def _active_elapsed_ms(self, session: AgentSession) -> int:
        active_view = session.active_run_view or {}
        assistant = active_view.get("assistant")
        started_at = assistant.get("response_started_at") if isinstance(assistant, dict) else None
        if not started_at:
            started_at = active_view.get("started_at")
        if not started_at:
            return 0
        try:
            started = datetime.fromisoformat(str(started_at))
        except ValueError:
            return 0
        return max(0, int((datetime.now(timezone.utc) - started).total_seconds() * 1000))

    def _apply_event_to_active_view(self, session: AgentSession, event: AgentEvent) -> None:
        active_view = session.active_run_view
        if not active_view:
            return
        assistant = active_view.get("assistant")
        if not isinstance(assistant, dict):
            return

        active_view["updated_at"] = _utcnow_iso()
        active_view["status"] = "completed" if event.type == "completed" else "running"

        payload = event.payload
        blocks = assistant.setdefault("blocks", [])
        if not isinstance(blocks, list):
            return

        if event.type == "assistant_visible_started":
            assistant["response_started_at"] = active_view.get("updated_at")
        elif event.type == "reasoning_delta":
            block = self._find_or_create_block(blocks, kind="reasoning", prefix="live-reasoning")
            block["content"] = f"{block.get('content', '')}{str(payload.get('delta') or '')}"
        elif event.type == "content_delta":
            block = self._find_or_create_block(blocks, kind="content", prefix="live-content")
            block["content"] = f"{block.get('content', '')}{str(payload.get('delta') or '')}"
            block["status"] = "streaming"
        elif event.type == "content_completed":
            for block in blocks:
                if isinstance(block, dict) and block.get("kind") == "content":
                    block["status"] = "done"
        elif event.type == "tool_started":
            tool_call_id = str(payload.get("id") or f"tool-{uuid.uuid4().hex}")
            tool_block = {
                "id": tool_call_id,
                "kind": "tool",
                "title": str(((payload.get("tool_display") or {}) if isinstance(payload.get("tool_display"), dict) else {}).get("title") or payload.get("tool_name") or "tool"),
                "meta": str(((payload.get("tool_display") or {}) if isinstance(payload.get("tool_display"), dict) else {}).get("meta") or "tool"),
                "argumentsText": self._pretty_json(payload.get("input")),
                "outputText": "",
                "liveOutputText": tool_execution_service.read_active_output(session, tool_call_id=tool_call_id),
                "status": "running",
            }
            blocks.append(tool_block)
        elif event.type == "tool_output_delta":
            tool_id = str(payload.get("id") or "")
            for block in blocks:
                if isinstance(block, dict) and block.get("kind") == "tool" and str(block.get("id")) == tool_id:
                    current_output = str(block.get("liveOutputText") or "")
                    block["liveOutputText"] = self._append_live_output_preview(
                        current_output,
                        str(payload.get("text") or ""),
                    )
                    break
        elif event.type == "tool_finished":
            tool_id = str(payload.get("id") or "")
            for block in blocks:
                if isinstance(block, dict) and block.get("kind") == "tool" and str(block.get("id")) == tool_id:
                    block["outputText"] = self._pretty_json(payload.get("output"))
                    block["status"] = "aborted" if self._tool_output_aborted(payload.get("output")) else "done"
                    block.pop("liveOutputText", None)
                    break
        elif event.type == "runtime_recreated":
            blocks.append(
                {
                    "id": f"runtime-notice-{uuid.uuid4().hex}",
                    "kind": "runtime_notice",
                    "eventType": "runtime_recreated",
                    "title": "沙箱已重建",
                    "detail": str(payload.get("reason") or ""),
                }
            )
        elif event.type == "aborted":
            partial_content = str(payload.get("partial_content") or "")
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("kind") == "content" and str(block.get("status")) == "streaming":
                    if partial_content:
                        block["content"] = partial_content
                    block["status"] = "aborted"
                if block.get("kind") == "tool" and str(block.get("status")) == "running":
                    block["status"] = "aborted"
                    block["outputText"] = self._pretty_json({"summary": "工具执行已停止", "aborted": True})
                    block.pop("liveOutputText", None)
        elif event.type == "completed":
            assistant["streaming"] = False
            assistant["elapsedMs"] = int(payload.get("elapsed_ms") or self._active_elapsed_ms(session))

        session_service.persist(session)

    def _find_or_create_block(self, blocks: list[dict[str, Any]], *, kind: str, prefix: str) -> dict[str, Any]:
        for block in reversed(blocks):
            if isinstance(block, dict) and block.get("kind") == kind:
                return block
        block = {
            "id": f"{prefix}-{uuid.uuid4().hex}",
            "kind": kind,
            "content": "",
        }
        if kind == "content":
            block["status"] = "streaming"
        blocks.append(block)
        return block

    def _pretty_json(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        try:
            import json

            return json.dumps(value, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            return str(value)

    def _tool_output_aborted(self, value: Any) -> bool:
        return isinstance(value, dict) and value.get("aborted") is True

    def _append_live_output_preview(self, current_output: str, delta: str) -> str:
        merged = f"{current_output}{delta}"
        if len(merged) <= self._LIVE_OUTPUT_PREVIEW_LIMIT:
            return merged
        return merged[-self._LIVE_OUTPUT_PREVIEW_LIMIT :]


agent_run_service = AgentRunService()
