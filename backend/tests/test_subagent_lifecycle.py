from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.runtime.event_protocol import make_event
from app.services.agent_run_service import agent_run_service
from app.services.session_service import session_service
from app.services.session_types import AgentSession
from app.services.store import store_service
from app.services.subagent_service import subagent_service
from app.services.token_service import token_service


def initialize_isolated_runtime(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    store_service._db_path = storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    session_service._sessions.clear()
    settings.storage_root = storage_root
    store_service.initialize()


def create_lifecycle_fixture(owner_user_id: int | None = None) -> tuple[AgentSession, AgentSession, dict]:
    parent = session_service.get_or_create("sess_parent_lifecycle")
    child = session_service.get_or_create("sess_child_lifecycle", workspace_id=parent.workspace_id)
    store_service.create_agent_run(
        run_id="run_parent_lifecycle",
        session_id=parent.session_id,
        conversation_id=parent.conversation_id,
        status="running",
    )
    store_service.create_agent_run(
        run_id="run_child_lifecycle",
        session_id=child.session_id,
        conversation_id=child.conversation_id,
        status="running",
    )
    row = store_service.create_subagent(
        subagent_id="subagent_lifecycle",
        workspace_id=parent.workspace_id,
        parent_run_id="run_parent_lifecycle",
        parent_session_id=parent.session_id,
        child_session_id=child.session_id,
        name="researcher",
        task="research the topic",
        latest_run_id="run_child_lifecycle",
    )
    if owner_user_id is not None:
        store_service.create_conversation(
            session_id=parent.session_id,
            title="Parent lifecycle",
            host_name="AetherCore",
            owner_user_id=owner_user_id,
        )
        store_service.create_conversation(
            session_id=child.session_id,
            workspace_id=parent.workspace_id,
            title="Subagent: researcher",
            host_name="AetherCore",
            owner_user_id=owner_user_id,
            visibility="hidden",
            metadata={"subagent": True},
        )
    return parent, child, row


def test_subagent_destroy_is_lifecycle_not_execution_status(tmp_path):
    initialize_isolated_runtime(tmp_path)
    parent, child, row = create_lifecycle_fixture()
    child.active_run_view = {
        "run_id": "run_child_lifecycle",
        "assistant": {
            "blocks": [
                {"kind": "tool", "title": "read", "meta": "file.txt", "status": "running"},
            ],
        },
    }
    session_service.persist(child)

    listed = subagent_service.list_subagents(parent)
    assert len(listed) == 1
    assert listed[0]["child_session_id"] == child.session_id
    assert listed[0]["destroyed_at"] is None
    assert listed[0]["current_action"] == {"kind": "tool", "label": "read file.txt"}

    destroyed = asyncio.run(subagent_service.destroy(parent, str(row["subagent_id"])))
    assert destroyed["status"] == "cancelled"
    assert destroyed["destroyed_at"]
    assert subagent_service.list_subagents(parent) == []
    assert store_service.get_subagent(str(row["subagent_id"])) is not None


def test_destroyed_subagent_blocks_followup_and_result_injection(tmp_path):
    initialize_isolated_runtime(tmp_path)
    parent, _, row = create_lifecycle_fixture()
    subagent_id = str(row["subagent_id"])

    destroyed = asyncio.run(subagent_service.destroy(parent, subagent_id))
    assert destroyed["destroyed_at"]

    with pytest.raises(RuntimeError, match="已销毁"):
        asyncio.run(
            subagent_service.send_message(
                parent,
                parent_run_id="run_parent_lifecycle",
                subagent_id=subagent_id,
                message="continue",
            )
        )

    with pytest.raises(RuntimeError, match="已销毁"):
        subagent_service.get_result(parent, subagent_id)

    asyncio.run(
        subagent_service._inject_result(
            parent_run_id="run_parent_lifecycle",
            parent_session_id=parent.session_id,
            subagent_id=subagent_id,
            latest_run_id="run_child_lifecycle",
            name="researcher",
            status="completed",
            result_text="answer",
        )
    )
    restored_parent = session_service.get_or_create(parent.session_id)
    assert all(message.get("subagent_id") != subagent_id for message in restored_parent.messages)
    assert store_service.list_destroyed_subagent_ids_for_session(parent.session_id) == {subagent_id}


def test_subagent_endpoints_and_exact_hidden_history_access(tmp_path):
    initialize_isolated_runtime(tmp_path)
    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None
    token, _ = token_service.create_user_token(admin.user_id, admin.role)
    parent, child, row = create_lifecycle_fixture(owner_user_id=admin.user_id)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    listed = client.get(f"/api/v1/agent/sessions/{parent.session_id}/subagents", headers=headers)
    assert listed.status_code == 200
    assert [item["subagent_id"] for item in listed.json()["data"]] == [row["subagent_id"]]
    assert listed.json()["data"][0]["child_session_id"] == child.session_id

    destroyed = client.post(
        f"/api/v1/agent/sessions/{parent.session_id}/subagents/{row['subagent_id']}/destroy",
        headers=headers,
    )
    assert destroyed.status_code == 200
    assert destroyed.json()["data"]["destroyed_at"]

    filtered = client.get(f"/api/v1/agent/sessions/{parent.session_id}/subagents", headers=headers)
    assert filtered.status_code == 200
    assert filtered.json()["data"] == []

    exact_history = client.get(f"/api/v1/agent/sessions/{child.session_id}", headers=headers)
    assert exact_history.status_code == 200
    assert exact_history.json()["data"]["session_id"] == child.session_id


def test_child_conversation_inherits_parent_access_when_runtime_state_is_missing(tmp_path):
    initialize_isolated_runtime(tmp_path)
    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None
    parent = session_service.get_or_create("sess_parent_access")
    store_service.create_conversation(
        session_id=parent.session_id,
        title="Parent access",
        host_name="AetherCore",
        owner_user_id=admin.user_id,
    )

    child = subagent_service._create_child_session(parent, name="researcher", allowed_tools=[])
    conversation = store_service.get_conversation_by_session(child.session_id)
    assert conversation is not None
    assert conversation["owner_user_id"] == admin.user_id
    assert conversation["visibility"] == "hidden"


def test_existing_child_conversations_are_backfilled_with_parent_access(tmp_path):
    initialize_isolated_runtime(tmp_path)
    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None
    parent = session_service.get_or_create("sess_parent_backfill")
    child = session_service.get_or_create("sess_child_backfill")
    store_service.create_conversation(
        session_id=parent.session_id,
        title="Parent backfill",
        host_name="AetherCore",
        owner_user_id=admin.user_id,
    )
    store_service.create_conversation(
        session_id=child.session_id,
        title="旧的子代理首句标题",
        host_name="AetherCore",
        visibility="hidden",
    )
    store_service.create_subagent(
        subagent_id="subagent_backfill",
        workspace_id=parent.workspace_id,
        parent_run_id="run_parent_backfill",
        parent_session_id=parent.session_id,
        child_session_id=child.session_id,
        name="legacy",
        task="legacy task",
        latest_run_id="run_child_backfill",
    )

    store_service.initialize()
    conversation = store_service.get_conversation_by_session(child.session_id)
    assert conversation is not None
    assert conversation["owner_user_id"] == admin.user_id
    assert conversation["title"] == "Subagent: legacy"


def test_subagent_first_message_does_not_replace_name_title(tmp_path, monkeypatch):
    initialize_isolated_runtime(tmp_path)
    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None
    parent = session_service.get_or_create("sess_parent_title")
    store_service.create_conversation(
        session_id=parent.session_id,
        title="Parent title",
        host_name="AetherCore",
        owner_user_id=admin.user_id,
    )
    child = subagent_service._create_child_session(parent, name="researcher", allowed_tools=[])
    agent_run_service._runs.clear()
    agent_run_service._session_runs.clear()

    from app.runtime.engine import agent_engine

    async def fake_stream_chat(session, message, **kwargs):
        yield make_event(session, "completed", subtype="success", elapsed_ms=1)

    monkeypatch.setattr(agent_engine, "stream_chat", fake_stream_chat)

    async def drive():
        run_id = await agent_run_service.start_chat_run(
            child,
            "这是一段很长的首条任务描述，不应该覆盖子代理名称标题。",
            agent_kind="subagent",
        )
        return await agent_run_service.wait_for_run(run_id)

    run = asyncio.run(drive())
    assert run["status"] == "completed"
    conversation = store_service.get_conversation_by_session(child.session_id)
    assert conversation is not None
    assert conversation["title"] == "Subagent: researcher"
