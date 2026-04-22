# backend/app/services/context/context_state_store.py
"""上下文状态存取层。"""

from __future__ import annotations

from app.services.context.contracts import SessionPersister
from app.services.context.runtime_types import ContextSessionState
from app.services.session_types import AgentSession


class ContextStateStore:
    """将上下文状态映射并回写到会话元数据。"""

    def __init__(self, persister: SessionPersister | None = None):
        self._persister = persister

    def bind_persister(self, persister: SessionPersister) -> None:
        self._persister = persister

    def get(self, session: AgentSession) -> ContextSessionState:
        return ContextSessionState.from_dict(session.session_id, session.context_state)

    def save(self, session: AgentSession, state: ContextSessionState) -> None:
        session.context_state = state.to_dict()
        self._persist(session)

    def reset(self, session: AgentSession) -> ContextSessionState:
        state = ContextSessionState(session_id=session.session_id)
        self.save(session, state)
        return state

    def _persist(self, session: AgentSession) -> None:
        if self._persister is None:
            raise RuntimeError("ContextStateStore requires a bound SessionPersister")
        self._persister.persist(session)


context_state_store = ContextStateStore()
