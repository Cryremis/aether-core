# backend/app/sandbox/docker_executor.py
from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from pathlib import Path

from app.core.config import settings
from app.sandbox.executors import SandboxExecutor
from app.sandbox.models import SandboxCommandResult, SandboxWorkspace


class DockerSandboxExecutor(SandboxExecutor):
    """基于 Docker 的强隔离执行器。"""

    name = "docker"

    async def run_shell(
        self,
        workspace: SandboxWorkspace,
        command: str,
        shell: str,
    ) -> SandboxCommandResult:
        docker_binary = self._resolve_docker_binary()
        if not docker_binary:
            raise RuntimeError("未找到 docker 可执行文件，无法启动容器沙箱。")

        container_name = f"aethercore-sbx-{workspace.session_id[:18]}-{uuid.uuid4().hex[:8]}"
        process_command = self._build_process_command(shell, command)
        cli_args = self._build_docker_run_args(workspace, container_name, process_command)

        started_at = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            docker_binary,
            *cli_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=settings.sandbox_command_timeout_seconds,
            )
        except asyncio.TimeoutError:
            await self._force_remove_container(docker_binary, container_name)
            raise RuntimeError(
                f"容器沙箱执行超时，超过 {settings.sandbox_command_timeout_seconds} 秒。"
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
        docker_binary = self._resolve_docker_binary()
        if not docker_binary:
            return False, "未找到 docker 可执行文件。"

        process = await asyncio.create_subprocess_exec(
            docker_binary,
            "version",
            "--format",
            "{{.Server.Version}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        if process.returncode != 0:
            error_text = self._decode_output(stderr_bytes).strip() or self._decode_output(stdout_bytes).strip()
            return False, error_text or "Docker daemon 不可用。"
        version = self._decode_output(stdout_bytes).strip()
        return True, version or "docker-ok"

    def _resolve_docker_binary(self) -> str | None:
        configured = settings.sandbox_docker_command.strip()
        candidates = [
            shutil.which(configured),
            shutil.which("docker.exe"),
            configured if Path(configured).suffix else None,
        ]
        for candidate in candidates:
            if not candidate:
                continue
            if Path(candidate).exists() or shutil.which(candidate):
                return candidate
        return None

    def _build_process_command(self, shell: str, command: str) -> list[str]:
        if shell == "bash":
            return ["/bin/bash", "-lc", command]
        if shell == "powershell":
            return ["pwsh", "-NoLogo", "-NoProfile", "-Command", command]
        raise RuntimeError(f"暂不支持的 shell 类型: {shell}")

    def _build_docker_run_args(
        self,
        workspace: SandboxWorkspace,
        container_name: str,
        process_command: list[str],
    ) -> list[str]:
        args = [
            "run",
            "--rm",
            "--name",
            container_name,
            "--workdir",
            settings.sandbox_docker_work_dir,
            "--user",
            settings.sandbox_docker_user,
            "--mount",
            (
                "type=bind,"
                f"src={workspace.root.resolve()},"
                f"dst={settings.sandbox_docker_workspace_mount}"
            ),
            "--memory",
            settings.sandbox_docker_memory,
            "--cpus",
            settings.sandbox_docker_cpus,
            "--pids-limit",
            str(settings.sandbox_docker_pids_limit),
            "--security-opt",
            "no-new-privileges:true",
            "--cap-drop",
            "ALL",
            "--env",
            f"AETHER_SESSION_ID={workspace.session_id}",
            "--env",
            f"AETHER_SANDBOX_ROOT={settings.sandbox_docker_workspace_mount}",
            "--env",
            f"AETHER_INPUT_DIR={settings.sandbox_docker_input_dir}",
            "--env",
            f"AETHER_SKILLS_DIR={settings.sandbox_docker_skills_dir}",
            "--env",
            f"AETHER_WORK_DIR={settings.sandbox_docker_work_dir}",
            "--env",
            f"AETHER_OUTPUT_DIR={settings.sandbox_docker_output_dir}",
            "--env",
            f"AETHER_LOGS_DIR={settings.sandbox_docker_logs_dir}",
            "--env",
            "PYTHONIOENCODING=utf-8",
        ]

        if settings.sandbox_docker_read_only_rootfs:
            args.append("--read-only")

        for tmpfs in settings.sandbox_docker_tmpfs:
            args.extend(["--tmpfs", tmpfs])

        if not settings.sandbox_allow_network:
            args.extend(["--network", "none"])

        args.append(settings.sandbox_docker_image)
        args.extend(process_command)
        return args

    async def _force_remove_container(self, docker_binary: str, container_name: str) -> None:
        process = await asyncio.create_subprocess_exec(
            docker_binary,
            "rm",
            "-f",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.communicate()

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
