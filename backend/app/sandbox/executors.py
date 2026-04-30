# backend/app/sandbox/executors.py
from __future__ import annotations

from abc import ABC, abstractmethod

from app.sandbox.models import SandboxCommandResult, SandboxWorkspace
from app.services.session_types import AgentSession


class SandboxExecutor(ABC):
    """沙箱执行器抽象。"""

    name: str

    @abstractmethod
    async def run_shell(
        self,
        workspace: SandboxWorkspace,
        command: str,
        shell: str,
        timeout_seconds: int | None = None,
        session: AgentSession | None = None,
        run_id: str | None = None,
    ) -> SandboxCommandResult:
        """在受控执行环境中运行命令。"""

    @abstractmethod
    async def check_availability(self) -> tuple[bool, str]:
        """检查执行器是否可用。"""
