# backend/app/services/context/contracts.py
"""上下文子系统依赖的抽象协议。"""

from __future__ import annotations

from typing import Protocol

from app.services.session_types import AgentSession


class SessionPersister(Protocol):
    """由会话子系统提供的持久化边界。"""

    def persist(self, session: AgentSession) -> None:
        """在上下文状态或历史消息变更后持久化会话。"""
