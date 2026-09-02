# backend/tests/test_agent_engine.py
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
import pytest

from app.core.config import settings
from app.runtime.engine import agent_engine
from app.services.context.context_pipeline import context_pipeline
from app.services.session_service import session_service
from app.services.session_types import AgentSession
from app.services.store import store_service
from app.services.tool_catalog_service import tool_catalog_service


async def collect_stream(session: AgentSession, message: str) -> list[dict]:
    events: list[dict] = []
    async for event in agent_engine.stream_chat(session, message):
        events.append(event.model_dump(mode="json"))
    return events


def initialize_store(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    store_service._db_path = storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.storage_root = storage_root
    store_service.initialize()


def seed_verbose_history(session: AgentSession, turns: int = 6) -> None:
    for turn in range(1, turns + 1):
        session.messages.extend(
            [
                {
                    "role": "user",
                    "content": f"user turn {turn}",
                    "turn_index": turn,
                    "timestamp": f"2026-01-{turn:02d}T00:00:00+00:00",
                },
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": f"call_{turn}",
                            "type": "function",
                            "function": {"name": "sandbox_shell", "arguments": '{"command":"echo hello"}'},
                        }
                    ],
                    "turn_index": turn,
                    "timestamp": f"2026-01-{turn:02d}T00:00:01+00:00",
                },
                {
                    "role": "tool",
                    "tool_call_id": f"call_{turn}",
                    "tool_name": "sandbox_shell",
                    "content": "x" * 5000,
                    "turn_index": turn,
                    "timestamp": f"2026-01-{turn:02d}T00:00:02+00:00",
                },
                {
                    "role": "assistant",
                    "content": f"assistant turn {turn}",
                    "turn_index": turn,
                    "timestamp": f"2026-01-{turn:02d}T00:00:03+00:00",
                },
            ]
        )


def build_session(session_id: str, **overrides) -> AgentSession:
    session = session_service.get_or_create(session_id)
    for key, value in overrides.items():
        setattr(session, key, value)
    return session


def test_agent_engine_returns_model_content_without_hardcoded_fallback(monkeypatch, tmp_path):
    initialize_store(tmp_path)

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        yield {
            "choices": [
                {
                    "delta": {"content": "this is the real model answer"},
                    "finish_reason": "stop",
                }
            ]
        }

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])

    session = build_session("sess_engine_success")
    events = asyncio.run(collect_stream(session, "reply directly"))

    result_events = [item for item in events if item["type"] == "result"]
    assert len(result_events) == 1
    assert result_events[0]["payload"]["subtype"] == "success"
    assert result_events[0]["payload"]["result"] == "this is the real model answer"
    assert session.messages[-1]["content"] == "this is the real model answer"
    assert session.transcript[-1]["role"] == "assistant"
    assert session.transcript[-1]["blocks"][-1]["kind"] == "content"
    assert session.transcript[-1]["blocks"][-1]["content"] == "this is the real model answer"
    event_types = [item["type"] for item in events]
    assert "workboard_snapshot" in event_types
    assert "elicitation_snapshot" in event_types
    assert "message" not in event_types
    committed = next(item for item in events if item["type"] == "message_committed")
    assert committed["payload"]["message"]["role"] == "user"
    assert committed["payload"]["message"]["content"] == "reply directly"


def test_agent_engine_emits_committed_user_message_with_client_id(monkeypatch, tmp_path):
    initialize_store(tmp_path)

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        yield {
            "choices": [
                {
                    "delta": {"content": "ok"},
                    "finish_reason": "stop",
                }
            ]
        }

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])

    session = build_session("sess_engine_committed_user")

    async def run_flow():
        events: list[dict] = []
        async for event in agent_engine.stream_chat(session, "hello world", client_message_id="client-user-1"):
            events.append(event.model_dump(mode="json"))
        return events

    events = asyncio.run(run_flow())

    committed = next(item for item in events if item["type"] == "message_committed")
    assert committed["payload"]["client_message_id"] == "client-user-1"
    assert committed["payload"]["message"]["role"] == "user"
    assert committed["payload"]["message"]["id"] == session.messages[0]["message_id"]
    assert committed["payload"]["message"]["content"] == "hello world"


