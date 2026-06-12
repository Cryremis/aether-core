import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.config import settings
from app.sandbox.runner import SandboxRunner
from app.sandbox.models import SandboxCommandResult, SandboxWorkspace
from app.services.session_runtime_service import RuntimeBusyError, RuntimeStartError, session_runtime_service
from app.services.session_workspace_sync_service import session_workspace_sync_service
from app.services.store import store_service


def build_workspace(root: Path) -> SandboxWorkspace:
    for name in ["skills", "work", "logs", "home", "cache", "metadata", ".overlay-work"]:
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in ["skills", "work", "logs"]:
        (root / ".overlay-work" / name).mkdir(parents=True, exist_ok=True)
    return SandboxWorkspace(
        session_id="sess_demo",
        root=root,
        baseline_root=None,
        skills_dir=root / "skills",
        work_dir=root / "work",
        logs_dir=root / "logs",
        home_dir=root / "home",
        cache_dir=root / "cache",
        overlay_work_dir=root / ".overlay-work",
        metadata_dir=root / "metadata",
    )


def build_workspace_with_baseline(root: Path, baseline_root: Path) -> SandboxWorkspace:
    workspace = build_workspace(root)
    return SandboxWorkspace(
        session_id=workspace.session_id,
        root=workspace.root,
        baseline_root=baseline_root,
        skills_dir=workspace.skills_dir,
        work_dir=workspace.work_dir,
        logs_dir=workspace.logs_dir,
        home_dir=workspace.home_dir,
        cache_dir=workspace.cache_dir,
        overlay_work_dir=workspace.overlay_work_dir,
        metadata_dir=workspace.metadata_dir,
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
    args = session_runtime_service._build_run_args(workspace, "test-container", settings.sandbox_docker_image)
    joined = " ".join(args)
    assert "--network bridge" in joined
    assert "--network none" not in joined
    assert "--read-only" not in joined
    assert "--cap-drop ALL" in joined
    assert "--dns 8.8.8.8" in joined
    assert "PIP_CACHE_DIR=/workspace/cache/pip" in joined
    assert "/workspace/home/.local/bin" in joined
    assert "dst=/aether/session-host" in joined
    assert settings.sandbox_docker_home_dir in joined
    assert "while true; do sleep 3600; done" in joined
    assert settings.sandbox_docker_image in args


def test_session_runtime_exec_uses_work_dir_and_sandbox_user(tmp_path):
    workspace = build_workspace(tmp_path / "sandbox")
    args = session_runtime_service._build_exec_args("test-container", "bash", "pwd")
    assert args[:6] == ["exec", "--user", "sandbox", "--workdir", "/workspace/work", "--env"]
    assert "HOME=/workspace/home" in args
    assert "PYTHONUSERBASE=/workspace/home/.local" in args
    assert "test-container" in args
    assert args[-3:] == ["/bin/bash", "-lc", "pwd"]


def test_session_runtime_builds_baseline_image_args_for_shared_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_allow_network", True)
    monkeypatch.setattr(settings, "sandbox_docker_network_mode", "bridge")
    monkeypatch.setattr(settings, "sandbox_docker_dns_servers", [])
    monkeypatch.setattr(settings, "sandbox_docker_read_only_rootfs", False)
    monkeypatch.setattr(settings, "sandbox_docker_user", "sandbox")

    baseline_root = tmp_path / "baseline"
    for name in ["skills", "work", "logs"]:
        (baseline_root / name).mkdir(parents=True, exist_ok=True)
    workspace = build_workspace_with_baseline(tmp_path / "sandbox", baseline_root)

    args = session_runtime_service._build_run_args(workspace, "test-container", settings.sandbox_docker_image)
    joined = " ".join(args)

    assert "--cap-add SYS_ADMIN" not in joined
    assert "dst=/aether/session-host" in joined
    assert "mount -t overlay overlay" not in joined
    assert f"--tmpfs {settings.sandbox_docker_workspace_mount}:size=64m" not in joined


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
        "runtime_spec": session_runtime_service._build_runtime_spec(settings.sandbox_docker_image),
    }
    assert session_runtime_service._detect_runtime_recreate_reason(runtime, now) is None

    runtime["metadata"]["runtime_spec"]["network_mode"] = "none"
    assert session_runtime_service._detect_runtime_recreate_reason(runtime, now) == "runtime_config_changed"


