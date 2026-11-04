from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from app.core.config import settings
from app.sandbox.models import SandboxWorkspace


class SharedSkillService:
    """技能包按内容版本共享；Workspace 只持有已授权包的引用。"""

    _lock = threading.RLock()
    _MANIFEST = "shared_skills.json"
    _PREFIX = ".aether/skills"

    def root(self) -> Path:
        return settings.resolved_storage_root / "shared_skills"

    @contextmanager
    def _store_lock(self):
        with self._lock:
            self.root().mkdir(parents=True, exist_ok=True)
            with (self.root() / ".lock").open("a+b") as handle:
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def references(self, workspace: SandboxWorkspace) -> dict[str, dict]:
        path = workspace.metadata_dir / self._MANIFEST
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("packages"), dict):
            raise RuntimeError("共享技能引用清单无效，已拒绝回退复制。")
        for digest in data["packages"]:
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise RuntimeError("共享技能版本无效。")
        return data["packages"]

    def _inventory(self, source: Path) -> tuple[str, list[Path]]:
        if not source.is_dir():
            raise FileNotFoundError(f"共享资源目录不存在: {source}")
        if source.is_symlink() or (hasattr(source, "is_junction") and source.is_junction()):
            raise RuntimeError("技能资源不能包含符号链接")
        paths = sorted(source.rglob("*"))
        digest = hashlib.sha256()
        for path in paths:
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise RuntimeError("技能资源不能包含符号链接")
            if not path.is_dir() and not path.is_file():
                raise RuntimeError("技能包包含不支持的文件类型")
            digest.update(path.relative_to(source).as_posix().encode("utf-8") + b"\0")
            executable = bool(path.stat().st_mode & 0o111)
            digest.update(b"d" if path.is_dir() else (b"x" if executable else b"f"))
            if path.is_file():
                with path.open("rb") as handle:
                    digest.update(hashlib.file_digest(handle, "sha256").digest())
        return digest.hexdigest(), paths

    def reference(self, workspace: SandboxWorkspace, source: Path, *, name: str) -> tuple[Path, str]:
        # 发布与回收共用跨进程锁，防止已有包在新增引用时被 GC 删除。
        with self._store_lock():
            digest, paths = self._inventory(source)
            root = self.root()
            root.mkdir(parents=True, exist_ok=True)
            destination = root / digest
            if not destination.exists():
                staging = Path(tempfile.mkdtemp(prefix=".publish-", dir=root))
                try:
                    for path in paths:
                        target = staging / path.relative_to(source)
                        if path.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            # 不可变资源以硬链接复用 blob；修改说明不会再复制整个资源包。
                            with path.open("rb") as handle:
                                file_digest = hashlib.file_digest(handle, "sha256").hexdigest()
                            executable = bool(path.stat().st_mode & 0o111)
                            blobs = root / ".blobs"
                            blobs.mkdir(exist_ok=True)
                            blob = blobs / (file_digest + ("-x" if executable else "-f"))
                            if not blob.exists():
                                temporary_blob = blobs / (".publish-" + uuid.uuid4().hex)
                                try:
                                    shutil.copyfile(path, temporary_blob)
                                    with temporary_blob.open("rb") as handle:
                                        if hashlib.file_digest(handle, "sha256").hexdigest() != file_digest:
                                            raise RuntimeError("技能资源发布期间发生变化，请重试。")
                                    if os.name != "nt":
                                        temporary_blob.chmod(0o555 if executable else 0o444)
                                    try:
                                        os.link(temporary_blob, blob)
                                    except FileExistsError:
                                        pass
                                finally:
                                    temporary_blob.unlink(missing_ok=True)
                            # 不支持硬链接时显式报错，不静默退回包级复制。
                            os.link(blob, target)
                    if self._inventory(staging)[0] != digest:
                        raise RuntimeError("技能包发布期间发生变化，请重试。")
                    # 包是不可变版本；容器还会以 readonly 挂载，不能修改共享原件。
                    try:
                        staging.rename(destination)
                    except OSError:
                        if not destination.is_dir():
                            raise
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
            references = self.references(workspace)
            if digest not in references:
                references[digest] = {"name": name}
                path = workspace.metadata_dir / self._MANIFEST
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
                try:
                    temporary.write_text(json.dumps({"version": 1, "packages": references}, ensure_ascii=False), encoding="utf-8")
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            return destination, f"{settings.sandbox_docker_workspace_mount}/{self._PREFIX}/{digest}"

    def collect_garbage(self, *, grace_seconds: int = 24 * 3600) -> int:
        """仅回收没有历史 Workspace 引用、且超过宽限期的包及 blob。"""
        if not self.root().exists():
            return 0
        with self._store_lock():
            referenced = set()
            for path in (settings.sessions_root / "workspaces").glob(f"*/sandbox/metadata/{self._MANIFEST}"):
                # 清单损坏时停止回收，不能把无法读取的历史引用当成不存在。
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version") != 1 or not isinstance(data.get("packages"), dict):
                    raise RuntimeError(f"共享技能引用清单无效: {path}")
                referenced.update(data["packages"])
            cutoff = time.time() - grace_seconds
            removed = 0
            for package in self.root().iterdir():
                if (len(package.name) == 64 and all(c in "0123456789abcdef" for c in package.name)
                        and package.is_dir() and package.name not in referenced and package.stat().st_mtime < cutoff):
                    shutil.rmtree(package)
                    removed += 1
            blobs = self.root() / ".blobs"
            if blobs.exists():
                for blob in blobs.iterdir():
                    info = blob.stat()
                    if blob.is_file() and info.st_nlink == 1 and info.st_mtime < cutoff:
                        blob.unlink()
            return removed

    def mounts(self, workspace: SandboxWorkspace) -> list[str]:
        result = []
        for digest in sorted(self.references(workspace)):
            source = self.root() / digest
            if not source.is_dir():
                raise RuntimeError(f"共享技能版本 {digest} 不可用，已拒绝回退复制。")
            result.extend(["--mount", f"type=bind,src={source.resolve()},dst={settings.sandbox_docker_workspace_mount}/{self._PREFIX}/{digest},readonly"])
        return result

    def pin_baseline(self, workspace: SandboxWorkspace) -> Path | None:
        if workspace.baseline_root is None:
            return None
        if not workspace.baseline_root.is_dir():
            previous = self.baseline_root(workspace)
            if previous is not None and previous != workspace.baseline_root:
                return previous
        root, _ = self.reference(workspace, workspace.baseline_root, name="platform-baseline")
        from app.services.workspace_sync_service import workspace_sync_service
        workspace_sync_service._write_json(workspace.metadata_dir / "baseline_snapshot.json", {
            "source": str(workspace.baseline_root.resolve()), "digest": root.name,
        })
        return root

    def baseline_root(self, workspace: SandboxWorkspace) -> Path | None:
        path = workspace.metadata_dir / "baseline_snapshot.json"
        if workspace.baseline_root is None or not path.exists():
            return workspace.baseline_root
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("source") != str(workspace.baseline_root.resolve()):
            return workspace.baseline_root
        digest = record.get("digest")
        if digest not in self.references(workspace):
            raise RuntimeError("工作区基线版本缺少共享引用。")
        root = self.root() / digest
        if not root.is_dir():
            raise RuntimeError("工作区基线版本不可用，已拒绝使用其他版本代替。")
        return root

    def resolve(self, workspace: SandboxWorkspace, relative: str) -> Path | None:
        prefix = self._PREFIX + "/"
        if not relative.startswith(prefix):
            return None
        digest, _, remainder = relative[len(prefix):].partition("/")
        if digest not in self.references(workspace):
            raise FileNotFoundError("当前工作区未引用该共享技能版本。")
        root = (self.root() / digest).resolve()
        target = (root / remainder).resolve()
        if not target.is_relative_to(root):
            raise ValueError("目标路径超出共享技能范围。")
        return target


shared_skill_service = SharedSkillService()
