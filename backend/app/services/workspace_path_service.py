from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.sandbox.manager import sandbox_manager
from app.services.session_types import AgentSession


@dataclass(frozen=True)
class ResolvedWorkspacePath:
    logical_path: str
    host_path: Path
    exists: bool
    is_dir: bool
    is_file: bool


class WorkspacePathService:
    """统一处理沙箱逻辑路径与宿主机真实路径的映射。"""

    _SPECIAL_DIRS = {
        "input": settings.sandbox_docker_input_dir,
        "output": settings.sandbox_docker_output_dir,
        "skills": settings.sandbox_docker_skills_dir,
        "logs": settings.sandbox_docker_logs_dir,
        "work": settings.sandbox_docker_work_dir,
        "home": settings.sandbox_docker_home_dir,
        "cache": settings.sandbox_docker_cache_dir,
    }

    def workspace_root(self) -> str:
        return settings.sandbox_docker_workspace_mount

    def normalize_logical_path(self, path: str | None) -> str:
        root = self.workspace_root()
        if path is None or not str(path).strip():
            return root

        normalized = str(path).replace("\\", "/").strip()
        if not normalized:
            return root

        if normalized.startswith(root):
            logical = posixpath.normpath(normalized)
            return logical if logical.startswith("/") else f"/{logical}"

        if normalized.startswith("/"):
            logical = posixpath.normpath(normalized)
            return logical if logical.startswith(root) else logical

        normalized = posixpath.normpath(normalized)
        first_part = normalized.split("/")[0]
        if first_part in self._SPECIAL_DIRS:
            base = self._SPECIAL_DIRS[first_part]
            remainder = normalized[len(first_part):].strip("/")
            return posixpath.join(base, remainder) if remainder else base

        return posixpath.join(root, normalized)

    def logical_to_relative_path(self, logical_path: str) -> str:
        normalized = self.normalize_logical_path(logical_path)
        root = self.workspace_root()
        if normalized == root:
            return ""
        if normalized.startswith(root + "/"):
            return normalized[len(root) + 1 :]
        return normalized.lstrip("/")

    def relative_to_logical_path(self, relative_path: str) -> str:
        relative = relative_path.replace("\\", "/").strip("/")
        if not relative:
            return self.workspace_root()
        return posixpath.join(self.workspace_root(), relative)

    def resolve_path(
        self,
        session: AgentSession,
        path: str | None,
    ) -> ResolvedWorkspacePath:
        if session.workspace is None:
            raise RuntimeError("会话沙箱尚未初始化。")

        logical_path = self.normalize_logical_path(path)
        relative_path = self.logical_to_relative_path(logical_path)
        host_path = sandbox_manager.resolve_logical_path(session.workspace, relative_path)
        exists = host_path.exists()
        return ResolvedWorkspacePath(
            logical_path=logical_path,
            host_path=host_path,
            exists=exists,
            is_dir=host_path.is_dir() if exists else False,
            is_file=host_path.is_file() if exists else False,
        )

    def path_display(self, path: str | None) -> str:
        return self.normalize_logical_path(path)


workspace_path_service = WorkspacePathService()
