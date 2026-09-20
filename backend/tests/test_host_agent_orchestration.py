from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.schemas.agent import AgentEvent
from app.services.agent_run_service import agent_run_service
from app.services.host_control_service import host_control_service
from app.runtime.event_protocol import make_event
from app.services.session_service import session_service
from app.services.session_types import AgentSession
from app.services.store import store_service
from app.services.subagent_service import subagent_service
from app.services.tool_service import SUBAGENT_TOOL_NAMES, tool_service


def initialize_isolated_runtime(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    store_service._db_path = storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    session_service._sessions.clear()
    settings.storage_root = storage_root
    store_service.initialize()


def create_platform() -> dict:
    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None
    return store_service.create_platform(
        platform_key="host-control",
        display_name="Host Control",
        host_type="embedded",
        description="host control test",
        owner_user_id=admin.user_id,
    )


def test_host_conversation_lifecycle_supports_hidden_creation_and_soft_delete(tmp_path):
    initialize_isolated_runtime(tmp_path)
    platform = create_platform()
    client = TestClient(app)
    headers = {"X-Aether-Platform-Secret": platform["host_secret"]}

    created = client.post(
        "/api/v1/host/conversations",
        headers=headers,
        json={
            "external_user_id": "user-1",
            "external_user_name": "User One",
            "title": "Hidden case",
            "visibility": "hidden",
        },
    )
    assert created.status_code == 200
    conversation_id = created.json()["data"]["conversation_id"]
    assert store_service.get_conversation(conversation_id)["visibility"] == "hidden"

    default_list = client.get(
        "/api/v1/host/conversations",
        headers=headers,
        params={"external_user_id": "user-1"},
    )
    assert default_list.status_code == 200
    assert default_list.json()["data"]["items"] == []

    hidden_list = client.get(
        "/api/v1/host/conversations",
        headers=headers,
        params={"external_user_id": "user-1", "include_hidden": True},
    )
    assert [item["conversation_id"] for item in hidden_list.json()["data"]["items"]] == [conversation_id]

    restored = client.post(f"/api/v1/host/conversations/{conversation_id}/restore", headers=headers)
    assert restored.status_code == 200
    assert restored.json()["data"]["visibility"] == "normal"

    deleted = client.delete(f"/api/v1/host/conversations/{conversation_id}", headers=headers)
    assert deleted.status_code == 200
    assert store_service.get_conversation(conversation_id)["deleted_at"] is not None


def test_run_events_are_persisted_and_replayable_after_live_state_is_gone(tmp_path):
    initialize_isolated_runtime(tmp_path)
    store_service.create_agent_run(
        run_id="run_replay",
        session_id="sess_replay",
        conversation_id=None,
        status="running",
    )
    for index, event_type in enumerate(("run_started", "content_delta", "completed"), start=1):
        seq = store_service.append_agent_run_event(
            run_id="run_replay",
            event_type=event_type,
            payload={"index": index},
            status="completed" if event_type == "completed" else None,
        )
        assert seq == index

    async def replay_events():
        queue = await agent_run_service.subscribe("run_replay")
        events = []
        while True:
            event = await queue.get()
            if event is None:
                break
            events.append(event)
        return events

    events = asyncio.run(replay_events())
    assert [event.type for event in events] == ["run_started", "content_delta", "completed"]
    assert [event.seq for event in events] == [1, 2, 3]
    assert store_service.get_agent_run("run_replay")["status"] == "completed"


def test_host_message_idempotency_key_replays_existing_run(tmp_path, monkeypatch):
    initialize_isolated_runtime(tmp_path)
    platform = create_platform()
    conversation = store_service.create_conversation(
        session_id="sess_idempotent",
        title="Idempotent",
        host_name="Host Control",
        platform_id=platform["platform_id"],
        external_user_id="user-idempotent",
    )

    async def fake_start_chat_run(session, message, **kwargs):
        return store_service.create_agent_run(
            run_id="run_idempotent",
            session_id=session.session_id,
            conversation_id=conversation["conversation_id"],
            status="running",
            request={"message": message},
            idempotency_key=kwargs.get("idempotency_key"),
        )["run_id"]

    monkeypatch.setattr(agent_run_service, "start_chat_run", fake_start_chat_run)

    async def send_twice():
        kwargs = {
            "platform": platform,
            "conversation_id": conversation["conversation_id"],
            "message": "hello",
            "allow_network": None,
            "client_message_id": None,
            "idempotency_key": "request-1",
            "reasoning_effort": None,
        }
        return await host_control_service.send_message(**kwargs), await host_control_service.send_message(**kwargs)

    first, second = asyncio.run(send_twice())
    assert first["run_id"] == "run_idempotent"
    assert second["run_id"] == "run_idempotent"
    assert second["idempotent_replay"] is True


def test_assistant_messages_include_run_status_and_subagent_results(tmp_path):
    initialize_isolated_runtime(tmp_path)
    platform = create_platform()
    client = TestClient(app)
    headers = {"X-Aether-Platform-Secret": platform["host_secret"]}
    created = client.post(
        "/api/v1/host/conversations",
        headers=headers,
        json={"external_user_id": "user-2", "external_user_name": "User Two"},
    )
    conversation_id = created.json()["data"]["conversation_id"]
    conversation = store_service.get_conversation(conversation_id)
    session = session_service.get_or_create(conversation["session_id"])
    session.messages.extend(
        [
            {
                "role": "assistant",
                "content": "first answer",
                "message_id": "msg_first",
                "run_id": "run_first",
                "timestamp": "2026-01-01T00:00:01+00:00",
            },
            {
                "role": "assistant",
                "content": "second answer",
                "message_id": "msg_second",
                "run_id": "run_second",
                "timestamp": "2026-01-01T00:00:02+00:00",
            },
        ]
    )
    session_service.persist(session)
    store_service.create_agent_run(
        run_id="run_first",
        session_id=session.session_id,
        conversation_id=conversation_id,
        status="failed",
    )
    store_service.create_agent_run(
        run_id="run_second",
        session_id=session.session_id,
        conversation_id=conversation_id,
        status="completed",
    )
    store_service.create_subagent_run(
        child_run_id="run_sub",
        workspace_id=session.workspace_id,
        parent_run_id="run_second",
        parent_session_id=session.session_id,
        child_session_id="sess_sub",
        name="researcher",
        task="research",
    )
    store_service.update_subagent_run(
        "run_sub",
        status="completed",
        result_text="subagent answer",
    )

    response = client.get(
        f"/api/v1/host/conversations/{conversation_id}/assistant-messages",
        headers=headers,
        params={"limit": 10, "include_subagents": True},
    )
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    by_id = {item["message_id"]: item for item in items}
    assert by_id["msg_second"]["run_status"] == "completed"
    assert by_id["msg_first"]["run_status"] == "failed"
    assert by_id["subagent_run_sub"]["subagent_run_id"] == "run_sub"
    assert by_id["subagent_run_sub"]["content"] == "subagent answer"


def test_subagent_tools_are_hidden_from_child_sessions(tmp_path):
    initialize_isolated_runtime(tmp_path)
    parent = AgentSession(session_id="sess_parent_tools")
    child = AgentSession(session_id="sess_child_tools", subagent_tools_enabled=False)

    parent_names = {
        schema["function"]["name"]
        for schema in tool_service.list_tool_schemas(parent)
    }
    child_names = {
        schema["function"]["name"]
        for schema in tool_service.list_tool_schemas(child)
    }
    assert SUBAGENT_TOOL_NAMES & parent_names
    assert not SUBAGENT_TOOL_NAMES & child_names

    child.allowed_tools = ["read"]
    filtered_names = {
        schema["function"]["name"]
        for schema in tool_service.list_tool_schemas(child)
    }
    assert filtered_names == {"read"}


def test_all_subagent_tools_dispatch_with_tool_name_and_run_id(tmp_path, monkeypatch):
    initialize_isolated_runtime(tmp_path)
    session = AgentSession(session_id="sess_subagent_dispatch")

    async def fake_execute_tool(parent_session, arguments, *, parent_run_id, tool_name):
        return {
            "tool_name": tool_name,
            "parent_run_id": parent_run_id,
            "arguments": arguments,
        }

    monkeypatch.setattr(subagent_service, "execute_tool", fake_execute_tool)
    cases = {
        "subagent_create": {"name": "researcher", "task": "analyze"},
        "subagent_send_message": {"subagent_run_id": "run_sub", "message": "continue"},
        "subagent_wait": {"subagent_run_id": "run_sub", "timeout_seconds": 5},
        "subagent_list": {},
        "subagent_cancel": {"subagent_run_id": "run_sub"},
        "subagent_get_result": {"subagent_run_id": "run_sub"},
    }

    async def execute_all():
        return {
            name: await tool_service.execute(session, name, arguments, run_id="run_parent")
            for name, arguments in cases.items()
        }

    results = asyncio.run(execute_all())
    assert set(results) == set(cases)
    assert all(result.data["parent_run_id"] == "run_parent" for result in results.values())
    assert all(result.data["tool_name"] == name for name, result in results.items())


def test_missing_subagent_id_returns_domain_error_not_internal_name_error(tmp_path):
    initialize_isolated_runtime(tmp_path)
    session = AgentSession(session_id="sess_missing_subagent")

    with pytest.raises(LookupError, match="子 Agent 不存在或不属于当前会话"):
        asyncio.run(
            tool_service.execute(
                session,
                "subagent_get_result",
                {"subagent_run_id": "run_missing"},
                run_id="run_parent",
            )
        )


def test_agent_run_service_persists_events_and_result(tmp_path, monkeypatch):
    initialize_isolated_runtime(tmp_path)
    agent_run_service._runs.clear()
    agent_run_service._session_runs.clear()
    session = session_service.get_or_create("sess_persisted_run")

    async def fake_stream_chat(stream_session, message, **kwargs):
        yield make_event(stream_session, "result", subtype="success", is_error=False, result="final answer")
        yield make_event(stream_session, "completed", subtype="success", elapsed_ms=12)

    from app.runtime.engine import agent_engine

    monkeypatch.setattr(agent_engine, "stream_chat", fake_stream_chat)

    async def drive():
        run_id = await agent_run_service.start_chat_run(session, "hello")
        await agent_run_service.wait_for_run(run_id)
        return run_id

    run_id = asyncio.run(drive())
    run = agent_run_service.get_run_view(run_id)
    events = store_service.list_agent_run_events(run_id)
    assert run["status"] == "completed"
    assert run["result"] == "final answer"
    assert [event["type"] for event in events] == ["result", "completed"]
    assert events[-1]["seq"] == 2


def test_subagent_creation_uses_hidden_child_conversation_and_one_level_depth(tmp_path, monkeypatch):
    initialize_isolated_runtime(tmp_path)
    platform = create_platform()
    parent = session_service.get_or_create("sess_parent_sub")
    parent.platform_id = platform["platform_id"]
    parent.external_user_id = "user-3"
    parent.host_name = "Host Control"
    parent.conversation_id = store_service.create_conversation(
        session_id=parent.session_id,
        title="Parent",
        host_name="Host Control",
        platform_id=platform["platform_id"],
        external_user_id="user-3",
    )["conversation_id"]
    session_service.persist(parent)

    async def fake_start_chat_run(session, message, **kwargs):
        return store_service.create_agent_run(
            run_id="run_child_sub",
            session_id=session.session_id,
            conversation_id=session.conversation_id,
            parent_run_id=kwargs.get("parent_run_id"),
            agent_kind="subagent",
            status="running",
            request={"message": message},
        )["run_id"]

    async def fake_publish(*args, **kwargs):
        return None

    monkeypatch.setattr(agent_run_service, "start_chat_run", fake_start_chat_run)
    monkeypatch.setattr(subagent_service, "_start_watcher", lambda **kwargs: None)
    monkeypatch.setattr(subagent_service, "_publish_parent_event", fake_publish)

    result = asyncio.run(
        subagent_service.create_subagent(
            parent,
            parent_run_id="run_parent_sub",
            name="researcher",
            task="analyze code",
            allowed_tools=["read", "glob"],
        )
    )
    child_session_id = store_service.get_subagent_run(result["subagent_run_id"])["child_session_id"]
    child = session_service.get_or_create(child_session_id)
    child_conversation = store_service.get_conversation_by_session(child_session_id)
    assert child.subagent_tools_enabled is False
    assert child.allowed_tools == ["read", "glob"]
    assert child_conversation["visibility"] == "hidden"
    assert store_service.get_agent_run("run_child_sub")["parent_run_id"] == "run_parent_sub"


def test_subagent_result_is_proactively_injected_into_parent_context(tmp_path):
    initialize_isolated_runtime(tmp_path)
    parent = session_service.get_or_create("sess_parent_result")
    store_service.create_agent_run(
        run_id="run_parent_result",
        session_id=parent.session_id,
        status="running",
    )
    store_service.create_subagent_run(
        child_run_id="run_child_result",
        workspace_id=parent.workspace_id,
        parent_run_id="run_parent_result",
        parent_session_id=parent.session_id,
        child_session_id="sess_child_result",
        name="researcher",
        task="research",
    )

    asyncio.run(
        subagent_service._inject_result(
            parent_run_id="run_parent_result",
            parent_session_id=parent.session_id,
            subagent_run_id="run_child_result",
            latest_run_id="run_child_result",
            name="researcher",
            status="completed",
            result_text="found the answer",
        )
    )

    injected = parent.messages[-1]
    event_types = [event["type"] for event in store_service.list_agent_run_events("run_parent_result")]
    assert injected["kind"] == "subagent_result"
    assert injected["subagent_run_id"] == "run_child_result"
    assert "found the answer" in injected["content"]
    assert "subagent_result_ready" in event_types
    assert "subagent_completed" in event_types
