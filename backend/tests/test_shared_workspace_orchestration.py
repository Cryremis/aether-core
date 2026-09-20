from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.session_service import session_service
from app.services.store import store_service
from app.services.subagent_service import subagent_service
from app.services.workspace_runtime_service import RuntimeBusyError, workspace_runtime_service
from app.services.workspace_service import workspace_service


def initialize_runtime(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    settings.storage_root = storage_root
    store_service._db_path = storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    session_service._sessions.clear()
    workspace_runtime_service._active_commands.clear()
    store_service.initialize()


def create_platform() -> dict:
    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None
    return store_service.create_platform(
        platform_key="shared-workspace",
        display_name="Shared Workspace",
        host_type="embedded",
        description="shared workspace test",
        owner_user_id=admin.user_id,
    )


def upsert_runtime(
    workspace_id: str,
    *,
    status: str = "running",
    workspace=None,
    idle_delta: timedelta = timedelta(minutes=-1),
) -> dict:
    now = datetime.now(timezone.utc)
    return store_service.upsert_workspace_runtime(
        workspace_id=workspace_id,
        owner_session_id=workspace_id.removeprefix("ws_"),
        conversation_id=None,
        platform_id=None,
        owner_user_id=None,
        external_user_id=None,
        container_name=f"container-{workspace_id}",
        container_id=f"container-id-{workspace_id}",
        image=settings.sandbox_docker_image,
        status=status,
        generation=1,
        network_mode="none",
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
        last_started_at=now.isoformat(),
        last_used_at=now.isoformat(),
        idle_expires_at=(now + idle_delta).isoformat(),
        max_expires_at=(now + timedelta(days=1)).isoformat(),
        destroyed_at=None,
        destroy_reason=None,
        restart_count=0,
        workspace_root="",
        home_root="",
        metadata={
            "runtime_spec": workspace_runtime_service._build_runtime_spec(
                settings.sandbox_docker_image,
                workspace,
            )
        },
    )


def test_parent_and_subagent_share_files_but_keep_contexts_isolated(tmp_path):
    initialize_runtime(tmp_path)
    parent = session_service.get_or_create("sess_shared_parent")
    child = session_service.get_or_create(
        "sess_shared_child",
        workspace_id=parent.workspace_id,
        role="subagent",
        parent_session_id=parent.session_id,
    )

    assert child.workspace_id == parent.workspace_id
    assert child.session_id != parent.session_id
    assert child.workspace.work_dir.resolve() == parent.workspace.work_dir.resolve()

    (parent.workspace.work_dir / "from-parent.txt").write_text("parent data", encoding="utf-8")
    assert (child.workspace.work_dir / "from-parent.txt").read_text(encoding="utf-8") == "parent data"

    (child.workspace.work_dir / "from-child.txt").write_text("child data", encoding="utf-8")
    assert (parent.workspace.work_dir / "from-child.txt").read_text(encoding="utf-8") == "child data"

    parent.messages.append({"role": "user", "content": "parent only"})
    child.messages.append({"role": "user", "content": "child only"})
    assert parent.messages != child.messages


def test_subagent_result_is_injected_while_parent_is_waiting(tmp_path):
    initialize_runtime(tmp_path)
    parent = session_service.get_or_create("sess_waiting_parent")
    parent.begin_run("run_waiting_parent")
    parent.mark_tool_running(
        "run_waiting_parent",
        tool_call_id="call_subagent_wait",
        tool_name="subagent_wait",
    )
    store_service.create_agent_run(
        run_id="run_waiting_parent",
        session_id=parent.session_id,
        status="running",
    )
    store_service.create_subagent_run(
        child_run_id="run_waiting_child",
        workspace_id=parent.workspace_id,
        parent_run_id="run_waiting_parent",
        parent_session_id=parent.session_id,
        child_session_id="sess_waiting_child",
        name="researcher",
        task="research",
    )

    asyncio.run(
        subagent_service._inject_result(
            parent_run_id="run_waiting_parent",
            parent_session_id=parent.session_id,
            subagent_run_id="run_waiting_child",
            latest_run_id="run_waiting_child",
            name="researcher",
            status="completed",
            result_text="shared workspace answer",
        )
    )

    injected = parent.messages[-1]
    assert injected["kind"] == "subagent_result"
    assert "shared workspace answer" in injected["content"]
    assert injected["workspace_id"] == parent.workspace_id
    parent.finish_run("run_waiting_parent")


def test_expired_runtime_is_not_collected_while_workspace_has_active_work(tmp_path, monkeypatch):
    initialize_runtime(tmp_path)
    parent = session_service.get_or_create("sess_active_parent")
    parent_conversation = store_service.create_conversation(
        session_id=parent.session_id,
        workspace_id=parent.workspace_id,
        title="Active parent",
        host_name="Host",
    )
    child = session_service.get_or_create(
        "sess_active_child",
        workspace_id=parent.workspace_id,
        role="subagent",
        parent_session_id=parent.session_id,
    )
    child_conversation = store_service.create_conversation(
        session_id=child.session_id,
        workspace_id=parent.workspace_id,
        title="Active child",
        host_name="Host",
        metadata={"subagent": True, "parent_session_id": parent.session_id},
    )
    store_service.create_agent_run(
        run_id="run_active_parent",
        session_id=parent.session_id,
        conversation_id=parent_conversation["conversation_id"],
        status="running",
    )
    store_service.create_agent_run(
        run_id="run_active_child",
        session_id=child.session_id,
        conversation_id=child_conversation["conversation_id"],
        status="running",
    )
    upsert_runtime(parent.workspace_id)

    async def fail_collect(workspace_id: str, *, reason: str):
        raise AssertionError("active workspace must not be collected")

    monkeypatch.setattr(workspace_runtime_service, "_collect_locked", fail_collect)
    asyncio.run(workspace_runtime_service.collect_expired_runtimes())
    assert store_service.get_workspace_runtime(parent.workspace_id)["status"] == "running"


def test_runtime_rebuild_is_blocked_while_commands_execute(tmp_path):
    initialize_runtime(tmp_path)
    parent = session_service.get_or_create("sess_busy_parent")
    workspace_runtime_service._active_commands[parent.workspace_id] = 1
    try:
        with pytest.raises(RuntimeBusyError):
            asyncio.run(
                workspace_runtime_service.rebuild_runtime(
                    parent.workspace,
                    reason="agent_requested_rebuild",
                )
            )
    finally:
        workspace_runtime_service._active_commands.clear()


def test_workspace_runtime_executes_commands_concurrently(tmp_path, monkeypatch):
    initialize_runtime(tmp_path)
    parent = session_service.get_or_create("sess_concurrent_parent")
    upsert_runtime(
        parent.workspace_id,
        workspace=parent.workspace,
        idle_delta=timedelta(days=1),
    )
    assert workspace_runtime_service._detect_runtime_recreate_reason(
        store_service.get_workspace_runtime(parent.workspace_id),
        workspace_runtime_service._now(),
        parent.workspace,
    ) is None
    observed_active_counts: list[int] = []

    async def fake_hydrate_container(**kwargs):
        observed_active_counts.append(
            workspace_runtime_service.active_command_count(parent.workspace_id)
        )
        await asyncio.sleep(0.02)

    async def fake_create_exec_process(*args, **kwargs):
        await asyncio.sleep(0.02)
        return SimpleNamespace(returncode=0)

    async def fake_collect_stream_output(*args, **kwargs):
        await asyncio.sleep(0.02)
        return b"ok\n", b""

    async def fake_capture_container_delta(**kwargs):
        return None

    async def fake_inspect_container_state(_container_name: str):
        return "running"

    async def fail_create_runtime(*args, **kwargs):
        raise AssertionError("concurrent commands must reuse the workspace runtime")

    monkeypatch.setattr(
        "app.services.workspace_runtime_service.workspace_sync_service.hydrate_container",
        fake_hydrate_container,
    )
    monkeypatch.setattr(workspace_runtime_service, "_create_exec_process", fake_create_exec_process)
    monkeypatch.setattr(workspace_runtime_service, "_collect_stream_output", fake_collect_stream_output)
    monkeypatch.setattr(workspace_runtime_service, "_inspect_container_state", fake_inspect_container_state)
    monkeypatch.setattr(
        "app.services.workspace_runtime_service.workspace_sync_service.capture_container_delta",
        fake_capture_container_delta,
    )
    monkeypatch.setattr(workspace_runtime_service, "_create_runtime_locked", fail_create_runtime)

    async def execute_both():
        return await asyncio.gather(
            workspace_runtime_service.run_shell(
                parent.workspace,
                command="echo first",
                shell="bash",
            ),
            workspace_runtime_service.run_shell(
                parent.workspace,
                command="echo second",
                shell="bash",
            ),
        )

    results = asyncio.run(execute_both())
    assert [result.exit_code for result in results] == [0, 0]
    assert observed_active_counts == [1, 2]


def test_host_workspace_api_exposes_members_and_runtime(tmp_path):
    initialize_runtime(tmp_path)
    platform = create_platform()
    parent = session_service.get_or_create("sess_api_parent")
    parent_conversation = store_service.create_conversation(
        session_id=parent.session_id,
        workspace_id=parent.workspace_id,
        title="API parent",
        host_name="Host",
        platform_id=platform["platform_id"],
        external_user_id="user-api",
    )
    child = session_service.get_or_create(
        "sess_api_child",
        workspace_id=parent.workspace_id,
        role="subagent",
        parent_session_id=parent.session_id,
    )
    store_service.create_conversation(
        session_id=child.session_id,
        workspace_id=parent.workspace_id,
        title="API child",
        host_name="Host",
        platform_id=platform["platform_id"],
        external_user_id="user-api",
        visibility="hidden",
        metadata={"subagent": True, "parent_session_id": parent.session_id},
    )
    store_service.update_workspace(
        parent.workspace_id,
        owner_conversation_id=parent_conversation["conversation_id"],
    )
    upsert_runtime(parent.workspace_id)

    client = TestClient(app)
    headers = {"X-Aether-Platform-Secret": platform["host_secret"]}
    workspace_response = client.get(
        f"/api/v1/host/workspaces/{parent.workspace_id}",
        headers=headers,
    )
    members_response = client.get(
        f"/api/v1/host/workspaces/{parent.workspace_id}/members",
        headers=headers,
    )
    runtime_response = client.get(
        f"/api/v1/host/workspaces/{parent.workspace_id}/runtime",
        headers=headers,
    )

    assert workspace_response.status_code == 200
    assert members_response.status_code == 200
    assert runtime_response.status_code == 200
    workspace = workspace_response.json()["data"]
    members = members_response.json()["data"]["items"]
    runtime = runtime_response.json()["data"]
    assert workspace["workspace_id"] == parent.workspace_id
    assert {item["role"] for item in members} == {"owner", "subagent"}
    assert runtime["workspace_id"] == parent.workspace_id
    assert runtime["status"] == "running"


def test_legacy_subagent_files_are_merged_into_parent_workspace(tmp_path):
    initialize_runtime(tmp_path)
    parent = session_service.get_or_create("sess_legacy_parent")
    store_service.create_conversation(
        session_id=parent.session_id,
        workspace_id=parent.workspace_id,
        title="Legacy parent",
        host_name="Host",
    )
    child = session_service.get_or_create(
        "sess_legacy_child",
        workspace_id=parent.workspace_id,
        role="subagent",
        parent_session_id=parent.session_id,
    )
    store_service.create_conversation(
        session_id=child.session_id,
        workspace_id=parent.workspace_id,
        title="Legacy child",
        host_name="Host",
        metadata={"subagent": True, "parent_session_id": parent.session_id},
    )

    parent_sandbox = settings.sessions_root / "sess_legacy_parent" / "sandbox"
    child_sandbox = settings.sessions_root / "sess_legacy_child" / "sandbox"
    (parent_sandbox / "work").mkdir(parents=True, exist_ok=True)
    (child_sandbox / "work").mkdir(parents=True, exist_ok=True)
    (parent_sandbox / "work" / "parent.txt").write_text("parent file", encoding="utf-8")
    (child_sandbox / "work" / "child.txt").write_text("child file", encoding="utf-8")

    workspace_service.migrate_legacy_layout()

    workspace_root = settings.sessions_root / "workspaces" / parent.workspace_id / "sandbox"
    assert (workspace_root / "work" / "parent.txt").read_text(encoding="utf-8") == "parent file"
    migrated_child = (
        workspace_root
        / "work"
        / "_migrated"
        / "subagents"
        / child.session_id
        / "work"
        / "child.txt"
    )
    assert migrated_child.read_text(encoding="utf-8") == "child file"
    assert not parent_sandbox.exists()
    assert not child_sandbox.exists()

    state_path = settings.sessions_root / "session-state" / child.session_id / "session.json"
    state_payload = {"marker": "retry-preserved"}
    state_path.write_text(json.dumps(state_payload), encoding="utf-8")
    workspace_service._write_session_state(child_sandbox, child.session_id, parent.workspace_id)
    assert state_path.read_text(encoding="utf-8").find("retry-preserved") >= 0
