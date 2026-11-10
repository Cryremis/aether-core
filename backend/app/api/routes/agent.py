# backend/app/api/routes/agent.py
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.api.deps import AuthContext, get_auth_context
from app.core.model_errors import public_run_error
from app.schemas.common import ApiResponse
from app.schemas.agent import AgentChatRequest, AgentElicitationResponseRequest, AgentEvent
from app.schemas.session import TimelineEditRequest, TimelineForkRequest, TimelineRerunRequest
from app.services.agent_run_service import AgentRunBusy, ElicitationResumeConflict, agent_run_service
from app.services.context.message_adapter import context_message_adapter
from app.services.runtime_state import runtime_state_service
from app.services.session_service import session_service
from app.services.session_inspection import inspect_context, inspect_tools
from app.services.durable_io import durable_io, finish_on_cancel
from app.services.store import store_service
from app.services.timeline_service import timeline_service
from app.services.transcript_service import transcript_service

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])
logger = logging.getLogger(__name__)


def _sse(event: AgentEvent) -> str:
    if event.type == "error":
        # 历史错误事件也经过此边界，旧数据库中的堆栈不再透传给终端用户。
        event = event.model_copy(update={"payload": {
            "message": public_run_error(), "error_id": event.run_id, "error_code": event.payload.get("error_code"),
        }})
    lines = [f"event: {event.type}"]
    if event.seq is not None:
        lines.append(f"id: {event.seq}")
    lines.append(f"data: {event.model_dump_json()}")
    return "\n".join(lines) + "\n\n"


def _run_stream(session, run_id: str, after_seq: int, *, leading=()) -> StreamingResponse:
    async def event_stream():
        yield _sse(AgentEvent(type="session_created", session_id=session.session_id, run_id=run_id,
                              payload={"resume_after_seq": after_seq}))
        for event in leading:
            yield _sse(event)
        try:
            queue, live = await agent_run_service.subscribe_from(run_id, after_seq=after_seq)
            try:
                cursor = after_seq
                events = await durable_io(agent_run_service.list_events_after, run_id, cursor)
                current_run = store_service.get_agent_run(run_id) or {}
                current_run["event_cursor"] = max(
                    int(current_run.get("event_cursor") or 0), int(events[-1].seq or 0) if events else 0,
                )
                snapshot = agent_run_service.get_answerable_elicitation(session)
                yield _sse(AgentEvent(type="elicitation_snapshot", session_id=session.session_id,
                                      payload={"snapshot": snapshot.model_dump(mode="json")}))
                for event in events:
                    cursor = int(event.seq or cursor)
                    historical_pause = agent_run_service.is_historical_pause(event.type, event.payload, cursor, current_run)
                    if historical_pause:
                        event = event.model_copy(update={"payload": {**event.payload, "historical_pause": True}})
                    yield _sse(event)
                    if event.type == "completed" and not historical_pause:
                        return
                if not live:
                    if cursor < current_run["event_cursor"]:
                        # 无实时订阅且历史超出单批上限，客户端按当前游标取下一批。
                        return
                    # 游标已在一个稳定边界之后时，给客户端确认，不重复发送已消费的事件。
                    if current_run.get("status") in {"waiting_input", "completed", "failed", "cancelled", "timed_out"}:
                        yield _sse(AgentEvent(type="stream_end", session_id=session.session_id,
                                              payload={"status": current_run["status"]}))
                    return
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except TimeoutError:
                        yield ": heartbeat\n\n"
                        continue
                    if event is None:
                        return
                    seq = int(event.seq or 0)
                    if seq <= cursor:
                        continue
                    cursor = seq
                    yield _sse(event)
                    if event.type == "completed":
                        return
            finally:
                await agent_run_service.unsubscribe(run_id, queue)
        except Exception:
            # 有 run ID 时让客户端按最后确认的游标续订；传输故障不是业务终态。
            logger.exception("Failed to stream agent run %s after %s", run_id, after_seq)

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


class AbortRequest:
    pass


class AbortResponse:
    success: bool
    partial_content: str | None


def _ensure_session_access(session_id: str, auth: AuthContext):
    conversation = store_service.get_conversation_by_session(session_id)
    if conversation is None or conversation.get("deleted_at") is not None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if auth.kind == "user":
        if auth.user is None or conversation.get("owner_user_id") != auth.user.user_id:
            raise HTTPException(status_code=403, detail="无权访问该会话")
    elif auth.kind == "embed":
        if (
            conversation.get("platform_id") != auth.platform_id
            or conversation.get("external_user_id") != auth.external_user_id
        ):
            raise HTTPException(status_code=403, detail="无权访问该会话")
    else:
        raise HTTPException(status_code=401, detail="未授权")
    return session_service.get_or_create(session_id)


