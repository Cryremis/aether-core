# backend/tests/test_agent_engine.py
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

from app.core.config import settings
from app.runtime.engine import agent_engine
from app.services.session_service import AgentSession
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


def test_agent_engine_returns_model_content_without_hardcoded_fallback(monkeypatch, tmp_path):
    initialize_store(tmp_path)

    async def fake_stream_chat_completion(messages, tools) -> AsyncGenerator[dict, None]:
        yield {
            "choices": [
                {
                    "delta": {"content": "这是模型真实返回的最终答案"},
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
    events = asyncio.run(collect_stream(session, "请直接回答"))

    result_events = [item for item in events if item["type"] == "result"]
    assert len(result_events) == 1
    assert result_events[0]["payload"]["subtype"] == "success"
    assert result_events[0]["payload"]["result"] == "这是模型真实返回的最终答案"
    assert session.messages[-1]["content"] == "这是模型真实返回的最终答案"
    assert session.messages[-1]["blocks"][-1]["kind"] == "content"


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
                    "delta": {"content": "重复工具调用后仍然继续完成任务"},
                    "finish_reason": "stop",
                }
            ]
        },
    ]
    round_index = {"value": 0}

    async def fake_stream_chat_completion(messages, tools) -> AsyncGenerator[dict, None]:
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
    events = asyncio.run(collect_stream(session, "继续长程任务"))

    result_events = [item for item in events if item["type"] == "result"]
    assert len(result_events) == 1
    assert result_events[0]["payload"]["subtype"] == "success"
    assert result_events[0]["payload"]["result"] == "重复工具调用后仍然继续完成任务"
    assert all(item["payload"].get("subtype") != "error_stalled" for item in result_events)
    assert any(block["kind"] == "tool" for block in session.messages[-1]["blocks"])


def test_agent_engine_injects_skill_content_after_invoke_skill(monkeypatch, tmp_path):
    initialize_store(tmp_path)

    observed_messages: list[list[dict]] = []
    rounds = {"value": 0}

    async def fake_stream_chat_completion(messages, tools) -> AsyncGenerator[dict, None]:
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
                    "delta": {"content": "已按技能要求完成分析"},
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
                    "content": '<aether_skill name="data-analysis" source="built_in">技能正文</aether_skill>',
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
    events = asyncio.run(collect_stream(session, "帮我做数据分析"))

    assert len(observed_messages) == 2
    assert any(
        message.get("role") == "user" and "aether_skill" in str(message.get("content", ""))
        for message in observed_messages[1]
    )
    result_events = [item for item in events if item["type"] == "result"]
    assert len(result_events) == 1
    assert result_events[0]["payload"]["subtype"] == "success"
    assert result_events[0]["payload"]["result"] == "已按技能要求完成分析"
