# backend/app/sandbox/runner.py
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from app.core.config import settings
from app.sandbox.manager import sandbox_manager
from app.sandbox.models import SandboxCommandResult, SandboxWorkspace


class SandboxRunner:
    """受限子进程执行器。"""

    async def run_shell(
        self,
        workspace: SandboxWorkspace,
        command: str,
        shell: str | None = None,
    ) -> SandboxCommandResult:
        active_shell = (shell or settings.sandbox_shell).lower()
        self._validate_command(command)

        program, args = self._build_shell_command(active_shell, command)
        log_name = f"cmd_{uuid.uuid4().hex}.json"
        log_path = sandbox_manager.ensure_within_workspace(workspace, workspace.logs_dir / log_name)

        started_at = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            program,
            *args,
            cwd=str(workspace.work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._build_env(workspace),
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=settings.sandbox_command_timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise RuntimeError(f"沙箱命令执行超时，超过 {settings.sandbox_command_timeout_seconds} 秒。") from None

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        stdout = self._truncate(stdout_bytes.decode("utf-8", errors="replace"))
        stderr = self._truncate(stderr_bytes.decode("utf-8", errors="replace"))

        log_payload = {
            "command": command,
            "shell": active_shell,
            "exit_code": process.returncode,
            "duration_ms": duration_ms,
            "stdout": stdout,
            "stderr": stderr,
        }
        log_path.write_text(json.dumps(log_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        return SandboxCommandResult(
            command=command,
            shell=active_shell,
            exit_code=process.returncode or 0,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            log_path=str(log_path.relative_to(workspace.root)),
        )

    def _build_shell_command(self, shell: str, command: str) -> tuple[str, list[str]]:
        if shell == "powershell":
            return (
                "powershell",
                ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            )
        if shell == "bash":
            return ("bash", ["-lc", command])
        raise RuntimeError(f"暂不支持的 shell 类型: {shell}")

    def _build_env(self, workspace: SandboxWorkspace) -> dict[str, str]:
        return {
            "AETHER_SESSION_ID": workspace.session_id,
            "AETHER_SANDBOX_ROOT": str(workspace.root),
            "AETHER_INPUT_DIR": str(workspace.input_dir),
            "AETHER_SKILLS_DIR": str(workspace.skills_dir),
            "AETHER_WORK_DIR": str(workspace.work_dir),
            "AETHER_OUTPUT_DIR": str(workspace.output_dir),
            "AETHER_LOGS_DIR": str(workspace.logs_dir),
            "PYTHONIOENCODING": "utf-8",
        }

    def _validate_command(self, command: str) -> None:
        normalized = f"{command.lower()} "
        for keyword in settings.sandbox_blocked_command_keywords:
            if keyword.lower() in normalized:
                raise RuntimeError(f"命令触发沙箱拦截规则: {keyword.strip()}")

    def _truncate(self, value: str) -> str:
        if len(value) <= settings.sandbox_output_char_limit:
            return value
        return f"{value[:settings.sandbox_output_char_limit]}\n\n[输出已截断]"


sandbox_runner = SandboxRunner()
