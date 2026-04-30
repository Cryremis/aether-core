from pathlib import Path

from app.services.session_types import AgentSession
from app.services.workspace_path_service import workspace_path_service
from app.sandbox.models import SandboxWorkspace


def build_workspace(root: Path) -> SandboxWorkspace:
    for name in ["input", "skills", "work", "output", "logs", "home", "cache", "metadata", ".overlay-work"]:
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in ["input", "skills", "work", "output", "logs"]:
        (root / ".overlay-work" / name).mkdir(parents=True, exist_ok=True)
    return SandboxWorkspace(
        session_id="sess_paths",
        root=root,
        baseline_root=None,
        input_dir=root / "input",
        skills_dir=root / "skills",
        work_dir=root / "work",
        output_dir=root / "output",
        logs_dir=root / "logs",
        home_dir=root / "home",
        cache_dir=root / "cache",
        overlay_work_dir=root / ".overlay-work",
        metadata_dir=root / "metadata",
    )


def test_workspace_path_service_normalizes_relative_and_absolute_paths(tmp_path):
    workspace = build_workspace(tmp_path / "sandbox")
    (workspace.input_dir / "demo.txt").write_text("hello", encoding="utf-8")
    session = AgentSession(session_id="sess_paths", workspace=workspace)

    resolved_relative = workspace_path_service.resolve_path(session, "input/demo.txt")
    resolved_absolute = workspace_path_service.resolve_path(session, "/workspace/input/demo.txt")
    resolved_empty = workspace_path_service.resolve_path(session, None)

    assert resolved_relative.logical_path == "/workspace/input/demo.txt"
    assert resolved_absolute.logical_path == "/workspace/input/demo.txt"
    assert resolved_relative.host_path == resolved_absolute.host_path
    assert resolved_empty.logical_path == "/workspace"
    assert resolved_empty.host_path == workspace.root
