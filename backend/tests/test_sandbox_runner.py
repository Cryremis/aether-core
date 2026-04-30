import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.config import settings
from app.sandbox.runner import SandboxRunner
from app.sandbox.models import SandboxWorkspace
from app.services.session_runtime_service import session_runtime_service
from app.services.store import store_service


def build_workspace(root: Path) -> SandboxWorkspace:
    for name in ["input", "skills", "work", "output", "logs", "home", "cache", "metadata", ".overlay-work"]:
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in ["input", "skills", "work", "output", "logs"]:
        (root / ".overlay-work" / name).mkdir(parents=True, exist_ok=True)
    return SandboxWorkspace(
        session_id="sess_demo",
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


def initialize_store(tmp_path: Path) -> None:
    settings.storage_root = tmp_path / "storage"
    store_service._db_path = settings.storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    store_service.initialize()


def test_session_runtime_builds_persistent_container_args(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_allow_network", True)
    monkeypatch.setattr(settings, "sandbox_docker_network_mode", "bridge")
    monkeypatch.setattr(settings, "sandbox_docker_dns_servers", ["8.8.8.8", "1.1.1.1"])
    monkeypatch.setattr(settings, "sandbox_docker_read_only_rootfs", False)
    monkeypatch.setattr(settings, "sandbox_docker_user", "sandbox")
    workspace = build_workspace(tmp_path / "sandbox")
    args = session_runtime_service._build_run_args(workspace, "test-container")
    joined = " ".join(args)
    assert "--network bridge" in joined
    assert "--network none" not in joined
    assert "--read-only" not in joined
    assert "--cap-drop ALL" in joined
    assert "--dns 8.8.8.8" in joined
    assert "PIP_CACHE_DIR=/workspace/cache/pip" in joined
    assert "/workspace/home/.local/bin" in joined
    assert f"dst={settings.sandbox_docker_workspace_mount}" in joined
    assert settings.sandbox_docker_home_dir in joined
    assert "chown -R 10001:10001 /workspace" in joined
    assert "chmod -R u+rwX,g+rwX,o+rwX /workspace" in joined
    assert settings.sandbox_docker_image in args


def test_session_runtime_exec_uses_work_dir_and_sandbox_user(tmp_path):
    workspace = build_workspace(tmp_path / "sandbox")
    args = session_runtime_service._build_exec_args("test-container", "bash", "pwd")
    assert args[:6] == ["exec", "--user", "sandbox", "--workdir", "/workspace/work", "--env"]
    assert "HOME=/workspace/home" in args
    assert "PYTHONUSERBASE=/workspace/home/.local" in args
    assert "test-container" in args
    assert args[-3:] == ["/bin/bash", "-lc", "pwd"]


def test_runtime_spec_drift_requests_recreate(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_allow_network", True)
    monkeypatch.setattr(settings, "sandbox_docker_network_mode", "bridge")
    monkeypatch.setattr(settings, "sandbox_docker_dns_servers", [])
    monkeypatch.setattr(settings, "sandbox_docker_read_only_rootfs", False)
    monkeypatch.setattr(settings, "sandbox_docker_user", "sandbox")
    now = datetime.now(timezone.utc)

    runtime = {
        "status": "running",
        "metadata": {},
    }
    assert session_runtime_service._detect_runtime_recreate_reason(runtime, now) == "runtime_spec_missing"

    runtime["metadata"] = {
        "runtime_spec": session_runtime_service._build_runtime_spec(),
    }
    assert session_runtime_service._detect_runtime_recreate_reason(runtime, now) is None

    runtime["metadata"]["runtime_spec"]["network_mode"] = "none"
    assert session_runtime_service._detect_runtime_recreate_reason(runtime, now) == "runtime_config_changed"


def test_runner_fails_closed_when_executor_unavailable(tmp_path, monkeypatch):
    runner = SandboxRunner()

    class FakeExecutor:
        async def check_availability(self):
            return False, "docker-daemon-down"

        async def run_shell(self, workspace, command, shell):
            raise AssertionError("unavailable executor should not be called")

    monkeypatch.setitem(runner._executors, "docker", FakeExecutor())
    monkeypatch.setattr(settings, "sandbox_executor", "docker")
    monkeypatch.setattr(settings, "sandbox_fail_closed", True)

    workspace = build_workspace(tmp_path / "sandbox")

    async def execute():
        with pytest.raises(RuntimeError, match="fail-closed"):
            await runner.run_shell(workspace, "echo hello", "bash")

    asyncio.run(execute())


def test_collect_expired_runtime_marks_record(tmp_path, monkeypatch):
    initialize_store(tmp_path)
    workspace = build_workspace(tmp_path / "sandbox")
    now = datetime.now(timezone.utc)
    store_service.upsert_session_runtime(
        session_id=workspace.session_id,
        conversation_id=None,
        platform_id=None,
        owner_user_id=None,
        external_user_id=None,
        container_name="test-container",
        container_id="container-id",
        image=settings.sandbox_docker_image,
        status="running",
        generation=1,
        network_mode="none",
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
        last_started_at=now.isoformat(),
        last_used_at=(now - timedelta(days=2)).isoformat(),
        idle_expires_at=(now - timedelta(minutes=1)).isoformat(),
        max_expires_at=(now + timedelta(days=1)).isoformat(),
        destroyed_at=None,
        destroy_reason=None,
        restart_count=0,
        workspace_root=str(workspace.root),
        home_root=str(workspace.home_dir),
        metadata={},
    )

    async def fake_collect(session_id: str, *, reason: str):
        record = store_service.get_session_runtime(session_id)
        assert record is not None
        return store_service.upsert_session_runtime(
            session_id=session_id,
            conversation_id=record.get("conversation_id"),
            platform_id=record.get("platform_id"),
            owner_user_id=record.get("owner_user_id"),
            external_user_id=record.get("external_user_id"),
            container_name=record.get("container_name"),
            container_id=record.get("container_id"),
            image=str(record.get("image")),
            status="expired",
            generation=int(record.get("generation") or 0),
            network_mode=str(record.get("network_mode") or "none"),
            created_at=str(record.get("created_at")),
            updated_at=now.isoformat(),
            last_started_at=record.get("last_started_at"),
            last_used_at=record.get("last_used_at"),
            idle_expires_at=record.get("idle_expires_at"),
            max_expires_at=record.get("max_expires_at"),
            destroyed_at=now.isoformat(),
            destroy_reason=reason,
            restart_count=int(record.get("restart_count") or 0),
            workspace_root=str(record.get("workspace_root") or ""),
            home_root=str(record.get("home_root") or ""),
            metadata=record.get("metadata") or {},
        )

    monkeypatch.setattr(session_runtime_service, "collect_runtime", fake_collect)

    asyncio.run(session_runtime_service.collect_expired_runtimes())

    runtime = store_service.get_session_runtime(workspace.session_id)
    assert runtime is not None
    assert runtime["status"] == "expired"
    assert runtime["destroy_reason"] == "idle_ttl_expired"
