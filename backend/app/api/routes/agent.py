# backend/app/api/routes/agent.py
import traceback

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.deps import AuthContext, get_auth_context
from app.runtime.engine import agent_engine
from app.schemas.agent import AgentChatRequest, AgentEvent
from app.services.session_service import session_service
from app.services.store import store_service

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


def _ensure_session_access(session_id: str, auth: AuthContext):
    conversation = store_service.get_conversation_by_session(session_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if auth.kind == "admin":
        if auth.user is None or conversation.get("owner_user_id") != auth.user.user_id:
            raise HTTPException(status_code=403, detail="无权访问该会话")
    elif auth.kind == "embed":
        if (
            conversation.get("conversation_id") != auth.conversation_id
            or conversation.get("platform_id") != auth.platform_id
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

    async def event_stream():
        yield f"data: {AgentEvent(type='session_created', session_id=session.session_id).model_dump_json()}\n\n"
        try:
            async for event in agent_engine.stream_chat(session, request.message):
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