def test_runtime_spec_drift_detects_platform_image_change(tmp_path, monkeypatch):
    initialize_store(tmp_path)
    monkeypatch.setattr(settings, "sandbox_allow_network", True)
    monkeypatch.setattr(settings, "sandbox_docker_network_mode", "bridge")
    monkeypatch.setattr(settings, "sandbox_docker_dns_servers", [])
    monkeypatch.setattr(settings, "sandbox_docker_read_only_rootfs", False)
    monkeypatch.setattr(settings, "sandbox_docker_user", "sandbox")

    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None
    platform = store_service.create_platform(
        platform_key="runtime-image-platform",
        display_name="Runtime Image Platform",
        host_type="embedded",
        description="runtime image test",
        owner_user_id=admin.user_id,
    )
    store_service.update_platform_sandbox_image(
        platform_id=int(platform["platform_id"]),
        image="registry.example.com/custom/platform:v2",
    )

    now = datetime.now(timezone.utc)
    runtime = {
        "status": "running",
        "platform_id": platform["platform_id"],
        "image": settings.sandbox_docker_image,
        "metadata": {
            "runtime_spec": session_runtime_service._build_runtime_spec(settings.sandbox_docker_image),
        },
    }
    assert session_runtime_service._detect_runtime_recreate_reason(runtime, now) == "runtime_config_changed"


