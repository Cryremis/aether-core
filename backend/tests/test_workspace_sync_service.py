from __future__ import annotations

import asyncio
import json
import tarfile
from pathlib import Path

import pytest

from app.sandbox.manager import sandbox_manager
from app.sandbox.workspace_sync_helper import describe, safe_path
from app.core.config import settings
from app.services.workspace_sync_service import WorkspaceState, WorkspaceSyncService


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
    return sandbox_manager.ensure_workspace("ws_sync", "sess_sync")


def fake_container(service, monkeypatch, root, diff, exported):
    async def list_diff(**kwargs):
        return diff

    async def inspect(_docker, _container, paths):
        return {path: describe(root / path) for path in paths}

    async def export(_docker, _container, entries, archive):
        exported.append(set(entries))
        with tarfile.open(archive, "w") as handle:
            for path in entries:
                handle.add(root / path, arcname=path, recursive=False)

    monkeypatch.setattr(service, "_list_container_diff", list_diff)
    monkeypatch.setattr(service, "_inspect_container_paths", inspect)
    monkeypatch.setattr(service, "_export_container_entries", export)


def capture(service, workspace):
    return asyncio.run(service.capture_container_delta(docker_binary="docker", container_name="container", workspace=workspace))


def test_changed_parent_never_exports_unmodified_baseline(tmp_path, workspace, monkeypatch):
    root = tmp_path / "container"
    (root / "skills" / "baseline").mkdir(parents=True)
    (root / "skills" / "baseline" / "large.bin").write_bytes(b"unchanged baseline")
    (root / "skills" / ".available" / "called").mkdir(parents=True)
    (root / "skills" / ".available" / "called" / "SKILL.md").write_text("called")
    diff = [("C", "skills"), ("A", "skills/.available"), ("A", "skills/.available/called"),
            ("A", "skills/.available/called/SKILL.md")]
    service, exports = WorkspaceSyncService(), []
    fake_container(service, monkeypatch, root, diff, exports)
    capture(service, workspace)
    assert not (workspace.skills_dir / "baseline").exists()
    assert (workspace.skills_dir / ".available/called/SKILL.md").read_text() == "called"
    assert "skills" not in exports[0]
    # docker diff 是累计差异；重复执行不应重复导出。
    capture(service, workspace)
    assert len(exports) == 1


def test_modified_file_and_empty_directory_survive_restore_archive(tmp_path, workspace, monkeypatch):
    root = tmp_path / "container"
    (root / "work" / "empty").mkdir(parents=True)
    (root / "work" / "report.txt").write_text("changed")
    (workspace.work_dir / "uploaded.txt").write_text("keep")
    service, exports = WorkspaceSyncService(), []
    fake_container(service, monkeypatch, root, [("C", "work"), ("A", "work/empty"), ("C", "work/report.txt")], exports)
    capture(service, workspace)
    archive = service._build_delta_archive(workspace)
    try:
        with tarfile.open(archive) as handle:
            assert set(handle.getnames()) == {"work/empty", "work/report.txt", "work/uploaded.txt"}
        assert (workspace.work_dir / "empty").is_dir()
        assert (workspace.work_dir / "uploaded.txt").read_text() == "keep"
    finally:
        archive.unlink()


def test_export_failure_does_not_apply_deletions(tmp_path, workspace, monkeypatch):
    service = WorkspaceSyncService()
    (workspace.work_dir / "keep.txt").write_text("keep")
    service._write_json(service._checkpoint_path(workspace), {"container": "container", "host": service._host_entries(workspace)})
    root = tmp_path / "container"
    (root / "work").mkdir(parents=True)
    (root / "work" / "new.txt").write_text("new")
    fake_container(service, monkeypatch, root, [("D", "work/keep.txt"), ("A", "work/new.txt")], [])

    async def fail(*args):
        raise RuntimeError("disk full")

    monkeypatch.setattr(service, "_export_container_entries", fail)
    with pytest.raises(RuntimeError, match="disk full"):
        capture(service, workspace)
    assert (workspace.work_dir / "keep.txt").read_text() == "keep"
    assert service.load_state(workspace).tombstones == ()


def test_deleted_baseline_and_recreated_file_update_tombstones(tmp_path, workspace, monkeypatch):
    service = WorkspaceSyncService()
    root = tmp_path / "container"
    (root / "work").mkdir(parents=True)
    diff = [("D", "work/base.txt")]
    fake_container(service, monkeypatch, root, diff, [])
    assert capture(service, workspace).tombstones == ("work/base.txt",)
    (root / "work/base.txt").write_text("recreated")
    diff[:] = [("C", "work/base.txt")]
    assert capture(service, workspace).tombstones == ()
    assert (workspace.work_dir / "base.txt").read_text() == "recreated"


