# backend/app/sandbox/runner.py
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.core.config import settings
from app.sandbox.docker_executor import DockerSandboxExecutor
from app.sandbox.local_executor import LocalSandboxExecutor
from app.sandbox.manager import sandbox_manager
from app.sandbox.models import SandboxCommandResult, SandboxWorkspace
from app.services.session_types import AgentSession


class SandboxRunner:
    """沙箱命令统一入口。"""

    def __init__(self) -> None:
        self._executors = {
            "docker": DockerSandboxExecutor(),
            "local": LocalSandboxExecutor(),
        }

    async def run_shell(
        self,
        workspace: SandboxWorkspace,
        command: str,
        shell: str | None = None,
        timeout_seconds: int | None = None,
        session: AgentSession | None = None,
        run_id: str | None = None,
    ) -> SandboxCommandResult:
        active_shell = (shell or settings.sandbox_shell).lower()
        self._validate_command(command)
        effective_timeout = self._normalize_timeout(timeout_seconds)

        log_name = f"cmd_{uuid.uuid4().hex}.json"
        log_path = sandbox_manager.ensure_within_workspace(workspace, workspace.logs_dir / log_name)
        executor = self._require_executor()
        available, detail = await executor.check_availability()
        if not available:
            if settings.sandbox_fail_closed:
                raise RuntimeError(f"沙箱执行器不可用，且当前配置为 fail-closed: {detail}")
            raise RuntimeError(f"沙箱执行器不可用: {detail}")

        started_at = time.perf_counter()
        extra_kwargs: dict[str, Any] = {}
        if session is not None:
            extra_kwargs["session"] = session
        if run_id is not None:
            extra_kwargs["run_id"] = run_id
        if effective_timeout is not None:
            extra_kwargs["timeout_seconds"] = effective_timeout
        result = await executor.run_shell(
            workspace,
            command,
            active_shell,
            **extra_kwargs,
        )
        if result.duration_ms <= 0:
            result = SandboxCommandResult(
                command=result.command,
                shell=result.shell,
                executor=result.executor,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                log_path=result.log_path,
            )
        return self._write_log(workspace, log_path, result, extra={"availability": detail})

    async def check_status(self) -> dict[str, Any]:
        executor = self._require_executor()
        available, detail = await executor.check_availability()
        return {
            "executor": settings.sandbox_executor,
            "available": available,
            "detail": detail,
            "fail_closed": settings.sandbox_fail_closed,
            "allow_network": settings.sandbox_allow_network,
        }

    def _validate_command(self, command: str) -> None:
        normalized = f"{command.lower()} "
        for keyword in settings.sandbox_blocked_command_keywords:
            if keyword.lower() in normalized:
                raise RuntimeError(f"命令触发沙箱拦截规则: {keyword.strip()}")

    def _require_executor(self):
        executor = self._executors.get(settings.sandbox_executor.lower())
        if executor is None:
            raise RuntimeError(f"未知沙箱执行器: {settings.sandbox_executor}")
        return executor

    def _normalize_timeout(self, timeout_seconds: int | None) -> int | None:
        if timeout_seconds is None:
            return None
        max_timeout = int(settings.sandbox_command_max_timeout_seconds)
        if max_timeout <= 0:
            return max(1, int(timeout_seconds))
        return max(1, min(int(timeout_seconds), max_timeout))

    def _write_log(
        self,
        workspace: SandboxWorkspace,
        log_path,
        result: SandboxCommandResult,
        *,
        extra: dict[str, Any] | None = None,
    ) -> SandboxCommandResult:
        log_payload = {
            "command": result.command,
            "shell": result.shell,
            "executor": result.executor,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "runtime_metadata": result.runtime_metadata,
        }
        if extra:
            log_payload["extra"] = extra
        log_path.write_text(json.dumps(log_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return SandboxCommandResult(
            command=result.command,
            shell=result.shell,
            executor=result.executor,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=result.duration_ms,
            log_path=str(log_path.relative_to(workspace.root)),
            runtime_metadata=result.runtime_metadata,
        )


sandbox_runner = SandboxRunner()
