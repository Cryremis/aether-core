from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from app.core.config import settings
from app.sandbox.manager import sandbox_manager
from app.services.file_service import file_service
from app.services.session_types import AgentSession
from app.services.shared_skill_service import SharedSkillService
from app.services.skill_loader import skill_loader
from app.services.skill_service import SkillService
from app.services.store import store_service
from app.services.workspace_runtime_service import WorkspaceRuntimeService


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
    monkeypatch.setattr(settings, "sandbox_executor", "docker")
    monkeypatch.setattr(store_service, "_db_path", tmp_path / "test.db")
    store_service.initialize()
    workspace = sandbox_manager.ensure_workspace("ws_shared", "sess_shared")
    source = tmp_path / "baseline" / "skills" / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: demo\ndescription: shared\n---\nRead ${AETHER_SKILL_DIR}/data.txt", encoding="utf-8")
    (source / "data.txt").write_text("version one", encoding="utf-8")
    return workspace, source


def test_shared_package_is_reused_across_workspaces(setup, monkeypatch):
    workspace, source = setup
    service = SharedSkillService()
    first, logical = service.reference(workspace, source, name="demo")
    second_workspace = sandbox_manager.ensure_workspace("ws_second", "sess_second")

    def fail(*args, **kwargs):
        raise AssertionError("existing shared version must not be copied again")

    monkeypatch.setattr(shutil, "copy2", fail)
    monkeypatch.setattr(shutil, "copyfile", fail)
    second, _ = service.reference(second_workspace, source, name="demo")
    assert first == second
    assert list(workspace.skills_dir.iterdir()) == []
    assert list(second_workspace.skills_dir.iterdir()) == []
    assert "readonly" in " ".join(service.mounts(workspace))
    assert service.resolve(workspace, logical.removeprefix("/workspace/") + "/data.txt").read_text() == "version one"


def test_versions_are_content_based_and_history_remains_readable(setup):
    workspace, source = setup
    service = SharedSkillService()
    first, _ = service.reference(workspace, source, name="demo")
    (source / "data.txt").write_text("version two")
    second, _ = service.reference(workspace, source, name="demo")
    assert first != second
    assert (first / "data.txt").read_text() == "version one"
    assert (second / "data.txt").read_text() == "version two"
    assert len(service.references(workspace)) == 2


def test_invoking_platform_skill_uses_shared_version_and_api_path(setup, monkeypatch):
    workspace, source = setup
    session = AgentSession(session_id="sess_shared", workspace_id=workspace.workspace_id, workspace=workspace)
    service = SkillService()
    definition = skill_loader.load_file(source / "SKILL.md", source="platform")
    monkeypatch.setattr(service, "resolve_skill", lambda *args: definition)
    result = service.invoke_skill(session, "demo")
    assert "/workspace/.aether/skills/" in result["injected_messages"][0]["content"]
    shared = SharedSkillService()
    digest = next(iter(shared.references(workspace)))
    path = f"/workspace/.aether/skills/{digest}"
    assert file_service.read_text(session, relative_path=f"{path}/data.txt") == "version one"
    assert {entry.name for entry in file_service.list(session, path=path)} == {"SKILL.md", "data.txt"}
    assert not (workspace.skills_dir / ".available").exists()


def test_only_referenced_packages_are_mounted_and_resolved(setup):
    workspace, source = setup
    service = SharedSkillService()
    _, logical = service.reference(workspace, source, name="demo")
    other = sandbox_manager.ensure_workspace("ws_other", "sess_other")
    with pytest.raises(FileNotFoundError):
        service.resolve(other, logical.removeprefix("/workspace/"))
    assert service.mounts(other) == []
    with pytest.raises(ValueError):
        service.resolve(workspace, logical.removeprefix("/workspace/") + "/../../private")


def test_reference_change_requires_new_runtime_mounts(setup):
    workspace, source = setup
    runtime = WorkspaceRuntimeService()
    before = runtime._build_runtime_spec("image", workspace)
    SharedSkillService().reference(workspace, source, name="demo")
    after = runtime._build_runtime_spec("image", workspace)
    assert before["shared_skills"] == []
    assert len(after["shared_skills"]) == 1
    args = runtime._build_run_args(workspace, "container", "image")
    mounts = [args[index + 1] for index, arg in enumerate(args[:-1]) if arg == "--mount"]
    assert any("/.aether/skills/" in mount and mount.endswith(",readonly") for mount in mounts)


def test_unchanged_resources_share_blobs_across_versions(setup):
    workspace, source = setup
    service = SharedSkillService()
    first, _ = service.reference(workspace, source, name="demo")
    (source / "SKILL.md").write_text("new instructions")
    second, _ = service.reference(workspace, source, name="demo")
    assert first != second
    assert (first / "data.txt").samefile(second / "data.txt")
    assert (first / "SKILL.md").read_text() != (second / "SKILL.md").read_text()


def test_gc_keeps_history_references_and_removes_unreferenced_packages(setup):
    workspace, source = setup
    service = SharedSkillService()
    first, _ = service.reference(workspace, source, name="demo")
    assert service.collect_garbage(grace_seconds=0) == 0
    assert first.exists()
    (workspace.metadata_dir / "shared_skills.json").unlink()
    assert service.collect_garbage(grace_seconds=0) == 1
    assert not first.exists()
    assert list((service.root() / ".blobs").iterdir()) == []


def test_baseline_snapshot_pins_api_view_and_reuses_resources(setup):
    workspace, source = setup
    baseline = source.parent.parent
    (baseline / "work").mkdir()
    (baseline / "work/guide.txt").write_text("old baseline")
    workspace = replace(workspace, baseline_root=baseline)
    service = SharedSkillService()
    first = service.pin_baseline(workspace)
    (baseline / "work/guide.txt").write_text("new baseline")
    assert sandbox_manager.resolve_logical_path(workspace, "work/guide.txt").read_text() == "old baseline"
    second = service.pin_baseline(workspace)
    assert sandbox_manager.resolve_logical_path(workspace, "work/guide.txt").read_text() == "new baseline"
    assert (first / "skills/demo/data.txt").samefile(second / "skills/demo/data.txt")
    assert (first / "work/guide.txt").read_text() == "old baseline"


def test_missing_shared_package_does_not_fall_back_to_copy(setup):
    workspace, source = setup
    service = SharedSkillService()
    package, _ = service.reference(workspace, source, name="demo")
    shutil.rmtree(package)
    with pytest.raises(RuntimeError, match="拒绝回退复制"):
        service.mounts(workspace)
    assert list(workspace.skills_dir.iterdir()) == []
