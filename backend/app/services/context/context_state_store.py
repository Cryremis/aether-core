# backend/app/services/context/context_state_store.py
from __future__ import annotations

from app.services.context.runtime_types import ContextSessionState

if False:  # pragma: no cover
    from app.services.session_service import AgentSession


class ContextStateStore:
    """Persist context-management state with the session metadata."""

    def get(self, session: AgentSession) -> ContextSessionState:
        return ContextSessionState.from_dict(session.session_id, session.context_state)

    def save(self, session: AgentSession, state: ContextSessionState) -> None:
        from app.services.session_service import session_service

        session.context_state = state.to_dict()
        session_service.persist(session)

    def reset(self, session: AgentSession) -> ContextSessionState:
        state = ContextSessionState(session_id=session.session_id)
        self.save(session, state)
        return state


context_state_store = ContextStateStore()
