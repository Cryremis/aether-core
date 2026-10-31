"""预算状态必须实际进入模型请求，查询与压缩策略也走真实生产链路。"""
from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace

import pytest

from app.services.context.message_adapter import context_message_adapter
from app.services.context.runtime_types import ContextOverflowError
from app.services.session_types import AgentSession
from app.services.tool_service import tool_service
from app.services.transcript_service import transcript_service
from test_agent_engine import build_session, collect_stream, initialize_store
from test_context_compaction import FakeLlmRuntime, make_pipeline
from test_context_reliability_audit import chat_frame, setup_client, wire
from test_llm_client import FakeAsyncClient, make_runtime_config


def report_from_text(text):
    return json.loads(next(line for line in text.splitlines() if line.startswith('{"available"')))


def test_chat_warning_has_numeric_budget_and_counts_its_own_overhead():
    pipeline = make_pipeline()
    session = AgentSession("numeric", messages=[{"role": "user", "content": "a" * 12000, "turn_index": 1}])
    original = copy.deepcopy(session.messages)
    prepared = pipeline.prepare_for_llm(session=session, llm_runtime=FakeLlmRuntime(),
        messages=[{"role": "system", "content": "root"}, *session.messages], turn_index=1, max_output_tokens=1000,
        tools=[tool_service.get_context_compact_schema()])
    report = report_from_text(prepared.messages[-1]["content"])
    assert report["used_percent"] >= 75
    assert report["remaining_input_tokens"] == report["input_budget_tokens"] - report["estimated_input_tokens"]
    assert report["tokens_left"] == report["auto_compact_at_tokens"] - report["estimated_input_tokens"]
    assert report["output_reserved_tokens"] == 1000
    assert report["safety_reserved_tokens"] == 4000
    assert report["context_compact_available"] is True
    assert report["snapshot_basis"] == "before_this_notice"
    assert prepared.state.last_known_token_estimate > report["estimated_input_tokens"]
    assert prepared.events[0].payload["estimated_tokens"] == prepared.state.last_known_token_estimate
    assert session.messages[0]["content"] == original[0]["content"]
    assert not any(message.get("kind") == "context_budget" for message in session.messages)


@pytest.mark.parametrize("protocol", ["openai-responses", "anthropic-messages"])
def test_native_warning_is_append_only_and_not_repeated_without_growth(protocol):
    pipeline = make_pipeline()
    config = replace(make_runtime_config(), api_format=protocol, sampling={"context_window": 20000, "max_output_tokens": 2000})
    session = AgentSession("native_notice", messages=[context_message_adapter.make_user_message("a" * 34000, turn_index=1)])
    def prepare():
        return pipeline.prepare_for_llm(session=session, llm_runtime=config, messages=[{"role": "system", "content": "root"}, *session.messages], turn_index=1)
    first = prepare()
    notice = copy.deepcopy(session.messages[-1])
    assert notice["kind"] == "context_budget" and notice["role"] == "user"
    assert notice["visible_in_transcript"] is False
    assert first.messages[-1]["content"] == notice["content"]
    second = prepare()
    assert session.messages[-1] == notice
    assert len([message for message in session.messages if message.get("kind") == "context_budget"]) == 1
    assert second.messages == first.messages
    assert not any("[runtime context]" in str(message) for message in transcript_service.build_session_transcript(session))
    session.messages.append(context_message_adapter.make_user_message("b" * 2000, turn_index=2))
    prepare()
    reminders = [message for message in session.messages if message.get("kind") == "context_budget"]
    assert len(reminders) == 2
    assert reminders[0] == notice
    assert report_from_text(reminders[1]["content"])["used_percent"] > report_from_text(notice["content"])["used_percent"]


@pytest.mark.parametrize("protocol", ["openai-responses", "anthropic-messages"])
def test_native_notice_crossing_hard_limit_does_not_commit_a_partial_history(protocol):
    pipeline = make_pipeline()
    config = replace(make_runtime_config(), api_format=protocol, sampling={"context_window": 10000, "max_output_tokens": 1000})
    session = AgentSession("blocked_notice", messages=[context_message_adapter.make_user_message("a" * 14000, turn_index=1)])
    original = copy.deepcopy(session.messages)
    assert pipeline._estimate_request(session.messages, [], runtime=config) < 4750
    with pytest.raises(ContextOverflowError):
        pipeline.prepare_for_llm(session=session, llm_runtime=config, messages=session.messages, turn_index=1, allow_compaction=False)
    assert session.messages == original


