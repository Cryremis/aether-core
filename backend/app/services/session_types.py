# backend/app/services/session_types.py
"""会话领域模型。

该模块只定义 AetherCore 运行时共享的会话数据结构，
避免业务模块为了引用 AgentSession 而依赖 session_service。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from app.sandbox.models import SandboxWorkspace


CONTEXT_MESSAGE_SCHEMA_VERSION = 2


@dataclass
class AgentSession:
    """AetherCore 运行时会话状态。"""

    session_id: str
    conversation_id: str | None = None
    host_name: str = ""
    baseline_root: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    host_context: dict[str, Any] = field(default_factory=dict)
    platform_files: list[dict[str, Any]] = field(default_factory=list)
    platform_skills: list[dict[str, Any]] = field(default_factory=list)
    host_tools: list[dict[str, Any]] = field(default_factory=list)
    host_skills: list[dict[str, Any]] = field(default_factory=list)
    host_system_prompts: list[dict[str, Any]] = field(default_factory=list)
    uploaded_skills: list[dict[str, Any]] = field(default_factory=list)
    host_apis: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    uploads: list[dict[str, Any]] = field(default_factory=list)
    context_state: dict[str, Any] = field(default_factory=dict)
    message_schema_version: int = CONTEXT_MESSAGE_SCHEMA_VERSION
    allow_network: bool = True
    created_at: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)
    workspace: SandboxWorkspace | None = None
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    partial_content: str = ""

    def touch(self) -> None:
        self.last_access = time.time()

    def request_abort(self) -> None:
        self.abort_event.set()

    def clear_abort(self) -> None:
        self.abort_event.clear()
        self.partial_content = ""

    def is_aborted(self) -> bool:
        return self.abort_event.is_set()