def test_agent_engine_injects_runtime_state_context(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    observed_messages: list[list[dict]] = []

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        observed_messages.append(messages)
        yield {
            "choices": [
                {
                    "delta": {"content": "done"},
                    "finish_reason": "stop",
                }
            ]
        }

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])

    session = build_session("sess_engine_runtime_state")
    from app.services.runtime_state import runtime_state_service

    runtime_state_service.update_workboard(
        session,
        {"ops": [{"op": "add_item", "id": "task_1", "title": "Track work", "status": "in_progress"}]},
    )
    runtime_state_service.request_user_input(
        session,
        {
            "title": "Need preference",
            "questions": [{"id": "q1", "header": "Choice", "question": "Choose one", "options": [{"label": "A"}]}],
        },
    )

    asyncio.run(collect_stream(session, "continue"))
    system_messages = [message for message in observed_messages[0] if message.get("role") == "system"]
    merged = "\n".join(str(message.get("content", "")) for message in system_messages)
    assert "workboard_state" in merged
    assert "elicitation_state" in merged


def test_agent_engine_injects_platform_and_host_system_prompts(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    observed_messages: list[list[dict]] = []

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        observed_messages.append(messages)
        yield {
            "choices": [
                {
                    "delta": {"content": "done"},
                    "finish_reason": "stop",
                }
            ]
        }

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])

    platform = store_service.get_platform_by_key("standalone")
    assert platform is not None
    store_service.upsert_platform_prompt_config(
        platform_id=platform["platform_id"],
        enabled=True,
        system_prompt="平台={{platform.display_name}} 用户={{host.user.id}}",
    )

    session = build_session(
        "sess_engine_prompt_layers",
        host_name="Demo Host",
        host_context={
            "user": {"id": "u-100"},
            "page": {"pathname": "/orders"},
        },
        host_system_prompts=[
            {
                "key": "page-focus",
                "content": "页面={{host.page.pathname}}",
                "enabled": True,
            }
        ],
    )
    store_service.create_conversation(
        session_id=session.session_id,
        title="Prompt test",
        host_name=session.host_name,
        platform_id=platform["platform_id"],
    )

    asyncio.run(collect_stream(session, "hello"))

    system_messages = [message for message in observed_messages[0] if message.get("role") == "system"]
    assert any("平台=AetherCore 用户=u-100" in str(message.get("content", "")) for message in system_messages)
    assert any("页面=/orders" in str(message.get("content", "")) for message in system_messages)
    assert any("## 宿主信息" in str(message.get("content", "")) for message in system_messages)


def test_agent_engine_fallback_conversation_inherits_platform_context(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    observed_messages: list[list[dict]] = []

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        observed_messages.append(messages)
        yield {
            "choices": [
                {
                    "delta": {"content": "done"},
                    "finish_reason": "stop",
                }
            ]
        }

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])

    platform = store_service.get_platform_by_key("standalone")
    assert platform is not None
    store_service.upsert_platform_prompt_config(
        platform_id=platform["platform_id"],
        enabled=True,
        system_prompt="平台注入校验={{platform.display_name}}",
    )

    source = store_service.create_conversation(
        session_id="sess_engine_seed_source",
        title="Seed source",
        host_name="Seed Host",
        platform_id=platform["platform_id"],
    )

    session = build_session(
        "sess_engine_seed_target",
        conversation_id=source["conversation_id"],
        host_name="Seed Host",
    )

    events = asyncio.run(collect_stream(session, "hello"))
    assert any(item["type"] == "result" and item["payload"]["subtype"] == "success" for item in events)

    created = store_service.get_conversation_by_session(session.session_id)
    assert created is not None
    assert created.get("platform_id") == platform["platform_id"]

    system_messages = [message for message in observed_messages[0] if message.get("role") == "system"]
    assert any("平台注入校验=AetherCore" in str(message.get("content", "")) for message in system_messages)