def test_runtime_spec_drift_detects_baseline_visibility_change(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_allow_network", True)
    monkeypatch.setattr(settings, "sandbox_docker_network_mode", "bridge")
    monkeypatch.setattr(settings, "sandbox_docker_dns_servers", [])
    monkeypatch.setattr(settings, "sandbox_docker_read_only_rootfs", False)
    monkeypatch.setattr(settings, "sandbox_docker_user", "sandbox")

    workspace = build_workspace(tmp_path / "sandbox")
    baseline_root = tmp_path / "baseline"
    for name in ["skills", "work", "logs"]:
        (baseline_root / name).mkdir(parents=True, exist_ok=True)
    baseline_workspace = build_workspace_with_baseline(tmp_path / "sandbox-baseline", baseline_root)

    now = datetime.now(timezone.utc)
    runtime = {
        "status": "running",
        "metadata": {
            "runtime_spec": session_runtime_service._build_runtime_spec(settings.sandbox_docker_image, workspace),
        },
    }

    assert session_runtime_service._detect_runtime_recreate_reason(runtime, now, baseline_workspace) == "runtime_config_changed"


def test_runtime_spec_records_baseline_image_mode(tmp_path):
    baseline_root = tmp_path / "baseline"
    for name in ["skills", "work", "logs"]:
        (baseline_root / name).mkdir(parents=True, exist_ok=True)
    workspace = build_workspace_with_baseline(tmp_path / "sandbox", baseline_root)

    runtime_spec = session_runtime_service._build_runtime_spec(settings.sandbox_docker_image, workspace)

    assert runtime_spec["baseline_mode"] == "image"


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


def test_runner_passes_session_context_to_executor(tmp_path, monkeypatch):
    runner = SandboxRunner()
    observed: dict[str, object] = {}

    class FakeExecutor:
        async def check_availability(self):
            return True, "ok"

        async def run_shell(self, workspace, command, shell, timeout_seconds=None, session=None, run_id=None):
            observed["session"] = session
            observed["run_id"] = run_id
            observed["timeout_seconds"] = timeout_seconds
            return SandboxCommandResult(
                command=command,
                shell=shell,
                executor="fake",
                exit_code=0,
                stdout="ok\n",
                stderr="",
                duration_ms=1,
                log_path="",
            )

    monkeypatch.setitem(runner._executors, "docker", FakeExecutor())
    monkeypatch.setattr(settings, "sandbox_executor", "docker")
    monkeypatch.setattr(settings, "sandbox_fail_closed", True)

    from app.services.session_types import AgentSession

    workspace = build_workspace(tmp_path / "sandbox")
    session = AgentSession(session_id=workspace.session_id)

    async def execute():
        await runner.run_shell(workspace, "echo hello", "bash", timeout_seconds=45, session=session, run_id="run_test")

    asyncio.run(execute())

    assert observed["session"] is session
    assert observed["run_id"] == "run_test"
    assert observed["timeout_seconds"] == 45


def test_runtime_timeout_is_clamped_by_max(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_command_timeout_seconds", 120)
    monkeypatch.setattr(settings, "sandbox_command_max_timeout_seconds", 300)

    assert session_runtime_service._effective_timeout_seconds(None) == 120
    assert session_runtime_service._effective_timeout_seconds(30) == 30
    assert session_runtime_service._effective_timeout_seconds(9999) == 300


def test_runtime_timeout_no_max_limit_when_zero(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_command_timeout_seconds", 120)
    monkeypatch.setattr(settings, "sandbox_command_max_timeout_seconds", 0)

    assert session_runtime_service._effective_timeout_seconds(None) == 120
    assert session_runtime_service._effective_timeout_seconds(30) == 30
    assert session_runtime_service._effective_timeout_seconds(9999) == 9999


def test_runtime_busy_error_when_runtime_remains_terminating(tmp_path, monkeypatch):
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
        status="terminating",
        generation=2,
        network_mode="none",
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
        last_started_at=now.isoformat(),
        last_used_at=now.isoformat(),
        idle_expires_at=(now + timedelta(days=1)).isoformat(),
        max_expires_at=(now + timedelta(days=7)).isoformat(),
        destroyed_at=None,
        destroy_reason=None,
        restart_count=0,
        workspace_root=str(workspace.root),
        home_root=str(workspace.home_dir),
        metadata={"runtime_spec": session_runtime_service._build_runtime_spec(settings.sandbox_docker_image)},
    )

    monkeypatch.setattr(settings, "sandbox_runtime_busy_wait_seconds", 0)

    async def fake_inspect(_container_name: str):
        return "restarting"

    monkeypatch.setattr(session_runtime_service, "_inspect_container_state", fake_inspect)

    async def execute():
        with pytest.raises(RuntimeBusyError, match="前一个命令仍在退出中"):
            await session_runtime_service.run_shell(
                workspace,
                command="echo hello",
                shell="bash",
            )

    asyncio.run(execute())


def test_runtime_start_error_when_runtime_already_failed(tmp_path):
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
        status="failed_start",
        generation=2,
        network_mode="none",
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
        last_started_at=now.isoformat(),
        last_used_at=now.isoformat(),
        idle_expires_at=(now + timedelta(days=1)).isoformat(),
        max_expires_at=(now + timedelta(days=7)).isoformat(),
        destroyed_at=now.isoformat(),
        destroy_reason="bootstrap_failed",
        restart_count=0,
        workspace_root=str(workspace.root),
        home_root=str(workspace.home_dir),
        metadata={"runtime_spec": session_runtime_service._build_runtime_spec(settings.sandbox_docker_image)},
    )

    async def fake_create_runtime(*_args, **_kwargs):
        raise RuntimeStartError(
            session_id=workspace.session_id,
            summary="沙箱 runtime 未能处于可执行状态，请重建运行环境。",
            runtime={"status": "failed_start", "generation": 3, "destroy_reason": "bootstrap_failed"},
        )

    original = session_runtime_service._create_runtime_locked
    session_runtime_service._create_runtime_locked = fake_create_runtime

    async def execute():
        with pytest.raises(RuntimeStartError, match="沙箱 runtime 未能处于可执行状态"):
            await session_runtime_service.run_shell(
                workspace,
                command="echo hello",
                shell="bash",
            )

    try:
        asyncio.run(execute())
    finally:
        session_runtime_service._create_runtime_locked = original


def test_workspace_sync_state_tombstones_are_minimized(tmp_path):
    workspace = build_workspace(tmp_path / "sandbox")
    state = session_workspace_sync_service.load_state(workspace)
    assert state.tombstones == ()

    session_workspace_sync_service.save_state(
        workspace,
        session_workspace_sync_service.load_state(workspace).__class__(
            tombstones=("work/repo", "work/repo/main.py", "skills/helper.py"),
        ),
    )

    restored = session_workspace_sync_service.load_state(workspace)
    assert set(restored.tombstones) == {"work/repo", "skills/helper.py"}