def test_warning_does_not_offer_tools_outside_the_current_allowlist():
    pipeline = make_pipeline()
    session = AgentSession("unavailable", messages=[{"role": "user", "content": "a" * 12000}])
    prepared = pipeline.prepare_for_llm(session=session, llm_runtime=FakeLlmRuntime(), messages=session.messages, turn_index=1, max_output_tokens=1000, tools=[])
    text = prepared.messages[-1]["content"]
    assert report_from_text(text)["context_compact_available"] is False
    assert "可调用 context_compact" not in text
    assert "可用 get_context_remaining" not in text


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses", "anthropic-messages"])
def test_engine_budget_query_uses_current_history_and_preserves_native_prefix(monkeypatch, tmp_path, protocol):
    initialize_store(tmp_path)
    config = replace(make_runtime_config(), api_format=protocol, sampling={"context_window": 20000, "max_output_tokens": 2000})
    schemas = tool_service.list_tool_schemas(AgentSession("catalog"))
    setup_client(monkeypatch, config)
    monkeypatch.setattr(tool_service, "list_tool_schemas", lambda session: [schema for schema in schemas if schema["function"]["name"] == "get_context_remaining"])
    query = tool_service._handle_get_context_remaining
    async def check_query(session, arguments):
        previous_state = copy.deepcopy(session.context_state)
        result = await query(session, arguments)
        assert session.context_state == previous_state
        return result
    monkeypatch.setitem(tool_service._registry._handlers, "get_context_remaining", check_query)
    if protocol == "openai-compatible":
        first = wire(chat_frame(calls=[("budget", "get_context_remaining", {})], finish="tool_calls"))
        final = wire(chat_frame("完成"))
    elif protocol == "openai-responses":
        first = wire({"type": "response.completed", "response": {"output": [
            {"type": "reasoning", "id": "r", "encrypted_content": "opaque"},
            {"type": "function_call", "id": "fc", "call_id": "budget", "name": "get_context_remaining", "arguments": "{}"}]}})
        final = wire({"type": "response.completed", "response": {"output": [{"type": "message", "id": "m", "content": [{"type": "output_text", "text": "完成"}]}]}})
    else:
        first = wire({"type": "message_start", "message": {}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": "signed"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "budget", "name": "get_context_remaining", "input": {}}},
            {"type": "content_block_stop", "index": 1}, {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}, {"type": "message_stop"})
        final = wire({"type": "message_start", "message": {}}, {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "完成"}},
            {"type": "content_block_stop", "index": 0}, {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}, {"type": "message_stop"})
    FakeAsyncClient.stream_responses = [first, final]
    session = build_session("budget_wire_" + protocol)
    events = asyncio.run(collect_stream(session, "a" * 34000))
    assert events[-1]["payload"]["subtype"] == "success"
    assert len(FakeAsyncClient.stream_payloads) == 2
    first_request, second_request = FakeAsyncClient.stream_payloads
    assert "used_percent" in json.dumps(first_request)
    result = next(event["payload"]["result"]["data"] for event in events if event["type"] == "tool_finished")
    assert "used_percent:" in next(message["content"] for message in session.messages if message.get("role") == "tool")
    assert result["snapshot_basis"] == "current_history_before_this_tool_result"
    assert result["used_percent"] >= 75
    assert result["context_compact_available"] is True
    assert result["tokens_left"] <= result["remaining_input_tokens"]
    if protocol != "openai-compatible":
        field = "input" if protocol == "openai-responses" else "messages"
        assert second_request[field][:len(first_request[field])] == first_request[field]
        assert session.context_epoch == 0
    assert not any("[runtime context]" in str(message) for message in transcript_service.build_session_transcript(session))


def test_query_before_first_request_is_unknown_and_has_no_external_calls(monkeypatch):
    monkeypatch.setattr(tool_service, "_resolve_runtime_config", lambda _: (_ for _ in ()).throw(AssertionError("not needed")))
    result = asyncio.run(tool_service._handle_get_context_remaining(AgentSession("empty"), {}))
    assert result.data == {"available": False, "tokens_left": None}


def test_aggressive_compaction_releases_more_old_tool_views_than_balanced():
    pipeline = make_pipeline()
    config = replace(make_runtime_config(), sampling={"context_window": 100000, "max_output_tokens": 4096})
    original = []
    for turn in range(1, 9):
        original.extend([context_message_adapter.make_user_message("task " + str(turn), turn_index=turn),
            context_message_adapter.make_assistant_message(turn_index=turn, tool_calls=[{"id": str(turn), "type": "function", "function": {"name": "read", "arguments": "{}"}}]),
            context_message_adapter.make_tool_message(tool_call_id=str(turn), content="x" * 12000, tool_name="read", turn_index=turn)])
    def compact(aggressive):
        session = AgentSession("mode_" + str(aggressive), messages=copy.deepcopy(original))
        return asyncio.run(pipeline.compact_session_async(session=session, llm_runtime=config, messages=session.messages, turn_index=8, aggressive=aggressive)), session
    balanced, balanced_session = compact(False)
    aggressive, aggressive_session = compact(True)
    assert aggressive.plan.messages_affected > balanced.plan.messages_affected
    assert sum(len(message.get("content") or "") for message in aggressive_session.messages) < sum(len(message.get("content") or "") for message in balanced_session.messages)
    assert balanced_session.messages[-1]["content"] == aggressive_session.messages[-1]["content"] == "x" * 12000


def test_budget_tool_schema_obeys_session_allowlist(monkeypatch):
    monkeypatch.setattr(tool_service, "_resolve_runtime_config", lambda session: make_runtime_config())
    assert any(schema["function"]["name"] == "get_context_remaining" for schema in tool_service.list_tool_schemas(AgentSession("allowed")))
    assert not any(schema["function"]["name"] == "get_context_remaining" for schema in tool_service.list_tool_schemas(AgentSession("denied", allowed_tools=["read"])))