def test_concurrent_host_edit_is_not_overwritten(tmp_path, workspace, monkeypatch):
    service = WorkspaceSyncService()
    target = workspace.work_dir / "file.txt"
    target.write_text("old")
    service._write_json(service._checkpoint_path(workspace), {"container": "container", "host": service._host_entries(workspace)})
    target.write_text("new upload")
    root = tmp_path / "container"
    (root / "work").mkdir(parents=True)
    (root / "work/file.txt").write_text("shell edit")
    fake_container(service, monkeypatch, root, [("C", "work/file.txt")], [])
    with pytest.raises(RuntimeError, match="并发修改"):
        capture(service, workspace)
    assert target.read_text() == "new upload"


def test_hydrate_unchanged_workspace_is_noop(workspace, monkeypatch):
    service = WorkspaceSyncService()
    (workspace.work_dir / "file.txt").write_text("same")
    service._write_json(service._checkpoint_path(workspace), {"container": "container", "host": service._host_entries(workspace)})

    async def fail(**kwargs):
        raise AssertionError("unchanged workspace must not invoke docker")

    monkeypatch.setattr(service, "_ensure_container_dirs", fail)
    asyncio.run(service.hydrate_container(docker_binary="docker", container_name="container", workspace=workspace))
    assert service._checkpoint(workspace, "new-container") == {}


def test_sync_rejects_traversal_and_corrupt_state(workspace):
    service = WorkspaceSyncService()
    assert service._normalize_container_path("/workspace/work/../../etc/passwd") == ""
    with pytest.raises(ValueError):
        safe_path(workspace.root, "work/../../elsewhere")
    service.state_path(workspace).write_text("broken json")
    with pytest.raises(RuntimeError, match="删除记录"):
        service.load_state(workspace)


def test_file_directory_replacements(tmp_path, workspace, monkeypatch):
    service = WorkspaceSyncService()
    (workspace.work_dir / "to_directory").write_text("old")
    (workspace.work_dir / "to_file").mkdir()
    (workspace.work_dir / "to_file/old").write_text("old")
    service._write_json(service._checkpoint_path(workspace), {"container": "container", "host": service._host_entries(workspace)})
    root = tmp_path / "container"
    (root / "work/to_directory").mkdir(parents=True)
    (root / "work/to_directory/new").write_text("new")
    (root / "work/to_file").write_text("file")
    diff = [("C", "work/to_directory"), ("A", "work/to_directory/new"), ("C", "work/to_file"), ("D", "work/to_file/old")]
    fake_container(service, monkeypatch, root, diff, [])
    capture(service, workspace)
    assert (workspace.work_dir / "to_directory/new").read_text() == "new"
    assert (workspace.work_dir / "to_file").read_text() == "file"


def test_pending_sync_survives_failure_blocks_collection_and_can_retry(workspace, monkeypatch):
    from types import SimpleNamespace
    from app.services.store import store_service
    from app.services.workspace_runtime_service import RuntimeBusyError, WorkspaceRuntimeService
    from app.services.workspace_sync_service import workspace_sync_service

    service = WorkspaceRuntimeService()
    runtime = {"status": "running", "container_name": "container", "generation": 1,
               "workspace_root": str(workspace.root), "idle_expires_at": "later"}
    pending = workspace.metadata_dir / "workspace_sync_pending.json"
    failed = workspace.metadata_dir / "workspace_sync_failed.json"

    async def persist(**kwargs):
        return runtime

    async def create(*args):
        assert pending.exists(), "must record pending data before starting a command"
        return SimpleNamespace(returncode=0)

    async def output(*args, **kwargs):
        return b"ok", b""

    async def fail(**kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(service, "_persist_runtime", persist)
    monkeypatch.setattr(service, "_create_exec_process", create)
    monkeypatch.setattr(service, "_collect_stream_output", output)
    monkeypatch.setattr(service, "_require_docker_binary", lambda: "docker")
    monkeypatch.setattr(workspace_sync_service, "capture_container_delta", fail)
    with pytest.raises(RuntimeError, match="disk full"):
        asyncio.run(service._run_shell_command(workspace, runtime=runtime, runtime_notice=None,
                    command="echo ok", shell="bash", timeout_seconds=None, allow_network_recovery=False,
                    session=None, run_id=None, output_callback=None))
    assert pending.exists() and failed.exists()
    monkeypatch.setattr(store_service, "get_workspace_runtime", lambda _: runtime)
    with pytest.raises(RuntimeBusyError, match="同步尚未完成"):
        asyncio.run(service._collect_locked(workspace.workspace_id, reason="idle_ttl_expired"))

    async def success(**kwargs):
        return WorkspaceState()

    async def refresh(_):
        return runtime

    monkeypatch.setattr(workspace_sync_service, "capture_container_delta", success)
    monkeypatch.setattr(service, "refresh_runtime", refresh)
    monkeypatch.setattr(service, "_detect_runtime_recreate_reason", lambda *args: None)
    asyncio.run(service._ensure_runtime_locked(workspace))
    assert not pending.exists() and not failed.exists()
