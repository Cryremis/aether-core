# backend/app/sandbox/docker_executor.py
from __future__ import annotations

import asyncio
import shlex
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.sandbox.executors import SandboxExecutor
from app.sandbox.models import SandboxCommandResult, SandboxWorkspace


@dataclass(frozen=True)
class BaselineRuntimePlan:
    """描述基线环境在容器内的挂载策略。"""

    mode: str
    mount_upper_workspace: bool
    requires_root: bool


class DockerSandboxExecutor(SandboxExecutor):
    """基于 Docker 的强隔离执行器。"""

    name = "docker"

    def __init__(self) -> None:
        self._baseline_plan_cache: BaselineRuntimePlan | None = None

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
        baseline_plan = await self._resolve_baseline_plan(docker_binary, workspace)
        cli_args = self._build_docker_run_args(workspace, container_name, process_command, baseline_plan)

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
            return ["/bin/bash", "-c", command]
        if shell == "powershell":
            return ["pwsh", "-NoLogo", "-NoProfile", "-Command", command]
        raise RuntimeError(f"暂不支持的 shell 类型: {shell}")

    def _build_docker_run_args(
        self,
        workspace: SandboxWorkspace,
        container_name: str,
        process_command: list[str],
        baseline_plan: BaselineRuntimePlan,
    ) -> list[str]:
        args = [
            "run",
            "--rm",
            "--name",
            container_name,
            "--workdir",
            settings.sandbox_docker_work_dir,
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
            "--cap-add",
            "SYS_ADMIN",
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
            "--env",
            "HOME=/tmp/aether-home",
            "--env",
            "XDG_CACHE_HOME=/tmp/aether-home/.cache",
            "--env",
            "XDG_CONFIG_HOME=/tmp/aether-home/.config",
            "--env",
            "DOTNET_CLI_HOME=/tmp/aether-home",
        ]

        if baseline_plan.requires_root:
            args.extend(["--user", "root"])

        if baseline_plan.mount_upper_workspace:
            baseline_root = workspace.baseline_root
            if baseline_root is None:
                raise RuntimeError("mount_upper_workspace requires baseline_root")
            args.extend(
                [
                    "--mount",
                    (
                        "type=bind,"
                        f"src={workspace.root.resolve()},"
                        "dst=/aether/session-upper"
                    ),
                    "--mount",
                    (
                        "type=bind,"
                        f"src={workspace.overlay_work_dir.resolve()},"
                        "dst=/aether/session-overlay-work"
                    ),
                    "--mount",
                    (
                        "type=bind,"
                        f"src={baseline_root.resolve()},"
                        "dst=/aether/baseline,readonly"
                    ),
                ]
            )
        elif workspace.baseline_root is not None:
            br = workspace.baseline_root
            args.extend(
                [
                    "--mount",
                    (
                        "type=bind,"
                        f"src={br.resolve()},"
                        "dst=/aether/baseline,readonly"
                    ),
                ]
            )
        if baseline_plan.mode == "overlay":
            args.extend(["--tmpfs", "/workspace:size=64m"])
        else:
            args.extend(
                [
                    "--mount",
                    (
                        "type=bind,"
                        f"src={workspace.root.resolve()},"
                        f"dst={settings.sandbox_docker_workspace_mount}"
                    ),
                ]
            )

        if settings.sandbox_docker_read_only_rootfs:
            args.append("--read-only")

        for tmpfs in settings.sandbox_docker_tmpfs:
            args.extend(["--tmpfs", tmpfs])

        if not settings.sandbox_allow_network:
            args.extend(["--network", "none"])

        args.append(settings.sandbox_docker_image)
        args.extend(self._build_runtime_command(process_command, baseline_plan))
        return args

    def _build_runtime_command(
        self,
        process_command: list[str],
        baseline_plan: BaselineRuntimePlan,
    ) -> list[str]:
        if baseline_plan.mode == "direct":
            return process_command

        quoted_process = shlex.join(process_command)
        if baseline_plan.mode == "copy":
            setup_script = """
set -euo pipefail
mkdir -p /workspace/metadata
if [ ! -f /workspace/metadata/.baseline-materialized ]; then
  for name in input skills work output logs; do
    mkdir -p "/workspace/${{name}}"
    if [ -d "/aether/baseline/${{name}}" ]; then
      cp -R "/aether/baseline/${{name}}/." "/workspace/${{name}}/"
    fi
  done
  printf 'copy\n' > /workspace/metadata/.baseline-materialized
fi
exec {process}
""".strip().format(process=quoted_process)
            return ["/bin/bash", "-c", setup_script]

        setup_script = """
set -euo pipefail
mkdir -p /workspace /tmp/aether-runtime
for name in input skills work output logs; do
  mkdir -p "/workspace/${{name}}"
  mkdir -p "/aether/session-upper/${{name}}"
  mkdir -p "/aether/session-overlay-work/${{name}}"
  mount -t overlay overlay \
    -o "lowerdir=/aether/baseline/${{name}},upperdir=/aether/session-upper/${{name}},workdir=/aether/session-overlay-work/${{name}}" \
    "/workspace/${{name}}"
done
exec {process}
""".strip().format(process=quoted_process)
        return ["/bin/bash", "-c", setup_script]

    async def _resolve_baseline_plan(
        self,
        docker_binary: str,
        workspace: SandboxWorkspace,
    ) -> BaselineRuntimePlan:
        if workspace.baseline_root is None:
            return BaselineRuntimePlan(mode="direct", mount_upper_workspace=False, requires_root=False)
        if self._baseline_plan_cache is not None:
            return self._baseline_plan_cache

        supports_overlay = await self._supports_overlay_mount(docker_binary)
        if supports_overlay:
            self._baseline_plan_cache = BaselineRuntimePlan(mode="overlay", mount_upper_workspace=True, requires_root=True)
        else:
            self._baseline_plan_cache = BaselineRuntimePlan(mode="copy", mount_upper_workspace=False, requires_root=True)
        return self._baseline_plan_cache

    async def _supports_overlay_mount(self, docker_binary: str) -> bool:
        probe_name = f"aethercore-sbx-probe-{uuid.uuid4().hex[:8]}"
        process = await asyncio.create_subprocess_exec(
            docker_binary,
            "run",
            "--rm",
            "--name",
            probe_name,
            "--user",
            "root",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "SYS_ADMIN",
            settings.sandbox_docker_image,
            "/bin/bash",
            "-c",
            (
                "set -e;"
                "mkdir -p /tmp/testmnt /tmp/lower /tmp/upper /tmp/work;"
                "touch /tmp/lower/probe.txt;"
                "mount -t overlay overlay "
                "-o lowerdir=/tmp/lower,upperdir=/tmp/upper,workdir=/tmp/work /tmp/testmnt"
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await process.communicate()
        if process.returncode == 0:
            return True
        stderr_text = self._decode_output(stderr_bytes)
        if "wrong fs type" in stderr_text.lower() or "must be superuser" in stderr_text.lower():
            return False
        return False

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
