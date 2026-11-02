from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.core.model_errors import ModelStreamError, public_run_error_code
from app.services.tool_display_service import tool_display_service as display
from app.services.tool_result_service import ToolExecutionResult
from app.services.transcript_service import transcript_service
from test_agent_engine import initialize_store, build_session, collect_stream


def tool_schema(name="host_query"):
    return {"type": "function", "function": {"name": name, "description": "查询任务",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"], "additionalProperties": False}}}


def test_note_schema_is_optional_and_does_not_mutate_host_contract():
    source = tool_schema()
    projected = display.annotate_schemas([source])[0]
    assert display.note_key not in source["function"]["parameters"]["properties"]
    assert projected["function"]["parameters"]["required"] == ["query"]
    args, note = display.split_arguments("host_query", {"query": "a", display.note_key: " 查找\n任务 "}, [source])
    assert args == {"query": "a"}
    assert note == "查找 任务"
    source["function"]["parameters"]["properties"][display.note_key] = {"type": "integer"}
    assert display.annotate_schemas([source]) == [source]
    assert display.split_arguments("host_query", {display.note_key: 12}, [source]) == ({display.note_key: 12}, "")


@pytest.mark.parametrize("value", [None, 12, {}, "  "])
def test_invalid_optional_note_never_breaks_execution(value):
    args, note = display.split_arguments("host_query", {"query": "a", display.note_key: value}, [tool_schema()])
    assert args == {"query": "a"}
    assert display.resolve("host_query", args, note=note)["title"] == "host_query"
    assert display.resolve("sandbox_shell", {"command": "echo hello"})["title"] == "sandbox_shell"


def test_note_survives_live_and_persisted_views_and_tool_failure_recovers(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    calls = []
    round_index = 0

    async def stream(config, messages, tools, **kwargs):
        nonlocal round_index
        schema = next(t for t in tools if t["function"]["name"] == "host_query")
        assert display.note_key in schema["function"]["parameters"]["properties"]
        if round_index < 2:
            if round_index:
                assert any(m.get("role") == "tool" and "修改查询" in str(m.get("content")) for m in messages)
            args = {"query": str(round_index), display.note_key: "查询训练任务"}
            yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": f"note_{round_index}",
                "function": {"name": "host_query", "arguments": json.dumps(args)}}]}, "finish_reason": "tool_calls"}]}
        else:
            yield {"choices": [{"delta": {"content": "查询完成"}, "finish_reason": "stop"}]}
        round_index += 1

    async def execute(session, name, arguments, **kwargs):
        assert display.note_key not in arguments
        calls.append(arguments)
        if len(calls) == 1:
            return ToolExecutionResult.failure("query", "修改查询", code="INVALID_QUERY", message="修改查询后继续")
        return ToolExecutionResult.success("query", "完成", {"found": True})

    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [tool_schema()])
    monkeypatch.setattr("app.runtime.engine.tool_service._execute_with_timeout", execute)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", stream)
    session = build_session("notes_and_recovery")
    events = asyncio.run(collect_stream(session, "查训练任务"))
    assert calls == [{"query": "0"}, {"query": "1"}]
    assert events[-1]["payload"]["subtype"] == "success"
    started = [e for e in events if e["type"] == "tool_started"]
    assert all(e["payload"]["tool_display"]["title"] == "查询训练任务" for e in started)
    assert all(display.note_key not in e["payload"]["input"] for e in started)
    transcript = transcript_service.build_persisted_transcript(session.messages)
    blocks = [b for m in transcript for b in m.get("blocks", []) if b["kind"] == "tool"]
    assert blocks and all(b["title"] == "查询训练任务" and b["toolName"] == "host_query" for b in blocks)


@pytest.mark.parametrize("status,code", [(401,"MODEL_ACCESS_DENIED"),(429,"MODEL_RATE_LIMITED"),(503,"MODEL_UNAVAILABLE"),(422,"MODEL_CONFIGURATION_ERROR")])
def test_terminal_error_codes_do_not_expose_provider_diagnostics(status, code):
    exc = httpx.HTTPStatusError("secret parser details", request=httpx.Request("POST", "https://example.invalid"), response=httpx.Response(status))
    assert public_run_error_code(exc) == code
    assert public_run_error_code(ModelStreamError("private")) == "MODEL_RESPONSE_INVALID"


def test_strict_schema_keeps_validation_with_nullable_note():
    source = tool_schema()
    source["function"]["strict"] = True
    projected = display.annotate_schemas([source])[0]["function"]
    assert projected["strict"] is True
    assert projected["parameters"]["properties"][display.note_key]["type"] == ["string", "null"]
    assert projected["parameters"]["required"] == ["query", display.note_key]


def test_note_does_not_defeat_repeated_call_detection():
    from app.runtime.engine import agent_engine
    def call(note):
        return {0: {"name": "host_query", "arguments": json.dumps({"query": "same", display.note_key: note})}}
    assert agent_engine._fingerprint_tool_calls(call("a"), [tool_schema()]) == agent_engine._fingerprint_tool_calls(call("b"), [tool_schema()])
