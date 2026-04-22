# backend/app/runtime/event_protocol.py
from app.schemas.agent import AgentEvent
from app.services.session_types import AgentSession


def make_event(session: AgentSession, event_type: str, **payload: object) -> AgentEvent:
    """构造统一的 SSE 事件。"""

    return AgentEvent(type=event_type, session_id=session.session_id, payload=payload)