def test_agent_engine_does_not_interrupt_long_run_when_stall_guard_disabled(monkeypatch, tmp_path):
    initialize_store(tmp_path)

    rounds = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_repeat_1",
                                "function": {
                                    "name": "sandbox_shell",
                                    "arguments": '{"command":"echo hello","shell":"bash"}',
                                },
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_repeat_2",
                                "function": {
                                    "name": "sandbox_shell",
                                    "arguments": '{"command":"echo hello","shell":"bash"}',
                                },
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {"content": "finished after repeated tool calls"},
                    "finish_reason": "stop",
                }
            ]
        },
    ]
    round_index = {"value": 0}

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        current = rounds[round_index["value"]]
        round_index["value"] += 1
        yield current

    async def fake_execute(session, tool_name, arguments):
        return {
            "command": arguments["command"],
            "shell": arguments.get("shell", "bash"),
            "executor": "docker",
            "exit_code": 0,
            "stdout": "hello\n",
            "stderr": "",
            "duration_ms": 10,
            "log_path": "logs/cmd_demo.json",
        }

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
    monkeypatch.setattr("app.runtime.engine.tool_service.execute", fake_execute)

    session = build_session("sess_engine_long_run")
    events = asyncio.run(collect_stream(session, "continue long task"))

    result_events = [item for item in events if item["type"] == "result"]
    assert len(result_events) == 1
    assert result_events[0]["payload"]["subtype"] == "success"
    assert result_events[0]["payload"]["result"] == "finished after repeated tool calls"
    assert all(item["payload"].get("subtype") != "error_stalled" for item in result_events)
    assert any(block["kind"] == "tool" for block in session.transcript[-1]["blocks"])
    assert any(message.get("tool_calls") for message in session.messages if message.get("role") == "assistant")
    assert any(message.get("role") == "tool" for message in session.messages)


