# backend/tests/test_agent_engine.py
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from app.core.config import settings
from app.runtime.engine import agent_engine
from app.services.session_service import AgentSession


async def collect_stream(session: AgentSession, message: str) -> list[dict]:
    events: list[dict] = []
    async for event in agent_engine.stream_chat(session, message):
        events.append(event.model_dump(mode="json"))
    return events


def test_agent_engine_returns_model_content_without_hardcoded_fallback(monkeypatch):
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


def test_agent_engine_does_not_interrupt_long_run_when_stall_guard_disabled(monkeypatch):
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
