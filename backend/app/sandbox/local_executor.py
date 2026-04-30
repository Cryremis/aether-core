# backend/app/sandbox/local_executor.py
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from app.core.config import settings
from app.sandbox.executors import SandboxExecutor
from app.sandbox.models import SandboxCommandResult, SandboxWorkspace
from app.services.session_types import AgentSession


class LocalSandboxExecutor(SandboxExecutor):
    """本地子进程执行器，仅允许在显式开发模式下启用。"""

    name = "local"

    async def run_shell(
        self,
        workspace: SandboxWorkspace,
        command: str,
        shell: str,
        timeout_seconds: int | None = None,
        session: AgentSession | None = None,
        run_id: str | None = None,
    ) -> SandboxCommandResult:
        if not settings.sandbox_local_enabled:
            raise RuntimeError("当前环境未开启本地执行器，已拒绝宿主机直连执行。")

        program, args = self._build_shell_command(shell, command)
        started_at = time.perf_counter()
        effective_timeout = timeout_seconds or settings.sandbox_command_timeout_seconds
        kwargs: dict = {
            "cwd": str(workspace.work_dir),
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "env": self._build_env(workspace),
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        process = await asyncio.create_subprocess_exec(program, *args, **kwargs)

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=effective_timeout,
            )
        except asyncio.CancelledError:
            process.kill()
            await process.communicate()
            raise
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise RuntimeError(
                f"沙箱命令执行超时，超过 {effective_timeout} 秒。"
            ) from None

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        return SandboxCommandResult(
            command=command,
            shell=shell,
            executor=self.name,
            exit_code=process.returncode or 0,
            stdout=self._truncate(self._decode_output(stdout_bytes)),
            stderr=self._truncate(self._decode_output(stderr_bytes)),
            duration_ms=duration_ms,
            log_path="",
        )

    async def check_availability(self) -> tuple[bool, str]:
        if not settings.sandbox_local_enabled:
            return False, "本地执行器未开启。"
        return True, "本地执行器已启用。"

    def _build_shell_command(self, shell: str, command: str) -> tuple[str, list[str]]:
        if shell == "powershell":
            return (
                shutil.which("powershell.exe") or "powershell.exe",
                ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            )
        if shell == "bash":
            bash_path = self._resolve_bash()
            if not bash_path:
                raise RuntimeError("当前宿主环境不可用 bash，请改用 powershell，或关闭本地执行器。")
            return (bash_path, ["-lc", command])
        raise RuntimeError(f"暂不支持的 shell 类型: {shell}")

    def _resolve_bash(self) -> str | None:
        candidates = [
            shutil.which("bash"),
            shutil.which("bash.exe"),
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        return None

    def _build_env(self, workspace: SandboxWorkspace) -> dict[str, str]:
        env: dict[str, str] = {}
        for name in settings.sandbox_env_whitelist:
            value = os.environ.get(name)
            if value:
                env[name] = value
        env.update(
            {
                "AETHER_SESSION_ID": workspace.session_id,
                "AETHER_SANDBOX_ROOT": str(workspace.root),
                "AETHER_INPUT_DIR": str(workspace.input_dir),
                "AETHER_SKILLS_DIR": str(workspace.skills_dir),
                "AETHER_WORK_DIR": str(workspace.work_dir),
                "AETHER_OUTPUT_DIR": str(workspace.output_dir),
                "AETHER_LOGS_DIR": str(workspace.logs_dir),
                "PYTHONIOENCODING": "utf-8",
            }
        )
        return env

    def _decode_output(self, value: bytes) -> str:
        if not value:
            return ""
        for encoding in ("utf-8", "utf-16-le", "utf-16", "gbk"):
            try:
                decoded = value.decode(encoding)
                if "\x00" in decoded:
                    decoded = decoded.replace("\x00", "")
                return decoded
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="replace").replace("\x00", "")

    def _truncate(self, value: str) -> str:
        if len(value) <= settings.sandbox_output_char_limit:
            return value
        return f"{value[:settings.sandbox_output_char_limit]}\n\n[输出已截断]"
