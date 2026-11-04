# backend/app/sandbox/docker_executor.py
from __future__ import annotations

from app.sandbox.executors import SandboxExecutor, SandboxOutputCallback
from app.sandbox.models import SandboxCommandResult, SandboxWorkspace
from app.services.workspace_runtime_service import workspace_runtime_service
from app.services.session_types import AgentSession


class DockerSandboxExecutor(SandboxExecutor):
    """基于 Docker 的强隔离执行器。"""

    name = "docker"

    async def run_shell(
        self,
        workspace: SandboxWorkspace,
        command: str,
        shell: str,
        timeout_seconds: int | None = None,
        session: AgentSession | None = None,
        run_id: str | None = None,
        output_callback: SandboxOutputCallback | None = None,
    ) -> SandboxCommandResult:
        return await workspace_runtime_service.run_shell(
            workspace,
            command=command,
            shell=shell,
            timeout_seconds=timeout_seconds,
            session=session,
            run_id=run_id,
            output_callback=output_callback,
        )

    async def check_availability(self) -> tuple[bool, str]:
        return await workspace_runtime_service.check_availability()
