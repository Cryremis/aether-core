# backend/app/api/routes/agent.py
import traceback

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.deps import AuthContext, get_auth_context
from app.runtime.engine import agent_engine
from app.schemas.agent import AgentChatRequest, AgentElicitationResponseRequest, AgentEvent
from app.schemas.session import TimelineEditRequest, TimelineForkRequest, TimelineRerunRequest
from app.services.agent_run_service import agent_run_service
from app.services.context.message_adapter import context_message_adapter
from app.services.runtime_state import runtime_state_service
from app.services.session_service import session_service
from app.services.store import store_service
from app.services.timeline_service import timeline_service

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


class AbortRequest:
    pass


class AbortResponse:
    success: bool
    partial_content: str | None


def _ensure_session_access(session_id: str, auth: AuthContext):
    conversation = store_service.get_conversation_by_session(session_id)
    if conversation is None:
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


@router.post("/chat")
async def chat(request: AgentChatRequest, auth: AuthContext = Depends(get_auth_context)) -> StreamingResponse:
    """AetherCore 对话入口。"""

    if not request.session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id")
    session = _ensure_session_access(request.session_id, auth)
    if request.allow_network is not None:
        session_service.set_allow_network(session, request.allow_network)

    async def event_stream():
        yield f"data: {AgentEvent(type='session_created', session_id=session.session_id).model_dump_json()}\n\n"
        try:
            if request.run_id:
                run_id = request.run_id
            else:
                run_id = await agent_run_service.start_chat_run(
                    session,
                    request.message,
                    replace_last_user_message=request.replace_last_user_message,
                    client_message_id=request.client_message_id,
                )
                started_event = AgentEvent(
                    type="run_started",
                    session_id=session.session_id,
                    payload={"run_id": run_id},
                )
                yield f"data: {started_event.model_dump_json()}\n\n"
            queue = await agent_run_service.subscribe(run_id)
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield f"data: {event.model_dump_json()}\n\n"
            finally:
                await agent_run_service.unsubscribe(run_id, queue)
        except Exception as exc:  # noqa: BLE001
            error_event = AgentEvent(
                type="error",
                session_id=session.session_id,
                payload={"message": str(exc), "traceback": traceback.format_exc()},
            )
            yield f"data: {error_event.model_dump_json()}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/{session_id}/abort")
async def abort_session(session_id: str, auth: AuthContext = Depends(get_auth_context)):
    """中断当前回合，保存已生成的内容。"""
    session = _ensure_session_access(session_id, auth)
    run_id = session.request_abort()
    partial_content = session.get_partial_content(run_id) if run_id else ""
    return {"success": True, "partial_content": partial_content}


@router.post("/{session_id}/elicitation/{request_id}/respond")
async def respond_to_elicitation(
    session_id: str,
    request_id: str,
    request: AgentElicitationResponseRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> StreamingResponse:
    session = _ensure_session_access(session_id, auth)
    elicitation_state, resume_message = runtime_state_service.resolve_elicitation(
        session,
        request_id,
        [item.model_dump(mode="json") for item in request.responses],
    )
    # 将用户对 request_user_input 的结构化回答持久化到会话消息，保证刷新后仍按同样样式渲染。
    resolved_request = next((item for item in reversed(elicitation_state.history) if item.id == request_id), None)
    if resolved_request is not None:
        summary = (
            "已提交给 AI，接下来会按你的回答继续执行"
            if resolved_request.blocking
            else "已提交给 AI，等待后续处理"
        )
        answers: list[dict[str, str]] = []
        answer_by_question_id = {item.question_id: item for item in resolved_request.answers}
        for question in resolved_request.questions:
            answer = answer_by_question_id.get(question.id)
            parts: list[str] = []
            if answer is not None:
                parts.extend([str(item) for item in answer.selected_options if str(item).strip()])
                if answer.other_text:
                    parts.append(str(answer.other_text))
                if answer.notes:
                    parts.append(f"说明：{answer.notes}")
            answers.append(
                {
                    "id": question.id,
                    "header": question.header,
                    "value": "、".join(parts) if parts else "未填写",
                }
            )
        turn_index = max(int(message.get("turn_index", 0)) for message in session.messages) if session.messages else 0
        committed_message = context_message_adapter.make_elicitation_response_message(
            request_id=request_id,
            title=resolved_request.title,
            summary=summary,
            answers=answers,
            turn_index=turn_index + 1,
        )
        session.messages.append(committed_message)
        session.touch()
        session_service.persist(session)
    else:
        committed_message = None

    async def event_stream():
        yield f"data: {AgentEvent(type='session_created', session_id=session.session_id).model_dump_json()}\n\n"
        if committed_message is not None:
            yield (
                "data: "
                + AgentEvent(
                    type="message_committed",
                    session_id=session.session_id,
                    payload={
                        "message": {
                            "id": str(committed_message.get("message_id") or ""),
                            "role": "elicitation_response",
                            "request_id": str(committed_message.get("request_id") or ""),
                            "title": str(committed_message.get("title") or ""),
                            "summary": str(committed_message.get("summary") or ""),
                            "answers": committed_message.get("answers") or [],
                        },
                        "client_message_id": request.client_message_id,
                    },
                ).model_dump_json()
                + "\n\n"
            )
        yield (
            "data: "
            + AgentEvent(
                type="ask_resolved",
                session_id=session.session_id,
                payload={
                    "request_id": request_id,
                    "snapshot": elicitation_state.model_dump(mode="json"),
                    "resume_message": resume_message,
                },
            ).model_dump_json()
            + "\n\n"
        )
        try:
            async for event in agent_engine.stream_chat(
                session,
                resume_message,
                persist_user_message=True,
                visible_user_message=False,
                user_message_kind="elicitation_resume",
            ):
                yield f"data: {event.model_dump_json()}\n\n"
        except Exception as exc:  # noqa: BLE001
            error_event = AgentEvent(
                type="error",
                session_id=session.session_id,
                payload={"message": str(exc), "traceback": traceback.format_exc()},
            )
            yield f"data: {error_event.model_dump_json()}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


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
) -> StreamingResponse:
    session = _ensure_session_access(session_id, auth)

    async def event_stream():
        yield f"data: {AgentEvent(type='session_created', session_id=session.session_id).model_dump_json()}\n\n"
        try:
            queue = await agent_run_service.subscribe(run_id, replay_history=False)
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield f"data: {event.model_dump_json()}\n\n"
            finally:
                await agent_run_service.unsubscribe(run_id, queue)
        except Exception as exc:  # noqa: BLE001
            error_event = AgentEvent(
                type="error",
                session_id=session.session_id,
                payload={"message": str(exc), "traceback": traceback.format_exc()},
            )
            yield f"data: {error_event.model_dump_json()}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
