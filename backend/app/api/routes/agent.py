# backend/app/api/routes/agent.py
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import traceback

from app.runtime.engine import agent_engine
from app.schemas.agent import AgentChatRequest, AgentEvent
from app.services.session_service import session_service

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


@router.post("/chat")
async def chat(request: AgentChatRequest) -> StreamingResponse:
    """AetherCore 对话入口。"""

    session = session_service.get_or_create(request.session_id)

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
