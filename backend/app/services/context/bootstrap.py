# backend/app/services/context/bootstrap.py
"""上下文子系统运行时装配入口。"""

from __future__ import annotations

from app.services.context.contracts import SessionPersister
from app.services.context.context_pipeline import context_pipeline
from app.services.context.context_state_store import context_state_store


def configure_context_runtime(persister: SessionPersister) -> None:
    """将上下文单例绑定到会话持久化边界。"""

    context_state_store.bind_persister(persister)
    context_pipeline.bind_persister(persister)
