from __future__ import annotations

import asyncio
import json
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.sandbox.models import SandboxWorkspace


@dataclass(frozen=True)
class WorkspaceState:
    tombstones: tuple[str, ...] = ()


class SessionWorkspaceSyncService:
    """负责容器工作区与宿主会话增量目录之间的双向同步。"""

    _STATE_FILE_NAME = "workspace_state.json"
    _SYNC_SECTIONS = ("work", "skills")

    def state_path(self, workspace: SandboxWorkspace) -> Path:
        return workspace.metadata_dir / self._STATE_FILE_NAME

    def load_state(self, workspace: SandboxWorkspace) -> WorkspaceState:
        path = self.state_path(workspace)
        if not path.exists():
            return WorkspaceState()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return WorkspaceState()
        tombstones = tuple(
            self._normalize_relative_path(item)
            for item in payload.get("tombstones", [])
            if self._normalize_relative_path(item)
        )
        return WorkspaceState(tombstones=self._minimize_paths(tombstones))

    def save_state(self, workspace: SandboxWorkspace, state: WorkspaceState) -> None:
        payload = {
            "version": 1,
            "tombstones": list(self._minimize_paths(state.tombstones)),
        }
        self.state_path(workspace).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def is_deleted(self, workspace: SandboxWorkspace, relative_path: str) -> bool:
        normalized = self._normalize_relative_path(relative_path)
        if not normalized:
            return False
        for tombstone in self.load_state(workspace).tombstones:
            if normalized == tombstone or normalized.startswith(f"{tombstone}/"):
                return True
        return False

    async def hydrate_container(
        self,
        *,
        docker_binary: str,
        container_name: str,
        workspace: SandboxWorkspace,
    ) -> None:
        state = self.load_state(workspace)
        await self._ensure_container_dirs(
            docker_binary=docker_binary,
            container_name=container_name,
        )
        if state.tombstones:
            quoted_paths = " ".join(
                self._shell_quote(f"/workspace/{path}")
                for path in state.tombstones
            )
            await self._run_container_command(
                docker_binary=docker_binary,
                container_name=container_name,
                command=f"rm -rf -- {quoted_paths}",
            )

        archive_path = self._build_delta_archive(workspace)
        if archive_path is None:
            return
        try:
            process = await asyncio.create_subprocess_exec(
                docker_binary,
                "exec",
                "-i",
                "--user",
                "sandbox",
                container_name,
                "/bin/bash",
                "-lc",
                "tar -xf - -C /",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await process.communicate(archive_path.read_bytes())
            if process.returncode != 0:
                error_text = self._decode_output(stderr_bytes).strip() or self._decode_output(stdout_bytes).strip()
                raise RuntimeError(error_text or "导入会话增量失败。")
        finally:
            archive_path.unlink(missing_ok=True)

    async def capture_container_delta(
        self,
        *,
        docker_binary: str,
        container_name: str,
        workspace: SandboxWorkspace,
    ) -> WorkspaceState:
        diff_entries = await self._list_container_diff(
            docker_binary=docker_binary,
            container_name=container_name,
        )
        changed_paths: list[str] = []
        tombstones: list[str] = []
        for kind, relative_path in diff_entries:
            if not self._is_synced_relative_path(relative_path):
                continue
            if kind == "D":
                tombstones.append(relative_path)
            else:
                changed_paths.append(relative_path)

        minimized_changes = self._minimize_paths(changed_paths)
        minimized_tombstones = self._minimize_paths(tombstones)

        self._reset_delta_directories(workspace)
        if minimized_changes:
            staging_root = Path(tempfile.mkdtemp(prefix="aethercore-delta-stage-"))
            try:
                for relative_path in minimized_changes:
                    await self._copy_path_from_container(
                        docker_binary=docker_binary,
                        container_name=container_name,
                        relative_path=relative_path,
                        staging_root=staging_root,
                    )
                self._materialize_staging(staging_root, workspace)
            finally:
                shutil.rmtree(staging_root, ignore_errors=True)

        state = WorkspaceState(tombstones=tuple(minimized_tombstones))
        self.save_state(workspace, state)
        return state

    def _build_delta_archive(self, workspace: SandboxWorkspace) -> Path | None:
        has_delta = False
        temp_file = tempfile.NamedTemporaryFile(
            prefix="aethercore-delta-",
            suffix=".tar",
            delete=False,
        )
        archive_path = Path(temp_file.name)
        temp_file.close()
        try:
            with tarfile.open(archive_path, mode="w") as handle:
                for section in self._SYNC_SECTIONS:
                    section_dir = getattr(workspace, f"{section}_dir")
                    if not section_dir.exists():
                        continue
                    for child in sorted(section_dir.iterdir(), key=lambda item: item.name.lower()):
                        has_delta = True
                        arcname = Path("workspace") / section / child.name
                        handle.add(child, arcname=str(arcname))
            if not has_delta:
                archive_path.unlink(missing_ok=True)
                return None
            return archive_path
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise

    async def _list_container_diff(
        self,
        *,
        docker_binary: str,
        container_name: str,
    ) -> list[tuple[str, str]]:
        process = await asyncio.create_subprocess_exec(
            docker_binary,
            "diff",
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        if process.returncode != 0:
            error_text = self._decode_output(stderr_bytes).strip() or self._decode_output(stdout_bytes).strip()
            raise RuntimeError(error_text or "读取容器差异失败。")

        entries: list[tuple[str, str]] = []
        for raw_line in self._decode_output(stdout_bytes).splitlines():
            line = raw_line.strip()
            if len(line) < 3 or line[1] != " ":
                continue
            kind = line[0]
            path = line[2:].strip()
            relative_path = self._normalize_container_path(path)
            if relative_path:
                entries.append((kind, relative_path))
        return entries

    async def _copy_path_from_container(
        self,
        *,
        docker_binary: str,
        container_name: str,
        relative_path: str,
        staging_root: Path,
    ) -> None:
        target_root = staging_root / Path(relative_path).parent
        target_root.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            docker_binary,
            "cp",
            f"{container_name}:/workspace/{relative_path}",
            str(target_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        if process.returncode != 0:
            error_text = self._decode_output(stderr_bytes).strip() or self._decode_output(stdout_bytes).strip()
            raise RuntimeError(error_text or f"导出容器路径失败: {relative_path}")

    async def _ensure_container_dirs(
        self,
        *,
        docker_binary: str,
        container_name: str,
    ) -> None:
        await self._run_container_command(
            docker_binary=docker_binary,
            container_name=container_name,
            command="mkdir -p /workspace/work /workspace/skills",
        )

    async def _run_container_command(
        self,
        *,
        docker_binary: str,
        container_name: str,
        command: str,
    ) -> None:
        process = await asyncio.create_subprocess_exec(
            docker_binary,
            "exec",
            "--user",
            "sandbox",
            container_name,
            "/bin/bash",
            "-lc",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        if process.returncode != 0:
            error_text = self._decode_output(stderr_bytes).strip() or self._decode_output(stdout_bytes).strip()
            raise RuntimeError(error_text or "执行容器同步命令失败。")

    def _reset_delta_directories(self, workspace: SandboxWorkspace) -> None:
        for section in self._SYNC_SECTIONS:
            self._clear_directory(getattr(workspace, f"{section}_dir"))

    def _materialize_staging(self, staging_root: Path, workspace: SandboxWorkspace) -> None:
        for section in self._SYNC_SECTIONS:
            source_dir = staging_root / section
            target_dir = getattr(workspace, f"{section}_dir")
            if not source_dir.exists():
                continue
            for child in source_dir.iterdir():
                destination = target_dir / child.name
                if child.is_dir():
                    shutil.copytree(child, destination, dirs_exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(child, destination)

    def _clear_directory(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for child in directory.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)

    def _is_synced_relative_path(self, relative_path: str) -> bool:
        return any(
            relative_path == section or relative_path.startswith(f"{section}/")
            for section in self._SYNC_SECTIONS
        )

    def _normalize_container_path(self, path: str) -> str:
        normalized = path.replace("\\", "/").strip()
        if not normalized.startswith("/workspace/"):
            return ""
        return self._normalize_relative_path(normalized[len("/workspace/") :])

    def _normalize_relative_path(self, value: str) -> str:
        normalized = str(value or "").replace("\\", "/").strip("/")
        return normalized

    def _minimize_paths(self, paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        normalized = sorted(
            {self._normalize_relative_path(path) for path in paths if self._normalize_relative_path(path)},
            key=lambda item: (item.count("/"), item),
        )
        kept: list[str] = []
        for path in normalized:
            if any(path == existing or path.startswith(f"{existing}/") for existing in kept):
                continue
            kept.append(path)
        return tuple(kept)

    def _shell_quote(self, value: str) -> str:
        escaped = value.replace("'", "'\"'\"'")
        return f"'{escaped}'"

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


session_workspace_sync_service = SessionWorkspaceSyncService()
