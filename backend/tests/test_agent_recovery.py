"""现网恢复回归：可兼容差异不能中断任务，恢复不能重复执行工具。"""
from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace

import httpx
import pytest

from app.core.model_errors import ModelStreamError, public_run_error
from app.core.config import settings
from app.runtime.engine import agent_engine
from app.services.context.history_store import history_store
from app.services.context.message_adapter import context_message_adapter
from app.services.context.summarizer import compaction_summarizer
from app.services.llm_client import LlmClient
from app.services.session_types import AgentSession
from app.services.tool_service import tool_service
from app.services.transcript_service import transcript_service
from test_agent_engine import build_session, collect_stream, initialize_store
from test_context_compaction import make_pipeline
from test_context_reliability_audit import chat_frame, setup_client, wire
from test_llm_client import FakeAsyncClient, FakeResponse, make_runtime_config


@pytest.fixture
def fast_recovery(monkeypatch):
    sleep = asyncio.sleep
    async def yield_once(delay):
        await sleep(0)
    monkeypatch.setattr(asyncio, "sleep", yield_once)


@pytest.mark.usefixtures("fast_recovery")
def test_ten_turns_of_missing_reasoning_keep_tools_running_and_replay_empty_fields(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    config = replace(make_runtime_config(), model="deepseek-v4.1-flash")
    setup_client(monkeypatch, config)
    FakeAsyncClient.stream_responses = [frame for turn in range(10) for frame in (
        wire(chat_frame(calls=[("shared-call-id", "conversation_read", {})], finish="tool_calls")), wire(chat_frame(f"完成 {turn}")))]
    session = build_session("missing_thought_ten_turns")
    async def run():
        return [await collect_stream(session, f"task {turn}") for turn in range(10)]
    turns = asyncio.run(run())
    assert all(turn[-1]["payload"]["subtype"] == "success" for turn in turns)
    assert sum(event["type"] == "tool_finished" for turn in turns for event in turn) == 10
    assert session.context_epoch == 0
    for request in FakeAsyncClient.stream_payloads:
        assistants = [message for message in request["messages"] if message["role"] == "assistant"]
        assert all(message["reasoning_content"] == "" for message in assistants)
        calls = [call["id"] for message in assistants for call in message.get("tool_calls", [])]
        assert len(calls) == len(set(calls))


@pytest.mark.parametrize("reasoning", [None, "", "  原样思考\n"])
def test_reasoning_projection_is_lossless_and_does_not_mutate_archive(reasoning):
    config = replace(make_runtime_config(), model="deepseek-flash", sampling={"reasoning_replay": "disabled", "reasoning_field": "reasoning"})
    message = {"role": "assistant", "content": "body"}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    original = copy.deepcopy(message)
    request = LlmClient()._payload(config, [message], [{"type": "function", "function": {"name": "noop", "parameters": {"type": "object"}}}], stream=True)
    assert request["messages"][0]["reasoning_content"] == (reasoning if reasoning is not None else "")
    assert message == original


@pytest.mark.usefixtures("fast_recovery")
def test_empty_response_is_retried_before_user_failure(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    FakeAsyncClient.stream_responses = [wire(chat_frame()), wire(chat_frame("recovered"))]
    events = asyncio.run(collect_stream(build_session("empty_recovered"), "task"))
    assert events[-1]["payload"]["subtype"] == "success"
    assert len(FakeAsyncClient.stream_payloads) == 2
    assert not any(event["type"] == "error" for event in events)


@pytest.mark.usefixtures("fast_recovery")
def test_broken_stream_continues_without_reexecuting_finished_tool(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    setup_client(monkeypatch, replace(make_runtime_config(), model="deepseek-flash"))
    FakeAsyncClient.stream_responses = [wire(chat_frame(calls=[("read", "conversation_read", {})], finish="tool_calls")),
        wire(chat_frame("partial 192.0.2.10", finish=None), {"error": {"message": "internal parser failure"}}), wire(chat_frame("continued"))]
    session = build_session("continue_after_tool")
    events = asyncio.run(collect_stream(session, "task"))
    assert events[-1]["payload"]["subtype"] == "success"
    assert len([event for event in events if event["type"] == "tool_finished"]) == 1
    assert any(message.get("content") == "partial 192.0.2.10" for message in FakeAsyncClient.stream_payloads[2]["messages"])
    assert "internal parser failure" not in json.dumps(events)
    texts = [block["content"] for message in transcript_service.build_session_transcript(session) for block in message.get("blocks", []) if block["kind"] == "content"]
    assert texts == ["partial 192.0.2.10", "continued"]


@pytest.mark.usefixtures("fast_recovery")
def test_incomplete_tool_parameters_are_regenerated_without_execution(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    partial = chat_frame(calls=[("partial", "conversation_read", {})], finish="length")
    partial["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"] = '{"query":'
    FakeAsyncClient.stream_responses = [wire(partial), wire(chat_frame(calls=[("valid", "conversation_read", {})], finish="tool_calls")), wire(chat_frame("done"))]
    session = build_session("partial_proposal")
    events = asyncio.run(collect_stream(session, "task"))
    assert events[-1]["payload"]["subtype"] == "success"
    assert [event["payload"]["id"] for event in events if event["type"] == "tool_started"] == ["valid"]
    assert not any(message.get("role") == "assistant" and not message.get("content") and not message.get("tool_calls") for message in session.messages)


@pytest.mark.parametrize("parameter", ["stream_options", "reasoning_effort", "temperature"])
def test_explicitly_rejected_optional_parameter_is_negotiated_without_dropping_tools(monkeypatch, tmp_path, parameter):
    initialize_store(tmp_path)
    config = replace(make_runtime_config(), sampling={"reasoning_effort": "high", "temperature": 0.5})
    setup_client(monkeypatch, config)
    FakeAsyncClient.stream_responses = [FakeResponse(status_code=400, json_data={"error": {"message": f"Unknown parameter: {parameter}"}}), wire(chat_frame("recovered"))]
    events = asyncio.run(collect_stream(build_session("optional_" + parameter), "task"))
    assert events[-1]["payload"]["subtype"] == "success"
    first, second = FakeAsyncClient.stream_payloads
    assert parameter in first and parameter not in second
    assert first["tools"] == second["tools"]
    assert first["messages"] == second["messages"]
    assert first["model"] == second["model"]


@pytest.mark.usefixtures("fast_recovery")
def test_each_successful_tool_round_gets_a_fresh_transient_retry_budget(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    FakeAsyncClient.stream_responses = [frame for turn in range(4) for frame in (
        wire({"error": {"message": "temporary error"}}), wire(chat_frame(calls=[(f"read_{turn}", "conversation_read", {})], finish="tool_calls")))] + [wire(chat_frame("done"))]
    events = asyncio.run(collect_stream(build_session("long_task_retries"), "task"))
    assert events[-1]["payload"]["subtype"] == "success"
    assert len([event for event in events if event["type"] == "tool_finished"]) == 4
    assert [event["payload"]["attempt"] for event in events if event["type"] == "stream_retry"] == [1, 1, 1, 1]


@pytest.mark.usefixtures("fast_recovery")
def test_protocol_recovery_is_bounded_when_external_service_never_recovers(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "agent_model_retry_attempts", 3)
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    FakeAsyncClient.stream_responses = [wire({"error": {"message": "permanent bad stream"}}) for _ in range(4)]
    with pytest.raises(ModelStreamError):
        asyncio.run(collect_stream(build_session("bounded_protocol"), "task"))
    assert len(FakeAsyncClient.stream_payloads) == 4


def test_error_sse_redacts_even_legacy_raw_errors():
    from app.api.routes.agent import _sse
    from app.schemas.agent import AgentEvent
    output = _sse(AgentEvent(type="error", session_id="own", run_id="run", payload={"message": "reasoning_content vLLM parser", "traceback": "Traceback SECRET"}))
    assert public_run_error() in output
    assert not any(word in output for word in ("vLLM", "reasoning_content", "Traceback", "SECRET"))


def test_external_auth_failure_is_not_retried_and_has_only_friendly_public_error(monkeypatch, tmp_path):
    from app.services.agent_run_service import AgentRunService
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    FakeAsyncClient.stream_responses = [FakeResponse(status_code=401, json_data={"error": {"message": "SECRET auth backend"}})]
    service, session = AgentRunService(), build_session("external_auth")
    async def run():
        run_id = await service.start_chat_run(session, "task")
        await service.wait_for_run(run_id)
        return run_id
    run_id = asyncio.run(run())
    events = service.list_events_after(run_id, 0)
    error = next(event for event in events if event.type == "error")
    assert error.payload["message"] == public_run_error()
    assert next(event for event in events if event.type == "completed").payload["error_code"] == "MODEL_ACCESS_DENIED"
    assert "SECRET" not in json.dumps([event.model_dump(mode="json") for event in events])
    assert "traceback" not in error.payload
    assert len(FakeAsyncClient.stream_payloads) == 1


@pytest.mark.usefixtures("fast_recovery")
@pytest.mark.parametrize("failure", ["empty", "transient"])
def test_summary_recovers_empty_or_transient_response_within_call_budget(monkeypatch, failure):
    FakeAsyncClient.reset()
    monkeypatch.setattr("app.services.llm_client.httpx.AsyncClient", FakeAsyncClient)
    invalid = FakeResponse(json_data={"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}) if failure == "empty" else FakeResponse(status_code=503)
    valid = "【目标】继续任务\n【约束与事实】原样保留\n【已完成与证据】已检查\n【待办与疑问】继续"
    FakeAsyncClient.post_responses = [invalid, FakeResponse(json_data={"choices": [{"message": {"content": valid}, "finish_reason": "stop"}]})]
    result = asyncio.run(compaction_summarizer.summarize([{"role": "user", "content": "task"}], make_runtime_config()))
    assert result == valid
    assert len(FakeAsyncClient.post_payloads) == 2


def test_missing_native_compact_endpoint_falls_back_to_client_checkpoint(monkeypatch, tmp_path, checkpoint_model):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    config = replace(make_runtime_config(), api_format="openai-responses", sampling={"native_compaction": True, "context_window": 100000, "max_output_tokens": 2000})
    FakeAsyncClient.post_responses = [FakeResponse(status_code=404)]
    session = AgentSession("native_fallback", messages=[context_message_adapter.make_user_message("old data " * 2000, turn_index=i) for i in range(10)])
    pipeline = make_pipeline()
    prepared = asyncio.run(pipeline.compact_session_async(session=session, llm_runtime=config, messages=session.messages, turn_index=10))
    assert prepared.compacted
    assert prepared.state.last_known_token_estimate < prepared.plan.estimated_tokens_before
    assert len(FakeAsyncClient.post_payloads) == 1
    assert session.context_epoch == 1


def test_manual_compact_large_session_without_model_and_can_continue(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    config = replace(make_runtime_config(), model="deepseek-v4.1-flash",
                     sampling={"context_window": 409600, "max_output_tokens": 32000})
    setup_client(monkeypatch, config)
    monkeypatch.setattr(settings, "context_compaction_model_enabled", False)
    session = build_session("large_compaction")
    for index in range(386):
        item = (context_message_adapter.make_user_message("旧任务 " + "数据" * 296, turn_index=index + 1)
                if index % 2 == 0 else
                context_message_adapter.make_assistant_message(content="旧答复 " + "证据" * 296, turn_index=index + 1))
        session.messages.append(item)
    oldest = session.messages[0]["message_id"]
    latest = session.messages[-2]["content"]
    FakeAsyncClient.stream_responses = [wire(chat_frame("压缩后继续"))]

    async def run():
        compacted = await collect_stream(session, "/compact")
        repeated = await collect_stream(session, "/compact")
        continued = await collect_stream(session, "继续任务")
        return compacted, repeated, continued

    compacted, repeated, continued = asyncio.run(run())
    event = next(event["payload"] for event in compacted if event["type"] == "context_compacted")
    deltas = [item["payload"]["delta"] for item in compacted if item["type"] == "content_delta"]
    assert len(deltas) > 1
    assert "".join(deltas).startswith("上下文已压缩。")
    assert event["tokens_before"] > 300_000
    assert event["tokens_after"] < 30_000
    assert not any(event["type"] == "context_compacted" for event in repeated)
    assert not FakeAsyncClient.post_payloads
    assert continued[-1]["payload"]["subtype"] == "success"
    assert latest in [item["content"] for item in FakeAsyncClient.stream_payloads[0]["messages"]]
    assert not any("上下文已压缩。" in str(item.get("content")) for item in FakeAsyncClient.stream_payloads[0]["messages"])
    assert history_store.read(session.session_id, message_id=oldest)["items"]
    from app.services.session_service import session_service
    session_service._sessions.pop(session.session_id)
    restored = session_service.get_or_create(session.session_id)
    marker = next(index for index, item in enumerate(restored.transcript) if item["id"] == event["compaction_id"])
    assert restored.transcript[marker]["title"] == "上下文已压缩"
    assert "summary" not in restored.transcript[marker]
    assistant = restored.transcript[marker + 1]
    assert assistant["role"] == "assistant"
    assert assistant["blocks"][0]["content"] == "".join(deltas)


@pytest.mark.parametrize("abort", [False, True])
def test_manual_compact_recovers_reasoning_only_length_or_aborts(monkeypatch, tmp_path, abort):
    from app.runtime.engine import context_pipeline
    from app.services.context.message_groups import split_checkpoint
    from app.services.llm_client import llm_client

    initialize_store(tmp_path)
    config = replace(make_runtime_config(extra_body={"thinking": {"type": "enabled"}}), model="deepseek-v4.1-flash", sampling={
        "context_window": 409600, "max_output_tokens": 32000,
        "reasoning_effort": "high",
        "compaction_max_output_tokens": 8192,
        "deployment_profile": {"max_output_tokens": 32000},
    })
    setup_client(monkeypatch, config)
    session = build_session("compact_abort" if abort else "compact_truncated")
    for turn in range(1, 50):
        session.messages.extend([
            context_message_adapter.make_user_message("核查" * 270, turn_index=turn),
            context_message_adapter.make_assistant_message(content="完成" * 280, turn_index=turn),
        ])
    _, tail = split_checkpoint(session.messages, context_pipeline.estimator, 12000)
    tools = agent_engine._tools_for_context_budget(session, tool_service.list_tool_schemas(session))
    tail_tokens = context_pipeline._estimate_request([{"role": "system", "content": "root"}, *tail], tools, runtime=config)
    monkeypatch.setattr(context_pipeline.budget.config, "compact_target_ratio", (tail_tokens + 450) / 373600)
    original = copy.deepcopy(session.messages)
    output_limits = []
    original_post = llm_client._post_json

    async def post(runtime, payload, **kwargs):
        output_limits.append(payload["max_tokens"])
        if abort:
            session.request_abort()
            await asyncio.Future()
        return await original_post(runtime, payload, **kwargs)

    monkeypatch.setattr(llm_client, "_post_json", post)
    FakeAsyncClient.post_responses = [FakeResponse(json_data={"choices": [{"message": {"reasoning_content": "thinking"},
        "finish_reason": "length"}], "usage": {"prompt_tokens": 13476, "completion_tokens": limit,
        "completion_tokens_details": {"reasoning_tokens": limit}}}) for limit in (8192, 16384, 16384)]
    events = asyncio.run(collect_stream(session, "/compact"))
    assert any(event["type"] == "context_compaction_progress" and event["payload"]["stage"] == "summarizing" for event in events)
    if abort:
        assert any(event["type"] == "aborted" for event in events)
        assert not session.compaction_audit
        assert session.messages == original
        assert output_limits == [8192]
    else:
        assert output_limits == [8192, 16384, 16384]
        assert all(payload["thinking"] == {"type": "enabled"} and payload["reasoning_effort"] == "high"
                   for payload in FakeAsyncClient.post_payloads)
        payload = next(event["payload"] for event in events if event["type"] == "context_compacted")
        assert payload["error_code"] == "COMPACTION_OUTPUT_TRUNCATED"
        assert payload["tokens_after"] <= tail_tokens + 450
        assert events[-1]["payload"]["subtype"] == "success"


@pytest.mark.usefixtures("fast_recovery")
def test_upstream_retry_after_is_respected(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    busy = FakeResponse(status_code=429)
    busy.headers = {"retry-after": "7"}
    FakeAsyncClient.stream_responses = [busy, wire(chat_frame("done"))]
    events = asyncio.run(collect_stream(build_session("cooldown"), "task"))
    assert events[-1]["payload"]["subtype"] == "success"
    assert next(event["payload"]["delay"] for event in events if event["type"] == "stream_retry") == 7


@pytest.mark.usefixtures("fast_recovery")
@pytest.mark.parametrize("protocol", ["openai-responses", "anthropic-messages"])
def test_native_partial_stream_rebuilds_state_and_continues_same_task(monkeypatch, tmp_path, protocol):
    initialize_store(tmp_path)
    setup_client(monkeypatch, replace(make_runtime_config(), api_format=protocol))
    if protocol == "openai-responses":
        partial = wire({"type": "response.output_text.delta", "item_id": "p", "content_index": 0, "delta": "partial"})
        final = wire({"type": "response.completed", "response": {"output": [{"type": "message", "id": "f", "content": [{"type": "output_text", "text": "continued"}]}]}})
    else:
        partial = wire({"type": "message_start", "message": {}}, {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "partial"}})
        final = wire({"type": "message_start", "message": {}}, {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "continued"}},
            {"type": "content_block_stop", "index": 0}, {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}, {"type": "message_stop"})
    FakeAsyncClient.stream_responses = [partial, final]
    session = build_session("native_partial_" + protocol)
    events = asyncio.run(collect_stream(session, "继续处理 192.0.2.10"))
    assert events[-1]["payload"]["subtype"] == "success"
    assert session.context_epoch >= 1
    assert "192.0.2.10" in json.dumps(FakeAsyncClient.stream_payloads[1], ensure_ascii=False)
    assert "partial" in json.dumps(FakeAsyncClient.stream_payloads[1], ensure_ascii=False)
    contents = [block["content"] for message in transcript_service.build_session_transcript(session) for block in message.get("blocks", []) if block["kind"] == "content"]
    assert contents == ["partial", "continued"]
