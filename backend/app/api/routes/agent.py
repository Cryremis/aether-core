# backend/app/api/routes/agent.py
import traceback

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.deps import AuthContext, get_auth_context
from app.runtime.engine import agent_engine
from app.schemas.agent import AgentChatRequest, AgentElicitationResponseRequest, AgentEvent
from app.services.agent_run_service import agent_run_service
from app.services.runtime_state import runtime_state_service
from app.services.session_service import session_service
from app.services.store import store_service

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
                run_id = await agent_run_service.start_chat_run(session, request.message)
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

    async def event_stream():
        yield f"data: {AgentEvent(type='session_created', session_id=session.session_id).model_dump_json()}\n\n"
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
            async for event in agent_engine.stream_chat(session, resume_message):
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
