from __future__ import annotations

import asyncio
import logging
import traceback
import uuid
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.runtime.engine import agent_engine
from app.runtime.event_protocol import make_event
from app.schemas.agent import AgentEvent
from app.services.session_service import session_service
from app.services.session_types import AgentSession
from app.services.store import store_service
from app.services.tool_execution_service import tool_execution_service
from app.services.transcript_service import transcript_service


logger = logging.getLogger(__name__)


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
        reasoning_effort: str | None = None,
        parent_run_id: str | None = None,
        agent_kind: str = "main",
        idempotency_key: str | None = None,
    ) -> str:
        async with self._lock:
            existing_run_id = self._session_runs.get(session.session_id)
            if existing_run_id and existing_run_id in self._runs:
                raise RuntimeError("当前会话已有执行中的任务，请等待当前任务结束后再继续。")
            # 内存状态只覆盖当前进程；数据库状态是跨进程、跨重启的最终并发护栏。
            if store_service.get_active_agent_run_for_session(session.session_id) is not None:
                raise RuntimeError("当前会话已有执行中的任务，请等待当前任务结束后再继续。")

            run_id = f"run_{uuid.uuid4().hex}"
            started_at = _utcnow_iso()
            store_service.create_agent_run(
                run_id=run_id,
                session_id=session.session_id,
                conversation_id=session.conversation_id,
                parent_run_id=parent_run_id,
                agent_kind=agent_kind,
                status="running",
                request={
                    "message": message,
                    "client_message_id": client_message_id,
                    "reasoning_effort": reasoning_effort,
                    "idempotency_key": idempotency_key,
                },
                idempotency_key=idempotency_key,
            )
            if session.conversation_id:
                store_service.set_conversation_last_run(session.conversation_id, run_id)
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
                    reasoning_effort=reasoning_effort,
                )
            )
            state = LiveRunState(run_id=run_id, session_id=session.session_id, task=task)
            self._runs[run_id] = state
            self._session_runs[session.session_id] = run_id
            return run_id

    async def subscribe(self, run_id: str, *, replay_history: bool = True) -> asyncio.Queue[AgentEvent | None]:
        async with self._lock:
            state = self._runs.get(run_id)
            if state is not None:
                queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
                state.subscribers.add(queue)
                history = list(state.history) if replay_history else []
                for item in history:
                    await queue.put(item)
                return queue

        run = store_service.get_agent_run(run_id)
        if run is None:
            raise RuntimeError("目标运行不存在。")
        queue = asyncio.Queue()
        if replay_history:
            for row in store_service.list_agent_run_events(run_id, limit=100_000):
                await queue.put(
                    AgentEvent(
                        type=str(row["type"]),
                        session_id=str(run["session_id"]),
                        run_id=run_id,
                        seq=int(row["seq"]),
                        timestamp=str(row["created_at"]),
                        payload=dict(row.get("payload") or {}),
                    )
                )
        await queue.put(None)
        return queue

    async def publish_event(self, run_id: str, event: AgentEvent) -> None:
        await self._publish(run_id, event)

    async def wait_for_run(self, run_id: str) -> dict[str, Any]:
        state = self._runs.get(run_id)
        if state is not None and state.task is not None and not state.task.done():
            await state.task
        return self.get_run_view(run_id) or {}

    async def cancel_run(
        self,
        session: AgentSession,
        run_id: str,
        *,
        grace_seconds: float = 5.0,
    ) -> dict[str, Any]:
        """请求取消并等待 run 进入可持久化的终态。"""
        state = self._runs.get(run_id)
        run = self.get_run_view(run_id)
        if run and str(run.get("status")) not in {"queued", "running", "waiting_input"}:
            return run
        if state is None or state.task is None or state.task.done():
            if run is not None:
                cancelled_run = store_service.cancel_orphan_agent_run(run_id)
                if cancelled_run is None:
                    return self.get_run_view(run_id)
                return self.get_run_view(str(cancelled_run["run_id"]))
            return {"run_id": run_id, "status": "failed", "error": {"message": "运行任务不存在"}}

        session.request_abort()
        tool_task = session.get_tool_task(run_id)
        if isinstance(tool_task, asyncio.Future) and not tool_task.done():
            tool_task.cancel()

        try:
            await asyncio.wait_for(asyncio.shield(state.task), timeout=grace_seconds)
        except asyncio.TimeoutError:
            state.task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(state.task), timeout=1.0)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                logger.exception("Agent run %s did not finish after forced cancellation", run_id)
        except asyncio.CancelledError:
            pass
        return self.get_run_view(run_id) or {"run_id": run_id, "status": "failed"}

    def request_cancel(self, session: AgentSession) -> str | None:
        """立即发起协作式取消，不等待工具清理完成。"""
        run_id = session.current_run_id()
        if run_id is None:
            return None
        session.request_abort()
        tool_task = session.get_tool_task(run_id)
        if isinstance(tool_task, asyncio.Future) and not tool_task.done():
            tool_task.cancel()
        return run_id

    def get_run_view(self, run_id: str) -> dict[str, Any] | None:
        run = store_service.get_agent_run(run_id)
        if run is None:
            return None
        run["request"] = json.loads(run.pop("request_json") or "{}")
        run["result"] = json.loads(run["result_json"]) if run.get("result_json") else None
        run.pop("result_json", None)
        run["error"] = json.loads(run["error_json"]) if run.get("error_json") else None
        run.pop("error_json", None)
        return run

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
        reasoning_effort: str | None = None,
    ) -> None:
        terminal_event_sent = False
        try:
            async for event in agent_engine.stream_chat(
                session,
                message,
                run_id=run_id,
                replace_last_user_message=replace_last_user_message,
                client_message_id=client_message_id,
                reasoning_effort=reasoning_effort,
            ):
                self._apply_event_to_active_view(session, event)
                await self._publish(run_id, event)
                if event.type == "completed":
                    terminal_event_sent = True
        except asyncio.CancelledError:
            aborted_event = make_event(
                session,
                "aborted",
                partial_content=session.get_partial_content(run_id),
                interrupt_point="run_task_cancelled",
            )
            self._apply_event_to_active_view(session, aborted_event)
            await self._publish(run_id, aborted_event)
            completed_event = make_event(
                session,
                "completed",
                elapsed_ms=self._active_elapsed_ms(session),
                subtype="aborted",
            )
            self._apply_event_to_active_view(session, completed_event)
            await self._publish(run_id, completed_event)
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
            try:
                if not terminal_event_sent:
                    completed_event = make_event(
                        session,
                        "completed",
                        elapsed_ms=self._active_elapsed_ms(session),
                        subtype="completed",
                    )
                    self._apply_event_to_active_view(session, completed_event)
                    await self._publish(run_id, completed_event)
            except Exception:
                logger.exception("Failed to publish terminal event for agent run %s", run_id)
                try:
                    store_service.fail_agent_run(
                        run_id,
                        message="任务终态发布失败，已自动收敛为 failed",
                        interrupt_point="terminal_publish_failed",
                    )
                except Exception:
                    logger.exception("Failed to finalize agent run %s in database", run_id)
            finally:
                try:
                    self._finalize_transcript(session)
                except Exception:
                    logger.exception("Failed to finalize transcript for agent run %s", run_id)
                finally:
                    session.finish_run(run_id)
                    session.active_run_view = None
                    try:
                        session_service.persist(session)
                    except Exception:
                        logger.exception("Failed to persist session after agent run %s", run_id)
                    finally:
                        final_run = store_service.get_agent_run(run_id)
                        if str((final_run or {}).get("status")) in {"failed", "cancelled", "timed_out"}:
                            from app.services.subagent_service import subagent_service

                            try:
                                await asyncio.wait_for(
                                    subagent_service.cancel_for_parent_run(run_id, session.session_id),
                                    timeout=5.0,
                                )
                            except Exception:
                                logger.exception(
                                    "Failed to cancel subagents for parent agent run %s",
                                    run_id,
                                )
                        await self._close_run(run_id, session.session_id)

    async def _publish(self, run_id: str, event: AgentEvent) -> None:
        subscribers: list[asyncio.Queue[AgentEvent | None]] = []
        event.run_id = run_id
        status = self._status_for_event(event)
        result = event.payload.get("result") if event.type == "result" else None
        error = (
            {
                "message": event.payload.get("message"),
                "traceback": event.payload.get("traceback"),
            }
            if event.type == "error"
            else None
        )
        event.seq = store_service.append_agent_run_event(
            run_id=run_id,
            event_type=event.type,
            payload=event.payload,
            status=status,
            result=result,
            error=error,
        )
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

    @staticmethod
    def _status_for_event(event: AgentEvent) -> str | None:
        if event.type != "completed":
            if event.type == "aborted":
                return "cancelling"
            return None
        subtype = str(event.payload.get("subtype") or "")
        if subtype == "success":
            return "completed"
        if subtype == "awaiting_user_input":
            return "waiting_input"
        if subtype == "aborted":
            return "cancelled"
        if subtype.startswith("error"):
            return "failed"
        return "completed"

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
        if event.type == "result":
            active_view["result"] = event.payload.get("result")
            active_view["is_error"] = bool(event.payload.get("is_error"))

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

    def _finalize_transcript(self, session: AgentSession) -> None:
        # transcript 是历史真相，只能从正式 messages 投影生成；
        # active_run_view 只是运行态快照，不能回写成 persisted transcript。
        session.transcript = transcript_service.build_persisted_transcript(session.messages)

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