def test_agent_engine_refreshes_host_tools_between_model_rounds(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    observed_tool_names: list[set[str]] = []
    executed_snapshots: list[set[str]] = []
    rounds = {"value": 0}

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        names = {item["function"]["name"] for item in tools}
        observed_tool_names.append(names)
        current_round = rounds["value"]
        rounds["value"] += 1
        if current_round == 0:
            assert "load_dynamic_tool" in names
            assert "dynamic_task_tool" not in names
            yield {
                "choices": [{
                    "delta": {"tool_calls": [{
                        "index": 0,
                        "id": "call_load_dynamic",
                        "function": {"name": "load_dynamic_tool", "arguments": "{}"},
                    }]},
                    "finish_reason": "tool_calls",
                }]
            }
            return
        if current_round == 1:
            assert "dynamic_task_tool" in names
            yield {
                "choices": [{
                    "delta": {"tool_calls": [{
                        "index": 0,
                        "id": "call_dynamic_task",
                        "function": {"name": "dynamic_task_tool", "arguments": '{"task_id":"task-1"}'},
                    }]},
                    "finish_reason": "tool_calls",
                }]
            }
            return
        yield {
            "choices": [{
                "delta": {"content": "dynamic task completed"},
                "finish_reason": "stop",
            }]
        }

    async def fake_execute(session, tool_name, arguments, *, catalog_snapshot=None):
        assert catalog_snapshot is not None
        executed_snapshots.append(set(catalog_snapshot.host_descriptors_by_name))
        if tool_name == "load_dynamic_tool":
            assert "dynamic_task_tool" not in catalog_snapshot.host_descriptors_by_name
            tool_catalog_service.apply_operations(
                session,
                source_id="host:dynamic",
                upserts=[{
                    "name": "dynamic_task_tool",
                    "description": "execute a dynamically loaded task",
                    "endpoint": "/api/tools/dynamic-task",
                    "method": "POST",
                    "input_schema": {
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"],
                    },
                }],
                removals=[],
            )
            return {"summary": "dynamic tool loaded"}
        assert tool_name == "dynamic_task_tool"
        assert "dynamic_task_tool" in catalog_snapshot.host_descriptors_by_name
        return {"summary": f"executed {arguments['task_id']}"}

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.execute", fake_execute)

    session = build_session("sess_engine_dynamic_tools")
    session.tool_refresh_policy = "round_boundary"
    tool_catalog_service.replace(
        session,
        [{
            "name": "load_dynamic_tool",
            "description": "load another host tool",
            "endpoint": "/api/tools/load",
            "method": "POST",
            "input_schema": {"type": "object", "properties": {}},
        }],
        source_id="host:base",
        replace_all=True,
    )

    events = asyncio.run(collect_stream(session, "load and execute the dynamic task"))

    assert len(observed_tool_names) == 3
    assert "dynamic_task_tool" not in executed_snapshots[0]
    assert "dynamic_task_tool" in executed_snapshots[1]
    catalog_events = [item for item in events if item["type"] == "tool_catalog_changed"]
    assert len(catalog_events) == 1
    assert catalog_events[0]["payload"]["added"] == ["dynamic_task_tool"]
    assert next(item for item in events if item["type"] == "result")["payload"]["result"] == "dynamic task completed"


def test_agent_engine_injects_skill_content_after_invoke_skill(monkeypatch, tmp_path):
    initialize_store(tmp_path)

    observed_messages: list[list[dict]] = []
    rounds = {"value": 0}

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        observed_messages.append(messages)
        if rounds["value"] == 0:
            rounds["value"] += 1
            yield {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_skill_1",
                                    "function": {
                                        "name": "invoke_skill",
                                        "arguments": '{"skill_name":"data-analysis"}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
            return

        yield {
            "choices": [
                {
                    "delta": {"content": "skill workflow finished"},
                    "finish_reason": "stop",
                }
            ]
        }

    async def fake_execute(session, tool_name, arguments):
        assert tool_name == "invoke_skill"
        return {
            "public_output": {"loaded": True, "skill": {"name": "data-analysis"}},
            "injected_messages": [
                {
                    "role": "user",
                    "content": '<aether_skill name="data-analysis" source="built_in">skill loaded</aether_skill>',
                }
            ],
        }

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
    monkeypatch.setattr("app.runtime.engine.tool_service.execute", fake_execute)

    session = build_session("sess_engine_skill")
    events = asyncio.run(collect_stream(session, "analyze data"))

    assert len(observed_messages) == 2
    assert any(
        message.get("role") == "user" and "aether_skill" in str(message.get("content", ""))
        for message in observed_messages[1]
    )
    result_events = [item for item in events if item["type"] == "result"]
    assert len(result_events) == 1
    assert result_events[0]["payload"]["subtype"] == "success"
    assert result_events[0]["payload"]["result"] == "skill workflow finished"


def test_agent_engine_emits_runtime_event_before_tool_finished(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    rounds = {"value": 0}

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        if rounds["value"] == 0:
            rounds["value"] += 1
            yield {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_runtime_1",
                                    "function": {
                                        "name": "sandbox_shell",
                                        "arguments": '{"command":"pip install pandas","shell":"bash"}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
            return

        yield {
            "choices": [
                {
                    "delta": {"content": "runtime settled"},
                    "finish_reason": "stop",
                }
            ]
        }

    async def fake_execute(session, tool_name, arguments):
        return {
            "command": arguments["command"],
            "shell": arguments.get("shell", "bash"),
            "executor": "docker",
            "exit_code": 0,
            "stdout": "ok\n",
            "stderr": "",
            "duration_ms": 10,
            "log_path": "logs/cmd_runtime.json",
            "runtime_events": [
                {
                    "type": "runtime_recreated",
                    "payload": {
                        "status": "recreated",
                        "reason": "runtime_config_changed",
                        "generation": 2,
                    },
                }
            ],
        }

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
    monkeypatch.setattr("app.runtime.engine.tool_service.execute", fake_execute)

    session = build_session("sess_engine_runtime_event")
    events = asyncio.run(collect_stream(session, "repair runtime"))
    event_types = [item["type"] for item in events]
    assert "runtime_recreated" in event_types
    assert "tool_finished" in event_types
    assert event_types.index("runtime_recreated") < event_types.index("tool_finished")
    assert any(
        block.get("kind") == "runtime_notice" and block.get("eventType") == "runtime_recreated"
        for block in session.transcript[-1]["blocks"]
    )


def test_agent_engine_emits_tool_progress_for_long_running_tools(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    rounds = {"value": 0}

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        if rounds["value"] == 0:
            rounds["value"] += 1
            yield {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_slow_tool",
                                    "function": {
                                        "name": "sandbox_shell",
                                        "arguments": '{"command":"python slow.py","shell":"bash"}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
            return

        yield {
            "choices": [
                {
                    "delta": {"content": "slow tool finished"},
                    "finish_reason": "stop",
                }
            ]
        }

    async def fake_execute(session, tool_name, arguments):
        await asyncio.sleep(0.03)
        return {
            "command": arguments["command"],
            "shell": arguments.get("shell", "bash"),
            "executor": "docker",
            "exit_code": 0,
            "stdout": "done\n",
            "stderr": "",
            "duration_ms": 30,
            "log_path": "logs/cmd_slow.json",
        }

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
    monkeypatch.setattr("app.runtime.engine.tool_service.execute", fake_execute)
    monkeypatch.setattr(agent_engine, "_TOOL_PROGRESS_INTERVAL_SECONDS", 0.01)

    session = build_session("sess_engine_tool_progress")
    events = asyncio.run(collect_stream(session, "run the slow tool"))

    assert any(item["type"] == "tool_progress" for item in events)
    assert any(item["type"] == "tool_finished" for item in events)
    assert any(item["type"] == "result" and item["payload"]["subtype"] == "success" for item in events)


def test_agent_engine_proactively_compacts_large_history(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    session = build_session("sess_engine_proactive")
    seed_verbose_history(session, turns=6)

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        yield {
            "choices": [
                {
                    "delta": {"content": "done after proactive compact"},
                    "finish_reason": "stop",
                }
            ]
        }

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
    monkeypatch.setattr(context_pipeline.budget, "get_context_window_for_model", lambda model, betas=None: 12000)
    monkeypatch.setattr(context_pipeline.budget, "get_effective_context_window", lambda model, max_output_tokens=None, betas=None: 10000)
    monkeypatch.setattr(context_pipeline.budget, "get_warning_threshold", lambda model, max_output_tokens=None, betas=None: 6000)
    monkeypatch.setattr(context_pipeline.budget, "get_error_threshold", lambda model, max_output_tokens=None, betas=None: 8000)
    monkeypatch.setattr(context_pipeline.budget, "get_blocking_limit", lambda model, max_output_tokens=None, betas=None: 12000)
    monkeypatch.setattr(context_pipeline.budget, "get_auto_compact_threshold", lambda model, max_output_tokens=None, betas=None: 7000)

    events = asyncio.run(collect_stream(session, "new task"))

    assert any(item["type"] == "context_compacted" for item in events)
    assert any(message.get("is_compact_summary") for message in session.messages) or any(
        item.get("compression_meta", {}).get("strategy") == "tool_result_truncate"
        for item in session.messages
        if item.get("role") == "tool"
    )
    assert session.context_state["compaction_count"] >= 1


def test_agent_engine_recovers_from_prompt_too_long(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    session = build_session("sess_engine_reactive")
    seed_verbose_history(session, turns=7)
    call_count = {"value": 0}

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        call_count["value"] += 1
        if call_count["value"] == 1:
            request = httpx.Request("POST", "https://example.invalid/chat/completions")
            response = httpx.Response(
                400,
                request=request,
                json={"error": {"message": "prompt is too long: 9000 tokens > 4096 maximum"}},
            )
            raise httpx.HTTPStatusError("prompt too long", request=request, response=response)
        yield {
            "choices": [
                {
                    "delta": {"content": "done after recovery"},
                    "finish_reason": "stop",
                }
            ]
        }

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])

    events = asyncio.run(collect_stream(session, "recover please"))

    assert call_count["value"] == 2
    assert any(item["type"] == "context_recovered" for item in events)
    assert any(item["type"] == "result" and item["payload"]["subtype"] == "success" for item in events)
    assert any(message.get("is_compact_summary") for message in session.messages)


def test_agent_engine_aborts_running_tool_and_allows_next_message(monkeypatch, tmp_path):
    initialize_store(tmp_path)

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        user_messages = [message for message in messages if message.get("role") == "user"]
        latest_user = str(user_messages[-1].get("content", "")) if user_messages else ""
        if latest_user == "stop me":
            yield {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_stop",
                                    "function": {
                                        "name": "sandbox_shell",
                                        "arguments": '{"command":"sleep 5","shell":"bash"}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
            return

        yield {
            "choices": [
                {
                    "delta": {"content": "second run ok"},
                    "finish_reason": "stop",
                }
            ]
        }

    tool_started = asyncio.Event()

    async def fake_execute(session, tool_name, arguments, *, run_id=None):
        tool_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            raise

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
    monkeypatch.setattr("app.runtime.engine.tool_service.execute", fake_execute)
    monkeypatch.setattr(agent_engine, "_TOOL_PROGRESS_INTERVAL_SECONDS", 0.01)

    session = build_session("sess_engine_abort_and_resume")

    async def run_flow():
        first_events: list[dict] = []

        async def consume_first():
            async for event in agent_engine.stream_chat(session, "stop me"):
                first_events.append(event.model_dump(mode="json"))

        task = asyncio.create_task(consume_first())
        await asyncio.wait_for(tool_started.wait(), timeout=1)
        aborted_run_id = session.request_abort()
        assert aborted_run_id is not None
        await asyncio.wait_for(task, timeout=1)

        second_events = await collect_stream(session, "next message")
        return first_events, second_events

    first_events, second_events = asyncio.run(run_flow())

    assert any(item["type"] == "aborted" for item in first_events)
    tool_finished = next(item for item in first_events if item["type"] == "tool_finished")
    assert tool_finished["payload"]["output"]["aborted"] is True
    assert session.current_run_id() is None

    result_event = next(item for item in second_events if item["type"] == "result")
    assert result_event["payload"]["subtype"] == "success"
    assert result_event["payload"]["result"] == "second run ok"
    assert all(item["type"] != "aborted" for item in second_events)


def test_agent_engine_persists_transcript_when_tool_requests_user_input(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    rounds = {"value": 0}

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        if rounds["value"] == 0:
            rounds["value"] += 1
            yield {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_need_input",
                                    "function": {
                                        "name": "request_user_input",
                                        "arguments": '{"title":"Need decision","questions":[]}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
            return

        yield {
            "choices": [
                {
                    "delta": {"content": "continued"},
                    "finish_reason": "stop",
                }
            ]
        }

    async def fake_execute(session, tool_name, arguments):
        return {
            "control": {
                "type": "await_user_input",
                "blocking": True,
                "request_id": "ask_1",
            },
            "summary": "waiting",
        }

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
    monkeypatch.setattr("app.runtime.engine.tool_service.execute", fake_execute)

    session = build_session("sess_engine_await_input")
    events = asyncio.run(collect_stream(session, "ask user"))

    completed = [item for item in events if item["type"] == "completed"]
    assert completed and completed[-1]["payload"]["subtype"] == "awaiting_user_input"
    assert session.transcript
    last_blocks = session.transcript[-1]["blocks"]
    assert any(block.get("kind") == "tool" for block in last_blocks)
    assert session.messages[-1].get("role") == "assistant"
    assert session.messages[-1].get("visible_in_transcript", True) is True


def test_agent_engine_returns_partial_answer_when_stream_interrupted_after_content(monkeypatch, tmp_path):
    initialize_store(tmp_path)

    call_count = 0

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield {
                "choices": [
                    {
                        "delta": {"content": "partial answer"},
                        "finish_reason": None,
                    }
                ]
            }
            raise httpx.RemoteProtocolError("incomplete chunked read")
        else:
            yield {
                "choices": [
                    {
                        "delta": {"content": "complete answer"},
                        "finish_reason": None,
                    }
                ]
            }
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    async def fake_sleep(_seconds):
        return

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    session = build_session("sess_engine_partial_stream")
    events = asyncio.run(collect_stream(session, "say something"))

    retry_events = [item for item in events if item["type"] == "stream_retry"]
    assert len(retry_events) == 1
    result_event = next(item for item in events if item["type"] == "result")
    assert result_event["payload"]["subtype"] == "success"
    assert result_event["payload"]["result"] == "complete answer"
    assert not any(item["type"] == "error" for item in events)


def test_agent_engine_raises_readable_llm_error_message(monkeypatch, tmp_path):
    initialize_store(tmp_path)

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        request = httpx.Request("POST", "http://models.ascend.huawei.com/v1/chat/completions")
        response = httpx.Response(
            400,
            request=request,
            json={"error": {"message": "invalid model: qwen3.6"}},
        )
        raise httpx.HTTPStatusError("bad request", request=request, response=response)
        if False:
            yield {}

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])

    session = build_session("sess_engine_llm_error")

    with pytest.raises(RuntimeError, match="LLM 服务报错: invalid model: qwen3.6"):
        asyncio.run(collect_stream(session, "hello"))


def test_agent_engine_recovers_from_length_truncation_reasoning_only(monkeypatch, tmp_path):
    """回归: finish_reason=length 且只有 reasoning 没有 visible content 时,
    不报 error_empty_response,而是持久化已有内容后继续,最终成功产出回答。
    """
    initialize_store(tmp_path)

    call_count = 0

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield {
                "choices": [
                    {"delta": {"reasoning_content": "让我思考一下这个问题..."}, "finish_reason": None}
                ]
            }
            yield {"choices": [{"delta": {}, "finish_reason": "length"}]}
        else:
            yield {
                "choices": [
                    {"delta": {"content": "这是最终回答。"}, "finish_reason": None}
                ]
            }
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])

    session = build_session("sess_engine_truncation_reasoning")
    events = asyncio.run(collect_stream(session, "hello"))

    result_event = next(item for item in events if item["type"] == "result")
    assert result_event["payload"]["subtype"] == "success"
    assert result_event["payload"]["result"] == "这是最终回答。"
    assert call_count == 2


def test_agent_engine_retries_on_transport_error(monkeypatch, tmp_path):
    """回归: 流式传输中 TransportError 时自动重连,第二次成功产出完整回答。"""
    initialize_store(tmp_path)

    call_count = 0

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield {"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]}
            raise httpx.RemoteProtocolError("connection reset")
        else:
            yield {"choices": [{"delta": {"content": "complete answer"}, "finish_reason": None}]}
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    async def fake_sleep(_seconds):
        return

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    session = build_session("sess_engine_transport_retry")
    events = asyncio.run(collect_stream(session, "hello"))

    retry_events = [item for item in events if item["type"] == "stream_retry"]
    assert len(retry_events) == 1
    result_event = next(item for item in events if item["type"] == "result")
    assert result_event["payload"]["subtype"] == "success"
    assert result_event["payload"]["result"] == "complete answer"
    assert call_count == 2


def test_agent_engine_recovers_from_length_truncation_partial_content(monkeypatch, tmp_path):
    """回归: finish_reason=length 且有部分 visible content 时,持久化部分内容后继续完成。
    """
    initialize_store(tmp_path)

    call_count = 0

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield {
                "choices": [
                    {"delta": {"content": "这是一个很长的回答的开头部分..."}, "finish_reason": None}
                ]
            }
            yield {"choices": [{"delta": {}, "finish_reason": "length"}]}
        else:
            yield {
                "choices": [
                    {"delta": {"content": "这是回答的结尾。"}, "finish_reason": None}
                ]
            }
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])

    session = build_session("sess_engine_truncation_partial")
    events = asyncio.run(collect_stream(session, "hello"))

    result_event = next(item for item in events if item["type"] == "result")
    assert result_event["payload"]["subtype"] == "success"
    assert call_count == 2