def _ensure_session_mutable(session_id: str) -> None:
    if agent_run_service.get_session_run_id(session_id):
        raise HTTPException(status_code=409, detail="当前会话正在执行，请等待当前任务结束后再操作。")


@router.get("/{session_id}/context/status")
def context_status(session_id: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    return ApiResponse(data=inspect_context(_ensure_session_access(session_id, auth)))


@router.get("/{session_id}/tools")
def effective_tools(session_id: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    return ApiResponse(data=inspect_tools(_ensure_session_access(session_id, auth)))


@router.get("/{session_id}/context/diagnostics")
async def context_diagnostics(session_id: str, limit: int = Query(50, ge=1, le=100),
                              before: int | None = Query(None, ge=1),
                              auth: AuthContext = Depends(get_auth_context)) -> dict:
    _ensure_session_access(session_id, auth)
    from app.services.context.history_store import history_store
    return history_store.diagnostics(session_id, limit=limit, before=before)


@router.post("/chat")
async def chat(request: AgentChatRequest, auth: AuthContext = Depends(get_auth_context)) -> StreamingResponse:
    """AetherCore 对话入口。"""

    if not request.session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id")
    session = _ensure_session_access(request.session_id, auth)
    if request.run_id:
        existing_run = store_service.get_agent_run(request.run_id)
        if existing_run is None or str(existing_run["session_id"]) != session.session_id:
            raise HTTPException(status_code=404, detail="运行不存在于当前会话")
    session.disabled_capability_ids = list(dict.fromkeys(request.disabled_capability_ids))[:512]
    if request.allow_network is not None:
        session_service.set_allow_network(session, request.allow_network)

    if request.run_id:
        return _run_stream(session, request.run_id, 0)
    try:
        run_id = await agent_run_service.start_chat_run(
            session, request.message, replace_last_user_message=request.replace_last_user_message,
            client_message_id=request.client_message_id, reasoning_effort=request.reasoning_effort,
            emit_started=True,
        )
    except AgentRunBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _run_stream(session, run_id, 0)


@router.post("/{session_id}/abort")
async def abort_session(session_id: str, auth: AuthContext = Depends(get_auth_context)):
    """中断当前回合，保存已生成的内容。"""
    session = _ensure_session_access(session_id, auth)
    try:
        return await agent_run_service.abort_session(session)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{session_id}/elicitation/{request_id}/respond")
async def respond_to_elicitation(
    session_id: str,
    request_id: str,
    request: AgentElicitationResponseRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> StreamingResponse:
    session = _ensure_session_access(session_id, auth)
    def resolved_event(state, *, resume_message="", no_run=False):
        return AgentEvent(
            type="ask_resolved", session_id=session.session_id,
            payload={"request_id": request_id, "snapshot": state.model_dump(mode="json"),
                     "resume_message": resume_message, "no_run": no_run},
        )

    def committed_event(message):
        return AgentEvent(type="message_committed", session_id=session.session_id, payload={
            "message": {
                "id": message["message_id"], "role": "elicitation_response",
                "request_id": request_id, "title": message["title"], "summary": message["summary"],
                "answers": message["answers"],
            },
            "client_message_id": request.client_message_id,
        })

    def replay_answer(state):
        resolved = next((item for item in reversed(state.history)
                         if item.id == request_id and item.status == "resolved"), None)
        if resolved is None:
            return None
        if resolved.resume_run_id:
            message = next((item for item in reversed(session.messages)
                            if item.get("role") == "elicitation_response" and item.get("request_id") == request_id), None)
            leading = [committed_event(message)] if message is not None else []
            leading.append(resolved_event(state))
            return _run_stream(session, resolved.resume_run_id, resolved.resume_after_seq or 0,
                               leading=leading)

        async def legacy_acknowledgement():
            yield _sse(AgentEvent(type="session_created", session_id=session.session_id))
            yield _sse(resolved_event(state, no_run=True))

        return StreamingResponse(legacy_acknowledgement(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    current = runtime_state_service.get_elicitation(session)
    duplicate = replay_answer(current)
    if duplicate is not None:
        return duplicate
    if current.pending is None or current.pending.id != request_id:
        if any(item.id == request_id for item in current.history):
            raise HTTPException(status_code=409, detail="该问题已停止或超时。可发送新消息继续。")
        raise HTTPException(status_code=404, detail="该问题不存在。请刷新会话。")
    try:
        state, resume_message = runtime_state_service.resolve_elicitation(
            session, request_id, [item.model_dump(mode="json") for item in request.responses], persist=False,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    resolved = state.history[-1]
    committed_message = None

    def verify_pending():
        latest = runtime_state_service.get_elicitation(session)
        if latest.revision != current.revision or latest.pending is None or latest.pending.id != request_id:
            raise ElicitationResumeConflict("问题状态已变化，请刷新会话。")

    def commit_answer(run_id, after_seq):
        nonlocal committed_message
        resolved.resume_run_id = run_id
        resolved.resume_after_seq = after_seq
        answers = []
        by_question = {answer.question_id: answer for answer in resolved.answers}
        for question in resolved.questions:
            answer = by_question[question.id]
            parts = list(answer.selected_options)
            if answer.other_text:
                parts.append(answer.other_text)
            if answer.notes:
                parts.append(f"说明：{answer.notes}")
            answers.append({"id": question.id, "header": question.header, "value": "、".join(parts) or "未填写"})
        turn_index = max((int(message.get("turn_index", 0)) for message in session.messages), default=0)
        committed_message = context_message_adapter.make_elicitation_response_message(
            request_id=request_id, title=resolved.title, summary="已提交给 AI，接下来会按你的回答继续执行",
            answers=answers, turn_index=turn_index + 1,
        )
        session.messages.append(committed_message)
        session.transcript = transcript_service.append_item(
            session.transcript, transcript_service.transcript_item_from_committed_message(committed_message),
        )
        runtime_state_service.save_elicitation(session, state)

    try:
        run_id, resume_seq = await finish_on_cancel(agent_run_service.resume_elicitation_run(
            session, resume_message, request_id=request_id, client_message_id=request.client_message_id,
            blocking=resolved.blocking, before_admission=verify_pending, on_admitted=commit_answer,
        ))
    except AgentRunBusy as exc:
        duplicate = replay_answer(agent_run_service.get_answerable_elicitation(session))
        if duplicate is not None:
            return duplicate
        detail = str(exc) if isinstance(exc, ElicitationResumeConflict) else "当前任务仍在执行。答案尚未提交，请在任务结束后重试。"
        raise HTTPException(status_code=409, detail=detail) from exc

    leading = [committed_event(committed_message)] if committed_message is not None else []
    leading.append(resolved_event(state, resume_message=resume_message))
    return _run_stream(session, run_id, max(0, resume_seq - 1), leading=leading)


@router.get("/{session_id}/elicitation")
def get_elicitation(session_id: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    session = _ensure_session_access(session_id, auth)
    return ApiResponse(data=agent_run_service.get_answerable_elicitation(session).model_dump(mode="json"))


@router.post("/{session_id}/timeline/fork")
def fork_session_timeline(
    session_id: str,
    request: TimelineForkRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    _ensure_session_mutable(session_id)
    session = _ensure_session_access(session_id, auth)
    conversation = store_service.get_conversation_by_session(session_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        result = timeline_service.fork_from_message(
            source_session=session,
            source_conversation=conversation,
            source_message_id=request.message_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "success": True,
        "session_id": result.session.session_id,
        "conversation_id": result.session.conversation_id,
        "source_message_id": result.source_message_id,
    }


@router.post("/{session_id}/timeline/rerun")
def rerun_session_timeline(
    session_id: str,
    request: TimelineRerunRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    _ensure_session_mutable(session_id)
    session = _ensure_session_access(session_id, auth)
    try:
        result = timeline_service.rerun_from_message(
            session=session,
            source_message_id=request.message_id,
            edited_content=None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "success": True,
        "rerun_prompt": result.rerun_prompt,
        "source_message_id": result.source_message_id,
        "anchor_message_id": result.anchor_message_id,
        "rerun_message_id": result.rerun_message_id,
        "truncated_count": result.truncated_count,
    }


@router.post("/{session_id}/timeline/edit")
def edit_session_timeline(
    session_id: str,
    request: TimelineEditRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    _ensure_session_mutable(session_id)
    session = _ensure_session_access(session_id, auth)
    try:
        result = timeline_service.rerun_from_message(
            session=session,
            source_message_id=request.message_id,
            edited_content=request.content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "success": True,
        "rerun_prompt": result.rerun_prompt,
        "source_message_id": result.source_message_id,
        "anchor_message_id": result.anchor_message_id,
        "rerun_message_id": result.rerun_message_id,
        "truncated_count": result.truncated_count,
    }


@router.get("/{session_id}/runs/{run_id}/events")
async def stream_run_events(
    session_id: str,
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    after_seq: int = Query(default=0, ge=0),
) -> StreamingResponse:
    session = _ensure_session_access(session_id, auth)
    run = store_service.get_agent_run(run_id)
    if run is None or str(run["session_id"]) != session_id:
        raise HTTPException(status_code=404, detail="运行不存在于当前会话")

    return _run_stream(session, run_id, after_seq)
