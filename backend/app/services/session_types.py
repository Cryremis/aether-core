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
class SessionRunContext:
    """描述一次活跃对话运行的取消状态。"""

    run_id: str
    started_at: float = field(default_factory=time.time)
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    partial_content: str = ""
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_task: Any | None = None
    cleanup_task: asyncio.Task[Any] | None = None

    def request_abort(self) -> None:
        self.abort_event.set()

    def is_aborted(self) -> bool:
        return self.abort_event.is_set()


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
    active_run: SessionRunContext | None = None
    last_abort: SessionRunContext | None = None

    def touch(self) -> None:
        self.last_access = time.time()

    def begin_run(self, run_id: str) -> SessionRunContext:
        run = SessionRunContext(run_id=run_id)
        self.active_run = run
        return run

    def get_run(self, run_id: str) -> SessionRunContext | None:
        run = self.active_run
        if run is not None and run.run_id == run_id:
            return run
        return None

    def finish_run(self, run_id: str) -> None:
        run = self.get_run(run_id)
        if run is not None:
            self.active_run = None

    def request_abort(self) -> str | None:
        run = self.active_run
        if run is None:
            return None
        run.request_abort()
        self.last_abort = SessionRunContext(
            run_id=run.run_id,
            started_at=run.started_at,
            partial_content=run.partial_content,
            tool_call_id=run.tool_call_id,
            tool_name=run.tool_name,
        )
        self.last_abort.abort_event.set()
        return run.run_id

    def current_run_id(self) -> str | None:
        return self.active_run.run_id if self.active_run is not None else None

    def is_aborted(self, run_id: str) -> bool:
        run = self.get_run(run_id)
        return run.is_aborted() if run is not None else True

    def set_partial_content(self, run_id: str, content: str) -> None:
        run = self.get_run(run_id)
        if run is not None:
            run.partial_content = content

    def get_partial_content(self, run_id: str) -> str:
        run = self.get_run(run_id)
        if run is not None:
            return run.partial_content
        if self.last_abort is not None and self.last_abort.run_id == run_id:
            return self.last_abort.partial_content
        return ""

    def mark_tool_running(self, run_id: str, *, tool_call_id: str, tool_name: str) -> None:
        run = self.get_run(run_id)
        if run is not None:
            run.tool_call_id = tool_call_id
            run.tool_name = tool_name

    def clear_tool_running(self, run_id: str) -> None:
        run = self.get_run(run_id)
        if run is not None:
            run.tool_call_id = None
            run.tool_name = None
            run.tool_task = None

    def set_tool_task(self, run_id: str, task: Any | None) -> None:
        run = self.get_run(run_id)
        if run is not None:
            run.tool_task = task

    def get_tool_task(self, run_id: str) -> Any | None:
        run = self.get_run(run_id)
        return run.tool_task if run is not None else None

    def set_cleanup_task(self, run_id: str, task: asyncio.Task[Any] | None) -> None:
        run = self.get_run(run_id)
        if run is not None:
            run.cleanup_task = task

    def get_cleanup_task(self, run_id: str) -> asyncio.Task[Any] | None:
        run = self.get_run(run_id)
        return run.cleanup_task if run is not None else None

    def get_abort_snapshot(self) -> SessionRunContext | None:
        return self.last_abort
