from __future__ import annotations

import asyncio
import json
import os
import uuid
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.sandbox.models import SandboxWorkspace
from app.sandbox.workspace_sync_helper import describe, safe_path


@dataclass(frozen=True)
class WorkspaceState:
    tombstones: tuple[str, ...] = ()


class WorkspaceSyncService:
    """负责容器与宿主 Workspace 增量目录之间的双向同步。"""

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
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("工作区删除记录不可读，已停止同步以保护历史文件。") from exc
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
        self._write_json(self.state_path(workspace), payload)

    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _checkpoint_path(self, workspace: SandboxWorkspace) -> Path:
        return workspace.metadata_dir / "workspace_sync_checkpoint.json"

    def _checkpoint(self, workspace: SandboxWorkspace, container_name: str) -> dict:
        path = self._checkpoint_path(workspace)
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if payload.get("container") == container_name else {}

    def has_pending_host_changes(self, workspace: SandboxWorkspace, container_name: str) -> bool:
        checkpoint = self._checkpoint(workspace, container_name)
        return bool(checkpoint) and (checkpoint.get("host", {}) != self._host_entries(workspace)
            or checkpoint.get("tombstones", []) != list(self.load_state(workspace).tombstones))

    def _same_entry(self, left: dict | None, right: dict | None) -> bool:
        if os.name == "nt" and left is not None and right is not None:
            # Windows 宿主不能表达 Linux 的完整权限位，仍校验类型、大小和内容。
            return {k: v for k, v in left.items() if k != "mode"} == {k: v for k, v in right.items() if k != "mode"}
        return left == right

    def _host_entries(self, workspace: SandboxWorkspace) -> dict:
        entries = {}
        for section in self._SYNC_SECTIONS:
            root = workspace.root / section
            if root.is_symlink():
                raise RuntimeError(f"工作区根目录不能是符号链接: {section}")
            if not root.exists():
                continue
            for directory, directories, files in os.walk(root, followlinks=False):
                for name in directories + files:
                    path = Path(directory) / name
                    relative = path.relative_to(workspace.root).as_posix()
                    safe_path(workspace.root, relative)
                    entry = describe(path)
                    if entry["type"] == "symlink" and not (path.parent / entry["target"]).resolve().is_relative_to(workspace.root.resolve()):
                        raise RuntimeError(f"符号链接超出工作区: {relative}")
                    entries[relative] = entry
        return entries

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
        checkpoint = self._checkpoint(workspace, container_name)
        entries = self._host_entries(workspace)
        previous = checkpoint.get("host", {})
        state = self.load_state(workspace)
        changes = {path: entry for path, entry in entries.items() if previous.get(path) != entry}
        deleted = set(previous) - set(entries)
        deleted.update(set(state.tombstones) - set(checkpoint.get("tombstones", [])))
        if not changes and not deleted and checkpoint:
            return
        await self._ensure_container_dirs(docker_binary=docker_binary, container_name=container_name)
        # 删除导入前的旧类型，保证文件/目录替换不合并成错误的树。
        replacements = {path for path, entry in changes.items()
                        if entry["type"] != "directory" or previous.get(path, {}).get("type") not in {None, "directory"}}
        removals = self._minimize_paths(list(deleted | replacements))
        if removals:
            quoted = " ".join(self._shell_quote(f"/workspace/{path}") for path in removals)
            await self._run_container_command(docker_binary=docker_binary, container_name=container_name,
                                              command=f"rm -rf -- {quoted}")
            await self._ensure_container_dirs(docker_binary=docker_binary, container_name=container_name)
        archive_path = self._build_delta_archive(workspace, changes)
        if archive_path is not None:
            try:
                # 从文件描述符流式传输，避免整个会话归档进入 API 进程内存。
                with archive_path.open("rb") as archive:
                    process = await asyncio.create_subprocess_exec(
                        docker_binary, "exec", "-i", "--user", "sandbox", container_name,
                        "/bin/bash", "-lc", "tar -xf - -C /workspace",
                        stdin=archive, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await process.communicate()
                if process.returncode != 0:
                    raise RuntimeError(self._decode_output(stderr).strip() or "导入会话增量失败。")
            finally:
                archive_path.unlink(missing_ok=True)
        self._write_json(self._checkpoint_path(workspace), {
            **checkpoint, "container": container_name, "host": entries,
            "tombstones": list(state.tombstones),
        })

    async def capture_container_delta(
        self, *, docker_binary: str, container_name: str, workspace: SandboxWorkspace,
    ) -> WorkspaceState:
        checkpoint = self._checkpoint(workspace, container_name)
        diff = await self._list_container_diff(docker_binary=docker_binary, container_name=container_name)
        changed = sorted({path for kind, path in diff if kind in {"A", "C"} and self._is_synced_relative_path(path)})
        deleted = {path for kind, path in diff if kind == "D" and self._is_synced_relative_path(path)}
        entries = await self._inspect_container_paths(docker_binary, container_name, changed) if changed else {}
        if any(entry is None for entry in entries.values()):
            raise RuntimeError("同步期间文件发生变化，保留容器等待重试。")
        host = self._host_entries(workspace)
        # 若 API 在命令执行期间改写了同一路径，保留两端原件并要求重试，不静默覆盖上传。
        prior_host = checkpoint.get("host", {})
        for path, entry in entries.items():
            if checkpoint and host.get(path) != prior_host.get(path) and not self._same_entry(host.get(path), entry):
                raise RuntimeError(f"工作区文件存在并发修改，未覆盖宿主文件: {path}")
        for path in deleted:
            if any((item == path or item.startswith(path + "/")) and value != prior_host.get(item)
                   for item, value in host.items()):
                raise RuntimeError(f"删除路径存在并发上传，未删除宿主文件: {path}")
        exports = {path: entry for path, entry in entries.items()
                   if not self._same_entry(host.get(path), entry) and checkpoint.get("container_entries", {}).get(path) != entry}
        # 父目录是元数据项，永远不折叠其子文件。根目录自身无需导出。
        exports = {path: entry for path, entry in exports.items() if path not in self._SYNC_SECTIONS}
        tombstones = self._minimize_paths(list(deleted))
        required_bytes = sum(entry.get("size", 0) for entry in exports.values())
        if shutil.disk_usage(workspace.root).free < required_bytes * 3 + 16 * 1024 * 1024:
            raise RuntimeError("磁盘空间不足以安全同步增量，已保留容器等待释放空间。")
        with tempfile.TemporaryDirectory(prefix="aethercore-delta-", dir=workspace.metadata_dir) as temporary:
            staging = Path(temporary) / "staging"
            staging.mkdir()
            if exports:
                archive = Path(temporary) / "delta.tar"
                await self._export_container_entries(docker_binary, container_name, exports, archive)
                self._extract_entries(archive, staging, exports)
            if self._host_entries(workspace) != host:
                raise RuntimeError("导出期间宿主工作区发生变化，保留两端文件等待重试。")
            # 先完成导出和校验，再修改宿主；失败时不提前应用删除记录。
            self._apply_tombstones(workspace, tombstones)
            self._materialize_staging(staging, workspace)
            for path, entry in sorted(exports.items(), key=lambda item: item[0].count("/"), reverse=True):
                if entry["type"] == "directory":
                    safe_path(workspace.root, path).chmod(entry["mode"])
        for section in self._SYNC_SECTIONS:
            (workspace.root / section).mkdir(exist_ok=True)
        state = WorkspaceState(tombstones=tombstones)
        self.save_state(workspace, state)
        self._write_json(self._checkpoint_path(workspace), {
            "container": container_name, "host": self._host_entries(workspace),
            "container_entries": entries, "tombstones": list(tombstones),
        })
        return state

    async def _inspect_container_paths(self, docker_binary: str, container_name: str, paths: list[str]) -> dict:
        output = await self._helper(docker_binary, container_name, {"action": "inspect", "paths": paths})
        entries = json.loads(output)
        if set(entries) != set(paths):
            raise RuntimeError("容器增量清单不完整。")
        return entries

    async def _export_container_entries(self, docker_binary: str, container_name: str, entries: dict, archive: Path) -> None:
        with archive.open("wb") as output:
            await self._helper(docker_binary, container_name, {"action": "export", "entries": entries}, output)

    async def _helper(self, docker_binary: str, container_name: str, request: dict, output=None) -> bytes:
        script = Path(__file__).parents[1] / "sandbox" / "workspace_sync_helper.py"
        process = await asyncio.create_subprocess_exec(
            docker_binary, "exec", "-i", "--user", "sandbox", container_name,
            "python3", "-c", script.read_text(encoding="utf-8"),
            stdin=asyncio.subprocess.PIPE, stdout=output if output is not None else asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(json.dumps(request).encode("utf-8"))
        if process.returncode != 0:
            raise RuntimeError(self._decode_output(stderr).strip() or "容器文件级同步失败。")
        return stdout or b""

    def _extract_entries(self, archive: Path, staging: Path, expected: dict) -> None:
        seen = set()
        with tarfile.open(archive) as handle:
            for member in handle:
                path = self._normalize_relative_path(member.name)
                if path not in expected or path in seen:
                    raise RuntimeError(f"容器导出了未请求的路径: {member.name}")
                target = safe_path(staging, path)
                target.parent.mkdir(parents=True, exist_ok=True)
                if member.isdir():
                    target.mkdir(exist_ok=True)
                elif member.isfile():
                    with handle.extractfile(member) as source, target.open("wb") as destination:
                        shutil.copyfileobj(source, destination, length=1024 * 1024)
                elif member.issym():
                    resolved = (target.parent / member.linkname).resolve()
                    if not resolved.is_relative_to(staging.resolve()):
                        raise RuntimeError(f"符号链接超出工作区: {path}")
                    target.symlink_to(member.linkname)
                else:
                    raise RuntimeError(f"不支持的增量文件类型: {path}")
                if not target.is_symlink():
                    target.chmod(member.mode)
                if not self._same_entry(describe(target), expected[path]):
                    raise RuntimeError(f"增量文件校验失败: {path}")
                seen.add(path)
        if seen != set(expected):
            raise RuntimeError("增量归档不完整。")

    def _build_delta_archive(self, workspace: SandboxWorkspace, entries: dict | None = None) -> Path | None:
        entries = self._host_entries(workspace) if entries is None else entries
        if not entries:
            return None
        temporary = tempfile.NamedTemporaryFile(prefix="aethercore-delta-", suffix=".tar", delete=False)
        archive_path = Path(temporary.name)
        temporary.close()
        try:
            with tarfile.open(archive_path, mode="w") as handle:
                for relative in sorted(entries, key=lambda p: (p.count("/"), p)):
                    target = safe_path(workspace.root, relative)
                    if describe(target) != entries[relative]:
                        raise RuntimeError(f"导入期间宿主文件发生变化: {relative}")
                    handle.add(target, arcname=relative, recursive=False)
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

    def _materialize_staging(self, staging_root: Path, workspace: SandboxWorkspace) -> None:
        for directory, directories, files in os.walk(staging_root, followlinks=False):
            for name in directories + files:
                source = Path(directory) / name
                relative = source.relative_to(staging_root).as_posix()
                destination = safe_path(workspace.root, relative)
                is_directory = source.is_dir() and not source.is_symlink()
                if destination.is_symlink() or (destination.exists() and destination.is_dir() != is_directory):
                    if destination.is_dir() and not destination.is_symlink():
                        shutil.rmtree(destination)
                    else:
                        destination.unlink()
                if is_directory:
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
                    try:
                        shutil.copy2(source, temporary, follow_symlinks=False)
                        temporary.replace(destination)
                    finally:
                        temporary.unlink(missing_ok=True)

    def _apply_tombstones(self, workspace: SandboxWorkspace, tombstones: tuple[str, ...]) -> None:
        for relative_path in tombstones:
            normalized = self._normalize_relative_path(relative_path)
            if not normalized:
                continue
            root_name, _, remainder = normalized.partition("/")
            if root_name not in self._SYNC_SECTIONS:
                continue
            target_root = getattr(workspace, f"{root_name}_dir")
            target = safe_path(workspace.root, normalized)
            try:
                target.relative_to(target_root.resolve(strict=False))
            except ValueError:
                continue
            if target.is_symlink():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)

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
        parts = normalized.split("/")
        if any(part in {"", ".", ".."} for part in parts) or ":" in normalized or "\x00" in normalized:
            return ""
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


workspace_sync_service = WorkspaceSyncService()
