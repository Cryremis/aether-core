# backend/app/runtime/event_protocol.py
from app.schemas.agent import AgentEvent
from app.services.session_types import AgentSession


def make_event(session: AgentSession, event_type: str, **payload: object) -> AgentEvent:
    """构造统一的 SSE 事件。"""

    run = session.active_run
    committed = payload.get("message")
    message_id = committed.get("id") if isinstance(committed, dict) else run.message_id if run else None
    return AgentEvent(type=event_type, session_id=session.session_id, run_id=run.run_id if run else None,
        context_epoch_id=session.context_epoch, turn_id=f"{run.run_id}:turn:{run.turn_index}" if run and run.turn_index else None,
        step_id=run.step_id if run else None, attempt_id=run.attempt_id if run else None,
        message_id=message_id, part_id=str(payload["part_id"]) if payload.get("part_id") else None,
        continuation_of=run.continuation_of if run else None, payload=payload)
