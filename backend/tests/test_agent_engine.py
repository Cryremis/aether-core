# backend/tests/test_agent_engine.py
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx

from app.core.config import settings
from app.runtime.engine import agent_engine
from app.services.context.context_pipeline import context_pipeline
from app.services.session_types import AgentSession
from app.services.store import store_service


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


def test_agent_engine_returns_model_content_without_hardcoded_fallback(monkeypatch, tmp_path):
    initialize_store(tmp_path)

    async def fake_stream_chat_completion(config, messages, tools) -> AsyncGenerator[dict, None]:
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

    session = AgentSession(session_id="sess_engine_success")
    events = asyncio.run(collect_stream(session, "reply directly"))

    result_events = [item for item in events if item["type"] == "result"]
    assert len(result_events) == 1
    assert result_events[0]["payload"]["subtype"] == "success"
    assert result_events[0]["payload"]["result"] == "this is the real model answer"
    assert session.messages[-1]["content"] == "this is the real model answer"
    assert session.messages[-1]["blocks"][-1]["kind"] == "content"
    event_types = [item["type"] for item in events]
    assert "workboard_snapshot" in event_types
    assert "elicitation_snapshot" in event_types


def test_agent_engine_injects_runtime_state_context(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    observed_messages: list[list[dict]] = []

    async def fake_stream_chat_completion(config, messages, tools) -> AsyncGenerator[dict, None]:
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

    session = AgentSession(session_id="sess_engine_runtime_state")
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

    async def fake_stream_chat_completion(config, messages, tools) -> AsyncGenerator[dict, None]:
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

    session = AgentSession(
        session_id="sess_engine_prompt_layers",
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

    async def fake_stream_chat_completion(config, messages, tools) -> AsyncGenerator[dict, None]:
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

    session = AgentSession(session_id="sess_engine_long_run")
    events = asyncio.run(collect_stream(session, "continue long task"))

    result_events = [item for item in events if item["type"] == "result"]
    assert len(result_events) == 1
    assert result_events[0]["payload"]["subtype"] == "success"
    assert result_events[0]["payload"]["result"] == "finished after repeated tool calls"
    assert all(item["payload"].get("subtype") != "error_stalled" for item in result_events)
    assert any(block["kind"] == "tool" for block in session.messages[-1]["blocks"])
    assert any(message.get("tool_calls") for message in session.messages if message.get("role") == "assistant")
    assert any(message.get("role") == "tool" for message in session.messages)


def test_agent_engine_injects_skill_content_after_invoke_skill(monkeypatch, tmp_path):
    initialize_store(tmp_path)

    observed_messages: list[list[dict]] = []
    rounds = {"value": 0}

    async def fake_stream_chat_completion(config, messages, tools) -> AsyncGenerator[dict, None]:
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

    session = AgentSession(session_id="sess_engine_skill")
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

    async def fake_stream_chat_completion(config, messages, tools) -> AsyncGenerator[dict, None]:
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

    session = AgentSession(session_id="sess_engine_runtime_event")
    events = asyncio.run(collect_stream(session, "repair runtime"))
    event_types = [item["type"] for item in events]
    assert "runtime_recreated" in event_types
    assert "tool_finished" in event_types
    assert event_types.index("runtime_recreated") < event_types.index("tool_finished")


def test_agent_engine_emits_tool_progress_for_long_running_tools(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    rounds = {"value": 0}

    async def fake_stream_chat_completion(config, messages, tools) -> AsyncGenerator[dict, None]:
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

    session = AgentSession(session_id="sess_engine_tool_progress")
    events = asyncio.run(collect_stream(session, "run the slow tool"))

    assert any(item["type"] == "tool_progress" for item in events)
    assert any(item["type"] == "tool_finished" for item in events)
    assert any(item["type"] == "result" and item["payload"]["subtype"] == "success" for item in events)


def test_agent_engine_proactively_compacts_large_history(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    session = AgentSession(session_id="sess_engine_proactive")
    seed_verbose_history(session, turns=6)

    async def fake_stream_chat_completion(config, messages, tools) -> AsyncGenerator[dict, None]:
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
    session = AgentSession(session_id="sess_engine_reactive")
    seed_verbose_history(session, turns=7)
    call_count = {"value": 0}

    async def fake_stream_chat_completion(config, messages, tools) -> AsyncGenerator[dict, None]:
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

    async def fake_stream_chat_completion(config, messages, tools) -> AsyncGenerator[dict, None]:
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

    session = AgentSession(session_id="sess_engine_abort_and_resume")

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
