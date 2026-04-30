# backend/app/sandbox/manager.py
import os
from pathlib import Path

from app.core.config import settings
from app.sandbox.models import SandboxWorkspace


class SandboxManager:
    """负责为每个会话准备独立工作区。"""

    _SESSION_ROOTS = ("input", "skills", "work", "output", "logs")

    def ensure_workspace(self, session_id: str, baseline_root: Path | None = None) -> SandboxWorkspace:
        session_root = (settings.sessions_root / session_id / "sandbox").resolve()
        input_dir = session_root / "input"
        skills_dir = session_root / "skills"
        work_dir = session_root / "work"
        output_dir = session_root / "output"
        logs_dir = session_root / "logs"
        home_dir = session_root / "home"
        cache_dir = session_root / "cache"
        overlay_work_dir = session_root / ".overlay-work"
        metadata_dir = session_root / "metadata"

        for directory in [
            input_dir,
            skills_dir,
            work_dir,
            output_dir,
            logs_dir,
            home_dir,
            cache_dir,
            overlay_work_dir,
            metadata_dir,
        ]:
            directory.mkdir(parents=True, exist_ok=True)

        for name in self._SESSION_ROOTS:
            (overlay_work_dir / name).mkdir(parents=True, exist_ok=True)

        return SandboxWorkspace(
            session_id=session_id,
            root=session_root,
            baseline_root=baseline_root.resolve() if baseline_root else None,
            input_dir=input_dir,
            skills_dir=skills_dir,
            work_dir=work_dir,
            output_dir=output_dir,
            logs_dir=logs_dir,
            home_dir=home_dir,
            cache_dir=cache_dir,
            overlay_work_dir=overlay_work_dir,
            metadata_dir=metadata_dir,
        )

    def ensure_within_workspace(self, workspace: SandboxWorkspace, target: Path) -> Path:
        resolved = target.resolve(strict=False)
        root = workspace.root.resolve(strict=False)
        common = Path(os.path.commonpath([str(root), str(resolved)]))
        if common != root:
            raise ValueError("目标路径超出沙箱范围。")
        return resolved

    def resolve_logical_path(self, workspace: SandboxWorkspace, relative_path: str) -> Path:
        normalized = relative_path.replace("\\", "/").strip("/")
        if not normalized:
            return workspace.root

        upper_candidate = self.ensure_within_workspace(workspace, workspace.root / normalized)
        if upper_candidate.exists():
            return upper_candidate

        if workspace.baseline_root is not None:
            baseline_candidate = (workspace.baseline_root / normalized).resolve(strict=False)
            baseline_root = workspace.baseline_root.resolve(strict=False)
            common = Path(os.path.commonpath([str(baseline_root), str(baseline_candidate)]))
            if common == baseline_root and baseline_candidate.exists():
                return baseline_candidate
        return upper_candidate


sandbox_manager = SandboxManager()
