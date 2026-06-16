from __future__ import annotations

import asyncio
import ipaddress
import os
import shlex
import shutil
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.core.config import settings
from app.sandbox.models import SandboxCommandResult, SandboxWorkspace
from app.services.platform_runtime_image_service import platform_runtime_image_service
from app.services.session_workspace_sync_service import session_workspace_sync_service
from app.services.session_types import AgentSession
from app.services.store import store_service, utcnow_iso


SandboxOutputCallback = Callable[[str, str], Awaitable[None]]


@dataclass(frozen=True)
class RuntimeBusyError(RuntimeError):
    session_id: str
    summary: str
    runtime: dict[str, Any]
    suggested_actions: tuple[str, ...] = ("retry_wait", "rebuild_runtime")

    def __str__(self) -> str:
        return self.summary


@dataclass(frozen=True)
class RuntimeStartError(RuntimeError):
    session_id: str
    summary: str
    runtime: dict[str, Any]

    def __str__(self) -> str:
        return self.summary


class SessionRuntimeService:
    """管理会话级持久化容器 runtime。"""

    _DEFAULT_SANDBOX_UID = 10001
    _DEFAULT_SANDBOX_GID = 10001
    def __init__(self) -> None:
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._gc_task: asyncio.Task[None] | None = None
        self._host_dns_servers_cache: tuple[str, ...] | None = None

    async def start_background_tasks(self) -> None:
        if self._gc_task and not self._gc_task.done():
            return
        self._gc_task = asyncio.create_task(self._gc_loop())

    async def stop_background_tasks(self) -> None:
        if self._gc_task is None:
            return
        self._gc_task.cancel()
        try:
            await self._gc_task
        except asyncio.CancelledError:
            pass
        self._gc_task = None

    async def run_shell(
        self,
        workspace: SandboxWorkspace,
        *,
        command: str,
        shell: str,
        timeout_seconds: int | None = None,
        session: AgentSession | None = None,
        run_id: str | None = None,
        output_callback: SandboxOutputCallback | None = None,
    ) -> SandboxCommandResult:
        lock = self._locks[workspace.session_id]
        async with lock:
            return await self._run_shell_locked(
                workspace,
                command=command,
                shell=shell,
                timeout_seconds=timeout_seconds,
                allow_network_recovery=True,
                session=session,
                run_id=run_id,
                output_callback=output_callback,
            )

    async def rebuild_runtime(self, workspace: SandboxWorkspace, *, reason: str) -> dict[str, Any]:
        lock = self._locks[workspace.session_id]
        async with lock:
            current = await self.refresh_runtime(workspace.session_id)
            previous_generation = int(current.get("generation") or 0)
            previous_status = str(current.get("status") or "missing")
            if current.get("container_name"):
                await self._collect_locked(workspace.session_id, reason=reason)
            runtime = await self._create_runtime_locked(workspace, generation=previous_generation + 1)
            return {
                "status": "recreated" if previous_generation > 0 else "created",
                "reason": reason,
                "previous_status": previous_status,
                "previous_generation": previous_generation,
                "generation": runtime["generation"],
                "container_name": runtime["container_name"],
                "idle_expires_at": runtime["idle_expires_at"],
            }

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
            **self._subprocess_kwargs(),
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        if process.returncode != 0:
            error_text = self._decode_output(stderr_bytes).strip() or self._decode_output(stdout_bytes).strip()
            return False, error_text or "Docker daemon 不可用。"
        return True, self._decode_output(stdout_bytes).strip() or "docker-ok"

    async def list_runtimes(self, *, refresh: bool = True) -> list[dict[str, Any]]:
        items = store_service.list_session_runtimes()
        if not refresh:
            return items
        refreshed: list[dict[str, Any]] = []
        for item in items:
            refreshed.append(await self.refresh_runtime(item["session_id"]))
        return refreshed

    async def refresh_runtime(self, session_id: str) -> dict[str, Any]:
        record = store_service.get_session_runtime(session_id)
        if record is None:
            return {
                "session_id": session_id,
                "status": "missing",
            }
        state = await self._inspect_container_state(str(record.get("container_name") or ""))
        if state is None and record["status"] not in {"collected", "expired"}:
            now_iso = utcnow_iso()
            record = store_service.upsert_session_runtime(
                session_id=session_id,
                conversation_id=record.get("conversation_id"),
                platform_id=record.get("platform_id"),
                owner_user_id=record.get("owner_user_id"),
                external_user_id=record.get("external_user_id"),
                container_name=record.get("container_name"),
                container_id=record.get("container_id"),
                image=str(record.get("image") or settings.sandbox_docker_image),
                status="failed_start" if record["status"] == "provisioning" else "failed_runtime",
                generation=int(record.get("generation") or 0),
                network_mode=str(record.get("network_mode") or self._runtime_network_mode()),
                created_at=str(record.get("created_at") or now_iso),
                updated_at=now_iso,
                last_started_at=record.get("last_started_at"),
                last_used_at=record.get("last_used_at"),
                idle_expires_at=record.get("idle_expires_at"),
                max_expires_at=record.get("max_expires_at"),
                destroyed_at=record.get("destroyed_at"),
                destroy_reason=record.get("destroy_reason") or "container_missing",
                restart_count=int(record.get("restart_count") or 0),
                workspace_root=str(record.get("workspace_root") or ""),
                home_root=str(record.get("home_root") or ""),
                metadata=record.get("metadata") or {},
            )
            return record
        if state and state != record["status"] and record["status"] not in {"executing"}:
            mapped_state = self._map_container_state_to_runtime_status(state)
            now_iso = utcnow_iso()
            record = store_service.upsert_session_runtime(
                session_id=session_id,
                conversation_id=record.get("conversation_id"),
                platform_id=record.get("platform_id"),
                owner_user_id=record.get("owner_user_id"),
                external_user_id=record.get("external_user_id"),
                container_name=record.get("container_name"),
                container_id=record.get("container_id"),
                image=str(record.get("image") or settings.sandbox_docker_image),
                status=mapped_state,
                generation=int(record.get("generation") or 0),
                network_mode=str(record.get("network_mode") or self._runtime_network_mode()),
                created_at=str(record.get("created_at") or now_iso),
                updated_at=now_iso,
                last_started_at=record.get("last_started_at"),
                last_used_at=record.get("last_used_at"),
                idle_expires_at=record.get("idle_expires_at"),
                max_expires_at=record.get("max_expires_at"),
                destroyed_at=record.get("destroyed_at"),
                destroy_reason=record.get("destroy_reason"),
                restart_count=int(record.get("restart_count") or 0),
                workspace_root=str(record.get("workspace_root") or ""),
                home_root=str(record.get("home_root") or ""),
                metadata=record.get("metadata") or {},
            )
        return record

    async def collect_runtime(self, session_id: str, *, reason: str) -> dict[str, Any] | None:
        lock = self._locks[session_id]
        async with lock:
            return await self._collect_locked(session_id, reason=reason)

    async def delete_runtime(self, session_id: str, *, reason: str) -> None:
        lock = self._locks[session_id]
        async with lock:
            await self._collect_locked(session_id, reason=reason)
            store_service.delete_session_runtime(session_id)

    async def collect_expired_runtimes(self) -> None:
        now = self._now()
        items = store_service.list_session_runtimes()
        for item in items:
            if item.get("status") in {"executing", "provisioning", "syncing", "terminating"}:
                continue
            idle_expires_at = self._parse_dt(item.get("idle_expires_at"))
            max_expires_at = self._parse_dt(item.get("max_expires_at"))
            if idle_expires_at and idle_expires_at <= now:
                await self.collect_runtime(item["session_id"], reason="idle_ttl_expired")
                continue
            if max_expires_at and max_expires_at <= now:
                await self.collect_runtime(item["session_id"], reason="max_age_expired")

    async def collect_platform_runtimes(self, platform_id: int, *, reason: str) -> int:
        items = store_service.list_session_runtimes()
        collected = 0
        for item in items:
            if int(item.get("platform_id") or 0) != int(platform_id):
                continue
            if item.get("status") in {"missing", "collected", "expired"}:
                continue
            updated = await self.collect_runtime(str(item["session_id"]), reason=reason)
            if updated is not None:
                collected += 1
        return collected

    async def _gc_loop(self) -> None:
        while True:
            try:
                await self.collect_expired_runtimes()
            except Exception:
                pass
            await asyncio.sleep(max(30, settings.sandbox_runtime_gc_interval_seconds))

    async def _ensure_runtime_locked(self, workspace: SandboxWorkspace) -> dict[str, Any]:
        current = await self.refresh_runtime(workspace.session_id)
        now = self._now()
        recreate_reason = self._detect_runtime_recreate_reason(current, now, workspace)
        if current.get("status") == "running" and recreate_reason is None:
            return {
                "runtime": current,
                "notice": None,
            }
        if current.get("status") == "executing" and recreate_reason is None:
            return {
                "runtime": current,
                "notice": None,
            }
        if current.get("status") == "terminating" and recreate_reason is None:
            return {
                "runtime": current,
                "notice": None,
            }
        if recreate_reason is None and current.get("status") in {"provisioning", "syncing"}:
            return {
                "runtime": current,
                "notice": None,
            }

        previous_generation = int(current.get("generation") or 0)
        previous_status = str(current.get("status") or "missing")
        if current.get("container_name"):
            await self._collect_locked(workspace.session_id, reason=recreate_reason or "runtime_replaced")
        runtime = await self._create_runtime_locked(workspace, generation=previous_generation + 1)
        reason = recreate_reason or ("container_missing" if previous_status != "missing" else "fresh_start")
        notice = {
            "status": "recreated" if previous_generation > 0 else "created",
            "reason": reason,
            "previous_status": previous_status,
            "previous_generation": previous_generation,
            "generation": runtime["generation"],
            "container_name": runtime["container_name"],
            "idle_expires_at": runtime["idle_expires_at"],
        }
        return {
            "runtime": runtime,
            "notice": notice,
        }

    async def _create_runtime_locked(self, workspace: SandboxWorkspace, *, generation: int) -> dict[str, Any]:
        docker_binary = self._require_docker_binary()
        conversation = store_service.get_conversation_by_session(workspace.session_id) or {}
        runtime_image = await platform_runtime_image_service.ensure_platform_runtime_image(conversation.get("platform_id"))
        created_at = utcnow_iso()
        max_expires_at = self._to_iso(self._now() + timedelta(seconds=settings.sandbox_runtime_max_age_seconds))
        idle_expires_at = self._to_iso(self._now() + timedelta(seconds=settings.sandbox_runtime_idle_ttl_seconds))
        container_name = self._build_container_name(workspace.session_id, generation)
        runtime = store_service.upsert_session_runtime(
            session_id=workspace.session_id,
            conversation_id=conversation.get("conversation_id"),
            platform_id=conversation.get("platform_id"),
            owner_user_id=conversation.get("owner_user_id"),
            external_user_id=conversation.get("external_user_id"),
            container_name=container_name,
            container_id=None,
            image=runtime_image,
            status="provisioning",
            generation=generation,
            network_mode=self._runtime_network_mode(),
            created_at=created_at,
            updated_at=created_at,
            last_started_at=None,
            last_used_at=None,
            idle_expires_at=idle_expires_at,
            max_expires_at=max_expires_at,
            destroyed_at=None,
            destroy_reason=None,
            restart_count=0,
            workspace_root=str(workspace.root),
            home_root=str(workspace.home_dir),
            metadata=self._build_runtime_metadata(runtime_image, workspace),
        )
        process = await asyncio.create_subprocess_exec(
            docker_binary,
            *self._build_run_args(workspace, container_name, runtime_image),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **self._subprocess_kwargs(),
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        if process.returncode != 0:
            stderr_text = self._decode_output(stderr_bytes).strip() or self._decode_output(stdout_bytes).strip()
            store_service.upsert_session_runtime(
                session_id=workspace.session_id,
                conversation_id=conversation.get("conversation_id"),
                platform_id=conversation.get("platform_id"),
                owner_user_id=conversation.get("owner_user_id"),
                external_user_id=conversation.get("external_user_id"),
                container_name=container_name,
                container_id=None,
                image=runtime_image,
                status="failed_start",
                generation=generation,
                network_mode=self._runtime_network_mode(),
                created_at=created_at,
                updated_at=utcnow_iso(),
                last_started_at=None,
                last_used_at=None,
                idle_expires_at=idle_expires_at,
                max_expires_at=max_expires_at,
                destroyed_at=utcnow_iso(),
                destroy_reason=stderr_text or "container_create_failed",
                restart_count=0,
                workspace_root=str(workspace.root),
                home_root=str(workspace.home_dir),
                metadata=self._build_runtime_metadata(runtime_image, workspace),
            )
            raise RuntimeStartError(
                session_id=workspace.session_id,
                summary=stderr_text or "创建会话 runtime 失败。",
                runtime={
                    "status": "failed_start",
                    "generation": generation,
                    "container_name": container_name,
                    "image": runtime_image,
                    "destroy_reason": stderr_text or "container_create_failed",
                },
            )
        container_id = self._decode_output(stdout_bytes).strip() or None
        runtime = store_service.upsert_session_runtime(
            session_id=workspace.session_id,
            conversation_id=conversation.get("conversation_id"),
            platform_id=conversation.get("platform_id"),
            owner_user_id=conversation.get("owner_user_id"),
            external_user_id=conversation.get("external_user_id"),
            container_name=container_name,
            container_id=container_id,
            image=runtime_image,
            status="running",
            generation=generation,
            network_mode=self._runtime_network_mode(),
            created_at=created_at,
            updated_at=utcnow_iso(),
            last_started_at=created_at,
            last_used_at=created_at,
            idle_expires_at=idle_expires_at,
            max_expires_at=max_expires_at,
            destroyed_at=None,
            destroy_reason=None,
            restart_count=0,
            workspace_root=str(workspace.root),
            home_root=str(workspace.home_dir),
            metadata=self._build_runtime_metadata(runtime_image, workspace),
        )
        try:
            await session_workspace_sync_service.hydrate_container(
                docker_binary=docker_binary,
                container_name=container_name,
                workspace=workspace,
            )
        except Exception as exc:
            destroy_reason = str(exc) or "workspace_hydration_failed"
            await self._collect_locked(workspace.session_id, reason="workspace_hydration_failed")
            store_service.upsert_session_runtime(
                session_id=workspace.session_id,
                conversation_id=conversation.get("conversation_id"),
                platform_id=conversation.get("platform_id"),
                owner_user_id=conversation.get("owner_user_id"),
                external_user_id=conversation.get("external_user_id"),
                container_name=container_name,
                container_id=container_id,
                image=runtime_image,
                status="failed_start",
                generation=generation,
                network_mode=self._runtime_network_mode(),
                created_at=created_at,
                updated_at=utcnow_iso(),
                last_started_at=created_at,
                last_used_at=created_at,
                idle_expires_at=idle_expires_at,
                max_expires_at=max_expires_at,
                destroyed_at=utcnow_iso(),
                destroy_reason=destroy_reason,
                restart_count=0,
                workspace_root=str(workspace.root),
                home_root=str(workspace.home_dir),
                metadata=self._build_runtime_metadata(runtime_image, workspace),
            )
            raise RuntimeStartError(
                session_id=workspace.session_id,
                summary=f"初始化会话增量失败: {destroy_reason}",
                runtime={
                    "status": "failed_start",
                    "generation": generation,
                    "container_name": container_name,
                    "image": runtime_image,
                    "destroy_reason": destroy_reason,
                },
            ) from None
        return runtime

    async def _collect_locked(self, session_id: str, *, reason: str) -> dict[str, Any] | None:
        record = store_service.get_session_runtime(session_id)
        if record is None:
            return None
        container_name = str(record.get("container_name") or "")
        if container_name:
            docker_binary = self._resolve_docker_binary()
            if docker_binary:
                process = await asyncio.create_subprocess_exec(
                    docker_binary,
                    "rm",
                    "-f",
                    container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    **self._subprocess_kwargs(),
                )
                await process.communicate()
        destroyed_at = utcnow_iso()
        status = "expired" if reason.endswith("_expired") else "collected"
        return store_service.upsert_session_runtime(
            session_id=session_id,
            conversation_id=record.get("conversation_id"),
            platform_id=record.get("platform_id"),
            owner_user_id=record.get("owner_user_id"),
            external_user_id=record.get("external_user_id"),
            container_name=record.get("container_name"),
            container_id=record.get("container_id"),
            image=str(record.get("image") or settings.sandbox_docker_image),
            status=status,
            generation=int(record.get("generation") or 0),
            network_mode=str(record.get("network_mode") or self._runtime_network_mode()),
            created_at=str(record.get("created_at") or destroyed_at),
            updated_at=destroyed_at,
            last_started_at=record.get("last_started_at"),
            last_used_at=record.get("last_used_at"),
            idle_expires_at=record.get("idle_expires_at"),
            max_expires_at=record.get("max_expires_at"),
            destroyed_at=destroyed_at,
            destroy_reason=reason,
            restart_count=int(record.get("restart_count") or 0),
            workspace_root=str(record.get("workspace_root") or ""),
            home_root=str(record.get("home_root") or ""),
            metadata=record.get("metadata") or {},
        )

    async def _persist_runtime(
        self,
        *,
        session_id: str,
        workspace: SandboxWorkspace,
        runtime: dict[str, Any],
        status: str,
        last_used_at: datetime,
        idle_expires_at: str,
    ) -> dict[str, Any]:
        return store_service.upsert_session_runtime(
            session_id=session_id,
            conversation_id=runtime.get("conversation_id"),
            platform_id=runtime.get("platform_id"),
            owner_user_id=runtime.get("owner_user_id"),
            external_user_id=runtime.get("external_user_id"),
            container_name=runtime.get("container_name"),
            container_id=runtime.get("container_id"),
            image=str(runtime.get("image") or settings.sandbox_docker_image),
            status=status,
            generation=int(runtime.get("generation") or 0),
            network_mode=str(runtime.get("network_mode") or self._runtime_network_mode()),
            created_at=str(runtime.get("created_at") or utcnow_iso()),
            updated_at=utcnow_iso(),
            last_started_at=runtime.get("last_started_at"),
            last_used_at=self._to_iso(last_used_at),
            idle_expires_at=idle_expires_at,
            max_expires_at=runtime.get("max_expires_at"),
            destroyed_at=None if status in {"running", "executing", "terminating", "syncing"} else runtime.get("destroyed_at"),
            destroy_reason=None if status in {"running", "executing", "terminating", "syncing"} else runtime.get("destroy_reason"),
            restart_count=int(runtime.get("restart_count") or 0),
            workspace_root=str(workspace.root),
            home_root=str(workspace.home_dir),
            metadata=runtime.get("metadata") or self._build_runtime_metadata(str(runtime.get("image") or ""), workspace),
        )

    def _build_run_args(self, workspace: SandboxWorkspace, container_name: str, image: str) -> list[str]:
        args = [
            "run",
            "-d",
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
            "--mount",
            (
                "type=bind,"
                f"src={workspace.root.resolve()},"
                "dst=/aether/session-host"
            ),
            "--env",
            f"AETHER_SESSION_ID={workspace.session_id}",
            "--env",
            f"AETHER_SANDBOX_ROOT={settings.sandbox_docker_workspace_mount}",
            "--env",
            f"AETHER_SKILLS_DIR={settings.sandbox_docker_skills_dir}",
            "--env",
            f"AETHER_WORK_DIR={settings.sandbox_docker_work_dir}",
            "--env",
            f"AETHER_LOGS_DIR={settings.sandbox_docker_logs_dir}",
            "--env",
            f"HOME={settings.sandbox_docker_home_dir}",
            "--env",
            (
                "PATH="
                f"{settings.sandbox_docker_home_dir}/.local/bin:"
                "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            ),
            "--env",
            f"XDG_CACHE_HOME={settings.sandbox_docker_cache_dir}",
            "--env",
            f"XDG_CONFIG_HOME={settings.sandbox_docker_home_dir}/.config",
            "--env",
            "PYTHONIOENCODING=utf-8",
            "--env",
            "TERM=xterm-256color",
            "--env",
            "FORCE_COLOR=1",
            "--env",
            "CLICOLOR_FORCE=1",
            "--env",
            f"PIP_CACHE_DIR={settings.sandbox_docker_cache_dir}/pip",
            "--env",
            "PIP_ROOT_USER_ACTION=ignore",
            "--env",
            f"DOTNET_CLI_HOME={settings.sandbox_docker_home_dir}",
        ]
        for env_name, env_value in self._build_passthrough_env_vars().items():
            args.extend(["--env", f"{env_name}={env_value}"])
        if settings.sandbox_docker_read_only_rootfs:
            args.append("--read-only")
        for tmpfs in settings.sandbox_docker_tmpfs:
            args.extend(["--tmpfs", tmpfs])
        network_mode = self._runtime_network_mode()
        args.extend(["--network", network_mode])
        if network_mode != "none":
            for dns_server in self._runtime_dns_servers():
                if dns_server.strip():
                    args.extend(["--dns", dns_server.strip()])
        args.append(image)
        args.extend(
            [
                "/bin/bash",
                "-lc",
                self._build_runtime_bootstrap_script(workspace),
            ]
        )
        return args

    def _build_exec_args(self, container_name: str, shell: str, command: str) -> list[str]:
        args = [
            "exec",
            "--user",
            settings.sandbox_docker_user.strip() or f"{self._sandbox_uid()}:{self._sandbox_gid()}",
            "--workdir",
            settings.sandbox_docker_work_dir,
            "--env",
            f"HOME={settings.sandbox_docker_home_dir}",
            "--env",
            (
                "PATH="
                f"{settings.sandbox_docker_home_dir}/.local/bin:"
                "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            ),
            "--env",
            f"XDG_CACHE_HOME={settings.sandbox_docker_cache_dir}",
            "--env",
            f"XDG_CONFIG_HOME={settings.sandbox_docker_home_dir}/.config",
            "--env",
            f"PIP_CACHE_DIR={settings.sandbox_docker_cache_dir}/pip",
            "--env",
            f"PYTHONUSERBASE={settings.sandbox_docker_home_dir}/.local",
            "--env",
            f"AETHER_SANDBOX_ROOT={settings.sandbox_docker_workspace_mount}",
            "--env",
            f"AETHER_SKILLS_DIR={settings.sandbox_docker_skills_dir}",
            "--env",
            f"AETHER_WORK_DIR={settings.sandbox_docker_work_dir}",
            "--env",
            f"AETHER_LOGS_DIR={settings.sandbox_docker_logs_dir}",
            "--env",
            "TERM=xterm-256color",
            "--env",
            "COLORTERM=truecolor",
            "--env",
            "FORCE_COLOR=1",
            "--env",
            "CLICOLOR_FORCE=1",
            "--env",
            "PYTHONUNBUFFERED=1",
            container_name,
        ]
        if shell == "bash":
            return [*args, "/bin/bash", "-lc", command]
        if shell == "powershell":
            return [*args, "pwsh", "-NoLogo", "-NoProfile", "-Command", command]
        raise RuntimeError(f"暂不支持的 shell 类型: {shell}")

    def _wrap_command_for_terminal(self, command: str) -> str:
        quoted = shlex.quote(command)
        return (
            "if command -v script >/dev/null 2>&1; then "
            f"script -q -e -f -c {quoted} /dev/null; "
            f"else exec /bin/bash -lc {quoted}; fi"
        )

    async def _inspect_container_state(self, container_name: str) -> str | None:
        if not container_name:
            return None
        docker_binary = self._resolve_docker_binary()
        if not docker_binary:
            return None
        process = await asyncio.create_subprocess_exec(
            docker_binary,
            "inspect",
            "--format",
            "{{.State.Status}}",
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **self._subprocess_kwargs(),
        )
        stdout_bytes, _ = await process.communicate()
        if process.returncode != 0:
            return None
        return self._decode_output(stdout_bytes).strip() or None

    def _detect_expired_reason(self, runtime: dict[str, Any], now: datetime) -> str | None:
        if runtime.get("status") == "missing":
            return None
        if runtime.get("status") in {"failed", "collected", "expired"}:
            return runtime.get("destroy_reason") or "runtime_unavailable"
        idle_expires_at = self._parse_dt(runtime.get("idle_expires_at"))
        if idle_expires_at and idle_expires_at <= now:
            return "idle_ttl_expired"
        max_expires_at = self._parse_dt(runtime.get("max_expires_at"))
        if max_expires_at and max_expires_at <= now:
            return "max_age_expired"
        return None

    def _build_container_name(self, session_id: str, generation: int) -> str:
        return f"aethercore-sess-{session_id[:18]}-g{generation}"

    def _runtime_network_mode(self) -> str:
        if not settings.sandbox_allow_network:
            return "none"
        configured = settings.sandbox_docker_network_mode.strip()
        return configured or "bridge"

    def _build_runtime_bootstrap_script(self, workspace: SandboxWorkspace) -> str:
        if workspace.baseline_root is None:
            return (
                "mkdir -p "
                f"{shlex.quote(settings.sandbox_docker_skills_dir)} "
                f"{shlex.quote(settings.sandbox_docker_work_dir)} "
                f"{shlex.quote(settings.sandbox_docker_logs_dir)} "
                f"{shlex.quote(settings.sandbox_docker_home_dir)} "
                f"{shlex.quote(settings.sandbox_docker_cache_dir)}"
                "; while true; do sleep 3600; done"
            )

        return (
            "mkdir -p "
            f"{shlex.quote(settings.sandbox_docker_skills_dir)} "
            f"{shlex.quote(settings.sandbox_docker_work_dir)} "
            f"{shlex.quote(settings.sandbox_docker_logs_dir)} "
            f"{shlex.quote(settings.sandbox_docker_home_dir)} "
            f"{shlex.quote(settings.sandbox_docker_cache_dir)}"
            "; while true; do sleep 3600; done"
        )

    def _build_runtime_metadata(self, image: str, workspace: SandboxWorkspace) -> dict[str, Any]:
        return {
            "runtime_spec": self._build_runtime_spec(image, workspace),
        }

    def _build_runtime_spec(self, image: str | None = None, workspace: SandboxWorkspace | None = None) -> dict[str, Any]:
        return {
            "image": image or settings.sandbox_docker_image,
            "network_mode": self._runtime_network_mode(),
            "dns_servers": self._runtime_dns_servers(),
            "user": settings.sandbox_docker_user.strip() or f"{self._sandbox_uid()}:{self._sandbox_gid()}",
            "read_only_rootfs": bool(settings.sandbox_docker_read_only_rootfs),
            "workspace_mount": settings.sandbox_docker_workspace_mount,
            "work_dir": settings.sandbox_docker_work_dir,
            "home_dir": settings.sandbox_docker_home_dir,
            "cache_dir": settings.sandbox_docker_cache_dir,
            "baseline_mode": "image" if workspace and workspace.baseline_root is not None else "direct",
            "baseline_root": str(workspace.baseline_root.resolve()) if workspace and workspace.baseline_root is not None else "",
        }

    def _sandbox_uid(self) -> int:
        configured = settings.sandbox_docker_user.strip()
        if ":" in configured:
            user, _, _ = configured.partition(":")
            if user.isdigit():
                return int(user)
        if configured.isdigit():
            return int(configured)
        return self._DEFAULT_SANDBOX_UID

    def _sandbox_gid(self) -> int:
        configured = settings.sandbox_docker_user.strip()
        if ":" in configured:
            _, _, group = configured.partition(":")
            if group.isdigit():
                return int(group)
            user, _, _ = configured.partition(":")
            if user.isdigit():
                return int(user)
        if configured.isdigit():
            return int(configured)
        return self._DEFAULT_SANDBOX_GID

    def _detect_runtime_recreate_reason(
        self,
        runtime: dict[str, Any],
        now: datetime,
        workspace: SandboxWorkspace | None = None,
    ) -> str | None:
        expired_reason = self._detect_expired_reason(runtime, now)
        if expired_reason is not None:
            return expired_reason
        return self._detect_runtime_spec_drift(runtime, workspace)

    def _detect_runtime_spec_drift(self, runtime: dict[str, Any], workspace: SandboxWorkspace | None = None) -> str | None:
        if runtime.get("status") == "missing":
            return None
        metadata = runtime.get("metadata") or {}
        current_spec = metadata.get("runtime_spec")
        desired_spec = self._build_runtime_spec(
            self._resolve_runtime_image({"platform_id": runtime.get("platform_id")}),
            workspace,
        )
        if not current_spec:
            return "runtime_spec_missing"
        if current_spec != desired_spec:
            return "runtime_config_changed"
        return None

    def _resolve_runtime_image(self, conversation: dict[str, Any]) -> str:
        platform_id = conversation.get("platform_id")
        return platform_runtime_image_service.resolve_for_platform(int(platform_id)) if platform_id is not None else settings.sandbox_docker_image

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

    def _require_docker_binary(self) -> str:
        docker_binary = self._resolve_docker_binary()
        if not docker_binary:
            raise RuntimeError("未找到 docker 可执行文件，无法启动容器沙箱。")
        return docker_binary

    def _subprocess_kwargs(self) -> dict[str, Any]:
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            return {"creationflags": subprocess.CREATE_NO_WINDOW}
        return {}

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

    async def _run_shell_locked(
        self,
        workspace: SandboxWorkspace,
        *,
        command: str,
        shell: str,
        timeout_seconds: int | None,
        allow_network_recovery: bool,
        session: AgentSession | None,
        run_id: str | None,
        output_callback: SandboxOutputCallback | None,
    ) -> SandboxCommandResult:
        runtime_state = await self._ensure_runtime_locked(workspace)
        runtime = runtime_state["runtime"]
        effective_timeout = self._effective_timeout_seconds(timeout_seconds)
        runtime_status = str(runtime.get("status") or "")
        if runtime_status in {"executing", "terminating"}:
            refreshed = await self._wait_for_runtime_ready_locked(workspace, runtime)
            refreshed_status = str(refreshed.get("status") or "")
            if refreshed_status != "running":
                raise RuntimeBusyError(
                    session_id=workspace.session_id,
                    summary="前一个命令仍在退出中，当前沙箱暂时不可用。可以继续等待，或调用 rebuild_runtime 重建沙箱。",
                    runtime={
                        "status": refreshed_status,
                        "generation": refreshed.get("generation"),
                        "container_name": refreshed.get("container_name"),
                        "destroy_reason": refreshed.get("destroy_reason"),
                    },
                )
            runtime = refreshed
        elif runtime_status in {"failed_start", "failed_runtime", "exited"}:
            raise RuntimeStartError(
                session_id=workspace.session_id,
                summary="沙箱 runtime 未能处于可执行状态，请重建运行环境。",
                runtime={
                    "status": runtime_status,
                    "generation": runtime.get("generation"),
                    "container_name": runtime.get("container_name"),
                    "destroy_reason": runtime.get("destroy_reason"),
                    "image": runtime.get("image"),
                },
            )
        container_name = str(runtime["container_name"])
        now = self._now()
        await self._persist_runtime(
            session_id=workspace.session_id,
            workspace=workspace,
            runtime=runtime,
            status="executing",
            last_used_at=now,
            idle_expires_at=self._to_iso(now + timedelta(seconds=settings.sandbox_runtime_idle_ttl_seconds)),
        )
        await session_workspace_sync_service.hydrate_container(
            docker_binary=self._require_docker_binary(),
            container_name=container_name,
            workspace=workspace,
        )
        started_at = time.perf_counter()
        try:
            process = await self._create_exec_process(container_name, shell, command)
            if session is not None and run_id is not None:
                session.set_tool_task(run_id, process)
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                self._collect_stream_output(process, output_callback=output_callback),
                timeout=effective_timeout,
            )
        except asyncio.CancelledError:
            await self._terminate_process(process, container_name=container_name)
            now = self._now()
            await self._persist_runtime(
                session_id=workspace.session_id,
                workspace=workspace,
                runtime=runtime,
                status="terminating",
                last_used_at=now,
                idle_expires_at=self._to_iso(now + timedelta(seconds=settings.sandbox_runtime_idle_ttl_seconds)),
            )
            raise
        except asyncio.TimeoutError:
            await self._terminate_process(process, container_name=container_name)
            now = self._now()
            await self._persist_runtime(
                session_id=workspace.session_id,
                workspace=workspace,
                runtime=runtime,
                status="failed_runtime",
                last_used_at=now,
                idle_expires_at=self._to_iso(now + timedelta(seconds=settings.sandbox_runtime_idle_ttl_seconds)),
            )
            raise RuntimeBusyError(
                session_id=workspace.session_id,
                summary=f"命令执行超过 {effective_timeout} 秒仍未完成，当前沙箱可能卡住。可以继续等待，或调用 rebuild_runtime 重建沙箱。",
                runtime={
                    "status": "failed_runtime",
                    "generation": runtime.get("generation"),
                    "container_name": runtime.get("container_name"),
                    "destroy_reason": "command_timeout",
                },
            ) from None
        finally:
            if session is not None and run_id is not None:
                session.set_tool_task(run_id, None)

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        stdout_text = self._decode_output(stdout_bytes)
        stderr_text = self._decode_output(stderr_bytes)
        exit_code = process.returncode or 0

        if (
            allow_network_recovery
            and self._runtime_network_mode() != "none"
            and exit_code != 0
            and self._is_retryable_dns_failure(stdout_text, stderr_text)
        ):
            await self._collect_locked(workspace.session_id, reason="dns_resolution_failure")
            return await self._run_shell_locked(
                workspace,
                command=command,
                shell=shell,
                timeout_seconds=effective_timeout,
                allow_network_recovery=False,
                session=session,
                run_id=run_id,
                output_callback=output_callback,
            )

        now = self._now()
        await session_workspace_sync_service.capture_container_delta(
            docker_binary=self._require_docker_binary(),
            container_name=container_name,
            workspace=workspace,
        )
        runtime = await self._persist_runtime(
            session_id=workspace.session_id,
            workspace=workspace,
            runtime=runtime,
            status="running",
            last_used_at=now,
            idle_expires_at=self._to_iso(now + timedelta(seconds=settings.sandbox_runtime_idle_ttl_seconds)),
        )
        return SandboxCommandResult(
            command=command,
            shell=shell,
            executor="docker",
            exit_code=exit_code,
            stdout=self._truncate(stdout_text),
            stderr=self._truncate(stderr_text),
            duration_ms=duration_ms,
            log_path="",
            runtime_metadata=runtime_state.get("notice")
            or {
                "status": runtime["status"],
                "generation": runtime["generation"],
                "container_name": runtime["container_name"],
                "idle_expires_at": runtime["idle_expires_at"],
                "timeout_seconds": effective_timeout,
},
        )

    def _effective_timeout_seconds(self, requested_timeout: int | None) -> int:
        default_timeout = max(1, int(settings.sandbox_command_timeout_seconds))
        max_timeout = int(settings.sandbox_command_max_timeout_seconds)
        if max_timeout <= 0:
            if requested_timeout is None:
                return default_timeout
            return max(1, int(requested_timeout))
        max_timeout = max(default_timeout, max_timeout)
        if requested_timeout is None:
            return min(default_timeout, max_timeout)
        return max(1, min(int(requested_timeout), max_timeout))

    async def _wait_for_runtime_ready_locked(
        self,
        workspace: SandboxWorkspace,
        runtime: dict[str, Any],
    ) -> dict[str, Any]:
        deadline = time.perf_counter() + max(0, settings.sandbox_runtime_busy_wait_seconds)
        current = runtime
        while time.perf_counter() < deadline:
            state = await self._inspect_container_state(str(current.get("container_name") or ""))
            if state == "running":
                now = self._now()
                current = await self._persist_runtime(
                    session_id=workspace.session_id,
                    workspace=workspace,
                    runtime=current,
                    status="running",
                    last_used_at=now,
                    idle_expires_at=self._to_iso(now + timedelta(seconds=settings.sandbox_runtime_idle_ttl_seconds)),
                )
                return current
            await asyncio.sleep(0.2)
            current = await self.refresh_runtime(workspace.session_id)
        return await self.refresh_runtime(workspace.session_id)

    def _map_container_state_to_runtime_status(self, state: str) -> str:
        normalized = str(state or "").strip().lower()
        mapping = {
            "running": "running",
            "created": "provisioning",
            "restarting": "terminating",
            "paused": "running",
            "dead": "failed_runtime",
            "exited": "failed_runtime",
            "removing": "terminating",
        }
        return mapping.get(normalized, normalized or "failed_runtime")

    async def _collect_stream_output(
        self,
        process: asyncio.subprocess.Process,
        *,
        output_callback: SandboxOutputCallback | None,
    ) -> tuple[bytes, bytes]:
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []

        async def read_stream(stream: asyncio.StreamReader | None, sink: list[bytes], stream_name: str) -> None:
            if stream is None:
                return
            while True:
                try:
                    chunk = await stream.read(4096)
                    if not chunk:
                        break
                    sink.append(chunk)
                    if output_callback is not None:
                        await output_callback(stream_name, self._decode_output(chunk))
                except OSError:
                    break

        await asyncio.gather(
            read_stream(process.stdout, stdout_chunks, "stdout"),
            read_stream(process.stderr, stderr_chunks, "stderr"),
        )
        await process.wait()

        pty_pipe = getattr(process, "_pty_pipe", None)
        if pty_pipe is not None:
            try:
                pty_pipe.close()
            except OSError:
                pass

        pty_master_fd = getattr(process, "_pty_master_fd", None)
        if pty_master_fd is not None:
            try:
                os.close(pty_master_fd)
            except OSError:
                pass

        return b"".join(stdout_chunks), b"".join(stderr_chunks)

    async def _create_exec_process(
        self,
        container_name: str,
        shell: str,
        command: str,
    ) -> asyncio.subprocess.Process:
        docker_binary = self._require_docker_binary()
        exec_command = self._wrap_command_for_terminal(command) if shell == "bash" else command
        exec_args = ["exec", "-i", *self._build_exec_args(container_name, shell, exec_command)[1:]]

        if os.name == "nt" or not hasattr(os, "fork"):
            return await asyncio.create_subprocess_exec(
                docker_binary,
                *exec_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **self._subprocess_kwargs(),
            )

        try:
            import pty
        except ImportError:
            return await asyncio.create_subprocess_exec(
                docker_binary,
                *exec_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **self._subprocess_kwargs(),
            )

        master_fd = None
        slave_fd = None
        pipe = None

        try:
            master_fd, slave_fd = pty.openpty()
        except OSError:
            return await asyncio.create_subprocess_exec(
                docker_binary,
                *exec_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **self._subprocess_kwargs(),
            )

        try:
            process = await asyncio.create_subprocess_exec(
                docker_binary,
                *exec_args,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                **self._subprocess_kwargs(),
            )
        except Exception:
            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass
            raise
        finally:
            if slave_fd is not None:
                try:
                    os.close(slave_fd)
                except OSError:
                    pass

        try:
            loop = asyncio.get_running_loop()
            pipe = os.fdopen(master_fd, "rb", buffering=0)
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            await loop.connect_read_pipe(lambda: protocol, pipe)
        except Exception:
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass
            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass
            raise

        process.stdout = reader  # type: ignore[attr-defined]
        process.stderr = None  # type: ignore[attr-defined]
        process._pty_master_fd = master_fd  # type: ignore[attr-defined]
        process._pty_pipe = pipe  # type: ignore[attr-defined]
        return process

    async def _terminate_process(
        self,
        process: asyncio.subprocess.Process,
        *,
        container_name: str | None = None,
    ) -> None:
        if process.returncode is not None:
            return

        pty_pipe = getattr(process, "_pty_pipe", None)
        pty_master_fd = getattr(process, "_pty_master_fd", None)

        process.terminate()
        try:
            if pty_pipe is None and process.stdout is not None:
                await asyncio.wait_for(process.communicate(), timeout=settings.sandbox_cancel_grace_seconds)
            else:
                await asyncio.wait_for(process.wait(), timeout=settings.sandbox_cancel_grace_seconds)
            return
        except (asyncio.TimeoutError, ProcessLookupError):
            pass

        if process.returncode is None:
            process.kill()

        try:
            if pty_pipe is None and process.stdout is not None:
                await asyncio.wait_for(process.communicate(), timeout=settings.sandbox_force_kill_grace_seconds)
            else:
                await asyncio.wait_for(process.wait(), timeout=settings.sandbox_force_kill_grace_seconds)
        except (asyncio.TimeoutError, ProcessLookupError):
            if container_name:
                docker_binary = self._resolve_docker_binary()
                if docker_binary:
                    forced = await asyncio.create_subprocess_exec(
                        docker_binary,
                        "rm",
                        "-f",
                        container_name,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        **self._subprocess_kwargs(),
                    )
                    await forced.communicate()

        if pty_pipe is not None:
            try:
                pty_pipe.close()
            except OSError:
                pass

        if pty_master_fd is not None:
            try:
                os.close(pty_master_fd)
            except OSError:
                pass

    def _runtime_dns_servers(self) -> list[str]:
        configured = [item.strip() for item in settings.sandbox_docker_dns_servers if item.strip()]
        if configured:
            return configured
        if not settings.sandbox_docker_auto_dns_from_host:
            return []
        discovered = self._discover_host_dns_servers()
        return list(discovered)

    def _discover_host_dns_servers(self) -> tuple[str, ...]:
        if self._host_dns_servers_cache is not None:
            return self._host_dns_servers_cache
        discovered: list[str] = []
        if os.name == "nt":
            discovered = self._discover_windows_dns_servers()
        else:
            discovered = self._discover_resolv_conf_dns_servers(Path("/etc/resolv.conf"))
        if discovered:
            self._host_dns_servers_cache = tuple(discovered)
            return self._host_dns_servers_cache
        return ()

    def _discover_windows_dns_servers(self) -> list[str]:
        try:
            process = subprocess.run(
                [
                    "powershell",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    (
                        "Get-DnsClientServerAddress -AddressFamily IPv4 "
                        "| Select-Object -ExpandProperty ServerAddresses "
                        "| Where-Object { $_ } "
                        "| Select-Object -Unique"
                    ),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=5,
                **self._subprocess_kwargs(),
            )
        except Exception:
            return []
        if process.returncode != 0:
            return []
        return self._normalize_dns_servers(process.stdout.splitlines())

    def _discover_resolv_conf_dns_servers(self, resolv_conf_path: Path) -> list[str]:
        try:
            lines = resolv_conf_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        values: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or not stripped.startswith("nameserver "):
                continue
            _, _, candidate = stripped.partition(" ")
            values.append(candidate.strip())
        return self._normalize_dns_servers(values)

    def _normalize_dns_servers(self, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            candidate = value.strip()
            if not candidate or candidate in seen:
                continue
            try:
                parsed = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified:
                continue
            normalized.append(candidate)
            seen.add(candidate)
        return normalized

    def _build_passthrough_env_vars(self) -> dict[str, str]:
        passthrough: dict[str, str] = {}
        for env_name in settings.sandbox_docker_env_passthrough:
            if not env_name:
                continue
            env_value = os.environ.get(env_name)
            if env_value:
                passthrough[env_name] = env_value
        return passthrough

    def _is_retryable_dns_failure(self, stdout_text: str, stderr_text: str) -> bool:
        combined = f"{stdout_text}\n{stderr_text}".lower()
        markers = (
            "temporary failure in name resolution",
            "name or service not known",
            "nodename nor servname provided",
            "failed to establish a new connection",
            "could not resolve host",
            "dial tcp: lookup ",
            "getaddrinfo failed",
        )
        return any(marker in combined for marker in markers)

    def _parse_dt(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    def _to_iso(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)


session_runtime_service = SessionRuntimeService()
