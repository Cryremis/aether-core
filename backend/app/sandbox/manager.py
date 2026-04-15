# backend/app/sandbox/manager.py
from pathlib import Path

from app.core.config import settings
from app.sandbox.models import SandboxWorkspace


class SandboxManager:
    """负责为每个会话准备独立工作区。"""

    def ensure_workspace(self, session_id: str) -> SandboxWorkspace:
        session_root = (settings.sessions_root / session_id / "sandbox").resolve()
        input_dir = session_root / "input"
        skills_dir = session_root / "skills"
        work_dir = session_root / "work"
        output_dir = session_root / "output"
        logs_dir = session_root / "logs"
        metadata_dir = session_root / "metadata"

        for directory in [
            input_dir,
            skills_dir,
            work_dir,
            output_dir,
            logs_dir,
            metadata_dir,
        ]:
            directory.mkdir(parents=True, exist_ok=True)

        return SandboxWorkspace(
            session_id=session_id,
            root=session_root,
            input_dir=input_dir,
            skills_dir=skills_dir,
            work_dir=work_dir,
            output_dir=output_dir,
            logs_dir=logs_dir,
            metadata_dir=metadata_dir,
        )

    def ensure_within_workspace(self, workspace: SandboxWorkspace, target: Path) -> Path:
        resolved = target.resolve()
        root = workspace.root.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError("目标路径超出沙箱范围。")
        return resolved


sandbox_manager = SandboxManager()
