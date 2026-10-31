"""从真实 Engine → LlmClient → 请求序列检查二次审计发现的缺口。"""
from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace

import pytest

from app.core.config import settings
from app.runtime.engine import agent_engine
from app.services.context.message_adapter import context_message_adapter
from app.services.context.integrity import context_integrity_validator
from app.services.llm_client import LlmClient
from app.services.session_types import AgentSession
from app.services.tool_service import tool_service
from app.services.transcript_service import transcript_service
from test_agent_engine import build_session, collect_stream, initialize_store
from test_context_compaction import make_pipeline
from test_llm_client import FakeAsyncClient, FakeResponse, make_runtime_config


def wire(*frames):
    return FakeResponse(lines=["data: " + json.dumps(frame, ensure_ascii=False) for frame in frames])


def chat_frame(content="", *, reasoning=None, calls=None, finish="stop"):
    delta = {"content": content}
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    if calls:
        delta["tool_calls"] = [{"index": index, "id": call_id, "function": {"name": name, "arguments": json.dumps(arguments)}}
            for index, (call_id, name, arguments) in enumerate(calls)]
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def setup_client(monkeypatch, config):
    FakeAsyncClient.reset()
    monkeypatch.setattr("app.services.llm_client.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.runtime.engine.llm_config_service.resolve_for_conversation", lambda conversation: config)
    monkeypatch.setattr(agent_engine, "_build_system_messages", lambda *args, **kwargs: [{"role": "system", "content": "root"}])
    schemas = tool_service.list_tool_schemas(AgentSession("catalog"))
    monkeypatch.setattr(tool_service, "list_tool_schemas", lambda session: [schema for schema in schemas if schema["function"]["name"] == "conversation_read"])


def test_deepseek_wire_replays_empty_and_all_prior_reasoning_after_tool_steps(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    config = replace(make_runtime_config(), model="deepseek-v4.1-flash")
    setup_client(monkeypatch, config)
    FakeAsyncClient.stream_responses = [wire(chat_frame(" 192.0.2.10\n", reasoning="")),
        wire(chat_frame(reasoning="第二轮推理", calls=[("read", "conversation_read", {})], finish="tool_calls")),
        wire(chat_frame("完成", reasoning="工具之后推理"))]
    session = build_session("deepseek_wire")

    async def run():
        first = await collect_stream(session, "原样复制 IP")
        second = await collect_stream(session, "继续并读取原文")
        return first, second

    first, second = asyncio.run(run())
    assert first[-2]["payload"]["result"] == " 192.0.2.10\n"
    assistants = [m for m in FakeAsyncClient.stream_payloads[-1]["messages"] if m["role"] == "assistant"]
    assert [m["reasoning_content"] for m in assistants] == ["", "第二轮推理"]
    assert assistants[0]["content"] == " 192.0.2.10\n"
    assert second[-1]["payload"]["subtype"] == "success"


def test_deepseek_old_missing_reasoning_sends_empty_field_without_rebasing(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    config = replace(make_runtime_config(), model="deepseek-v4.1-flash")
    setup_client(monkeypatch, config)
    FakeAsyncClient.stream_responses = [wire(chat_frame("new answer", reasoning="new thought"))]
    session = build_session("deepseek_missing")
    session.messages = [{"role": "user", "message_id": "old-user", "content": "目标 192.0.2.10"},
                        {"role": "assistant", "message_id": "old-assistant", "content": "legacy answer", "legacy_incomplete": True}]
    asyncio.run(collect_stream(session, "继续"))
    payload = FakeAsyncClient.stream_payloads[0]
    assert not any(m["role"] == "assistant" and "reasoning_content" not in m for m in payload["messages"])
    assert session.context_epoch == 0
    assert next(m for m in payload["messages"] if m["role"] == "assistant")["reasoning_content"] == ""
    assert any(m.get("message_id") == "old-assistant" for m in session.messages)
    assert "192.0.2.10" in json.dumps(payload, ensure_ascii=False)


def test_deepseek_required_replay_recovers_conflicting_setting_without_interrupting():
    config = replace(make_runtime_config(), model="deepseek-v4.1-flash", sampling={"reasoning_replay": "disabled"})
    payload = LlmClient()._payload(config, [{"role": "assistant", "content": "answer"}], [{"type": "function", "function": {"name": "noop", "parameters": {"type": "object"}}}], stream=True)
    assert payload["messages"][0]["reasoning_content"] == ""


def test_length_continuation_has_one_persisted_ui_projection(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    FakeAsyncClient.stream_responses = [wire(chat_frame("first", reasoning="think", finish="length")), wire(chat_frame("second", reasoning="next"))]
    session = build_session("continuation_projection")
    asyncio.run(collect_stream(session, "continue"))
    transcript = transcript_service.build_session_transcript(session)
    texts = [block["content"] for message in transcript if message["role"] == "assistant" for block in message["blocks"] if block["kind"] == "content"]
    assert texts == ["first", "second"]


def test_native_compaction_tool_waits_until_call_results_are_closed(monkeypatch, tmp_path, checkpoint_model):
    initialize_store(tmp_path)
    config = replace(make_runtime_config(), api_format="openai-responses")
    setup_client(monkeypatch, config)
    items = [{"type": "reasoning", "id": "thought", "encrypted_content": "opaque"},
             {"type": "function_call", "id": "fc", "call_id": "compact", "name": "context_compact", "arguments": "{}"}]
    FakeAsyncClient.stream_responses = [wire({"type": "response.completed", "response": {"output": items}}),
        wire({"type": "response.completed", "response": {"output": [{"type": "message", "id": "msg", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]}]}})]
    session = build_session("native_compact_tool")
    session.provider_binding = __import__("app.services.context.provider_adapters", fromlist=["binding"]).binding(config)
    session.messages = [context_message_adapter.make_user_message("old " * 500, turn_index=i) for i in range(1, 10)]
    seen = []
    from app.services.context.context_pipeline import context_pipeline
    compact = context_pipeline.compact_session_async

    async def check_closed(**kwargs):
        report = context_integrity_validator.validate(context_message_adapter.to_api_messages(kwargs["session"].messages))
        seen.append(report)
        assert report.ok, report.errors
        return await compact(**kwargs)

    monkeypatch.setattr(context_pipeline, "compact_session_async", check_closed)
    events = asyncio.run(collect_stream(session, "compact then continue"))
    assert seen and all(report.ok for report in seen)
    assert events[-1]["payload"]["subtype"] == "success"


def test_context_budget_uses_compiled_replay_policy_and_final_payload_limit(monkeypatch):
    pipeline = make_pipeline()
    session = AgentSession("compiled_budget", messages=[context_message_adapter.make_assistant_message(content="answer", reasoning_content="x" * 100000, turn_index=1), context_message_adapter.make_user_message("next", turn_index=2)])
    config = replace(make_runtime_config(), sampling={"reasoning_replay": "disabled"})
    prepared = pipeline.prepare_for_llm(session=session, llm_runtime=config, messages=[{"role": "system", "content": "root"}, *session.messages], turn_index=2, max_output_tokens=1000, allow_compaction=False)
    assert prepared.state.last_known_token_estimate < 1000


def test_terminal_sse_selects_primary_choice_without_mixing_candidates(monkeypatch):
    setup_client(monkeypatch, make_runtime_config())
    FakeAsyncClient.stream_responses = [wire({"choices": [{"index": 0, "delta": {"content": "a"}, "finish_reason": "stop"}, {"index": 1, "delta": {"content": "b"}, "finish_reason": "stop"}]})]

    async def run():
        return [chunk async for chunk in LlmClient().stream_chat_completion(make_runtime_config(), [], [])]

    chunks = asyncio.run(run())
    assert chunks[0]["choices"] == [{"index": 0, "delta": {"content": "a"}, "finish_reason": "stop"}]


def test_deepseek_missing_response_reasoning_executes_valid_tool_and_continues(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    setup_client(monkeypatch, replace(make_runtime_config(), model="deepseek-flash"))
    FakeAsyncClient.stream_responses = [wire(chat_frame(calls=[("read", "conversation_read", {})], finish="tool_calls")), wire(chat_frame("done"))]
    session = build_session("missing_response_thought")
    events = asyncio.run(collect_stream(session, "read history"))
    assert events[-1]["payload"]["subtype"] == "success"
    assert len([event for event in events if event["type"] == "tool_finished"]) == 1
    assert next(m for m in FakeAsyncClient.stream_payloads[1]["messages"] if m["role"] == "assistant")["reasoning_content"] == ""
    assert not any(event["type"] == "error" for event in events)


def test_chat_requests_usage_and_records_it_during_tool_rounds(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    tool = chat_frame(calls=[("read", "conversation_read", {})], finish="tool_calls")
    tool["usage"] = {"prompt_tokens": 123, "completion_tokens": 20}
    FakeAsyncClient.stream_responses = [wire(tool), wire(chat_frame("done"))]
    session = build_session("usage_tool_round")
    asyncio.run(collect_stream(session, "read"))
    assert all(payload["stream_options"]["include_usage"] for payload in FakeAsyncClient.stream_payloads)
    assert session.context_state["last_api_usage"]["prompt_tokens"] == 123
    no_usage = replace(make_runtime_config(), sampling={"stream_usage": False})
    assert "stream_options" not in LlmClient()._payload(no_usage, [], [], stream=True)


def test_protocol_error_commits_partial_before_next_turn(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    FakeAsyncClient.stream_responses = [wire(chat_frame("partial 192.0.2.10", reasoning="thought", finish=None), {"error": {"message": "broken"}}), wire(chat_frame("continued"))]
    session = build_session("protocol_partial")
    events = asyncio.run(collect_stream(session, "task"))
    assert events[-1]["payload"]["subtype"] == "success"
    assert any(m.get("content") == "partial 192.0.2.10" for m in FakeAsyncClient.stream_payloads[1]["messages"])
    message = next(m for m in session.messages if m.get("role") == "assistant")
    assert message["content"] == "partial 192.0.2.10"
    assert message["reasoning_content"] == "thought"
    assert message["status"] == "incomplete"


def test_native_rule_injection_enters_epoch_before_replaying_signature(monkeypatch, tmp_path):
    from app.services.tool_result_service import ToolExecutionResult
    initialize_store(tmp_path)
    config = replace(make_runtime_config(), api_format="openai-responses")
    setup_client(monkeypatch, config)
    original_execute = tool_service.execute

    async def inject(session, name, arguments, **kwargs):
        return ToolExecutionResult.success("loaded", "skill loaded", injected_messages=[{"role": "system", "content": "active skill rule"}])

    monkeypatch.setattr(tool_service, "execute", inject)
    FakeAsyncClient.stream_responses = [wire({"type": "response.completed", "response": {"output": [
        {"type": "reasoning", "id": "r", "encrypted_content": "old-signed-state"},
        {"type": "function_call", "id": "f", "call_id": "load", "name": "conversation_read", "arguments": "{}"}]}}),
        wire({"type": "response.completed", "response": {"output": [{"type": "message", "id": "m", "content": [{"type": "output_text", "text": "done"}]}]}})]
    session = build_session("native_injected_rule")
    asyncio.run(collect_stream(session, "load skill"))
    second = json.dumps(FakeAsyncClient.stream_payloads[1], ensure_ascii=False)
    assert "active skill rule" in second
    assert "old-signed-state" not in second
    assert session.context_epoch >= 1


def test_epoch_from_opaque_checkpoint_recovers_original_task_and_ui_sources(tmp_path):
    from app.services.context.history_store import history_store
    from app.services.context.epoch import rebase_context
    initialize_store(tmp_path)
    original = context_message_adapter.make_user_message("must preserve 192.0.2.10", turn_index=1)
    history_store.capture("opaque", {"messages": [original]})
    checkpoint = context_message_adapter.make_assistant_message(content="原生上下文检查点", turn_index=1,
        provider_payload={"complete_window": True, "items": [{"type": "compaction", "encrypted_content": "opaque"}]})
    checkpoint["compression_meta"] = {"source_message_ids": [original["message_id"]]}
    history_store.capture("opaque", {"messages": [checkpoint]})
    session = AgentSession("opaque", messages=[checkpoint])
    rebase_context(session, reason="model switch")
    assert "192.0.2.10" in session.messages[0]["content"]
    assert history_store.expand_history("opaque", session.messages)[0]["message_id"] == original["message_id"]


def test_summary_is_independent_of_output_schema_and_reduces_multiple_chunks(monkeypatch):
    from app.services.context.summarizer import compaction_summarizer
    from app.services.llm_client import llm_client
    labels = "【目标】task【约束与事实】fact【已完成与证据】done【待办与疑问】todo"
    seen = []

    async def summarize(config, messages, tools):
        seen.append((config, messages, tools))
        assert "response_format" not in config.extra_body
        assert config.extra_body["max_completion_tokens"] == config.sampling["max_output_tokens"]
        assert not tools
        assert config.sampling.get("stop") is None
        if messages[1]["content"].startswith("合并"):
            return {"content": labels}
        return {"content": labels + "x" * 5000}

    monkeypatch.setattr(llm_client, "create_chat_completion", summarize)
    config = replace(make_runtime_config(extra_body={"response_format": {"type": "json_object"}, "max_completion_tokens": 4000}), sampling={"context_window": 20000, "stop": ["【约束与事实】"]})
    result = asyncio.run(compaction_summarizer.summarize([{"role": "user", "content": "中" * 20000}], config))
    assert all(label in result for label in ("【目标】", "【待办与疑问】"))
    assert len(result) < 300
    assert len(seen) >= 4


def test_compaction_does_not_reintroduce_filtered_subagent_data(monkeypatch, checkpoint_model):
    pipeline = make_pipeline()
    session = AgentSession("filtered_compaction")
    hidden = context_message_adapter.make_user_message("DESTROYED_SUBAGENT_SECRET" + "h" * 2000, turn_index=1)
    hidden["subagent_id"] = "destroyed"
    session.messages = [hidden, *[context_message_adapter.make_user_message("x" * 2000, turn_index=i) for i in range(2, 14)]]
    visible = [m for m in session.messages if m is not hidden]
    seen = []

    async def summarize(messages, config):
        seen.extend(messages)
        return "【目标】task【约束与事实】fact【已完成与证据】done【待办与疑问】todo"

    monkeypatch.setattr("app.services.context.summarizer.compaction_summarizer.summarize", summarize)
    asyncio.run(pipeline.compact_session_async(session=session, llm_runtime=make_runtime_config(),
        messages=[{"role": "system", "content": "root"}, *visible], turn_index=14, max_output_tokens=1000))
    assert seen
    assert "DESTROYED_SUBAGENT_SECRET" not in json.dumps(seen)
    assert "DESTROYED_SUBAGENT_SECRET" not in json.dumps(session.messages)


def test_profile_summary_and_deployment_revision_are_effective():
    from app.schemas.llm import LlmResolvedConfig, LlmSamplingParams
    from app.services.context.provider_adapters import binding
    from app.services.llm_config_service import llm_config_service
    resolved = llm_config_service._with_profile_summary(LlmResolvedConfig(scope="user", model="alias", base_url="http://local/v1", sampling=LlmSamplingParams(model_profile="deepseek-thinking")))
    assert resolved.model_profile_id == "deepseek-thinking/v1"
    assert resolved.reasoning_required_with_tools
    assert "ultra" in resolved.reasoning_effort_options
    config = replace(make_runtime_config(), sampling={"deployment_revision": "weights-1"})
    assert binding(config) != binding(replace(config, sampling={"deployment_revision": "weights-2"}))


def test_backend_sse_is_invariant_at_every_utf8_byte_split_and_rejects_corruption():
    from app.services.context.stream_parser import SseDataDecoder
    frame = 'data: {"choices":[{"delta":{"content":"中文 192.0.2.10"},"finish_reason":"stop"}]}\r\n\r\n'.encode()
    expected = json.loads(frame.decode().split("data: ")[1].strip())
    for index in range(len(frame) + 1):
        decoder = SseDataDecoder()
        parsed = decoder.feed(frame[:index]) + decoder.feed(frame[index:], final=True)
        assert [json.loads(item) for item in parsed] == [expected]
    assert SseDataDecoder().feed(b'data: {"value":1}\r\r', final=True) == ['{"value":1}']
    assert SseDataDecoder().feed(b'\xef\xbb\xbfdata: {"value":1}\n\n', final=True) == ['{"value":1}']
    with pytest.raises(RuntimeError, match="UTF-8"):
        SseDataDecoder().feed(b'data: {"content":"\xff"}\n\n')
    with pytest.raises(RuntimeError, match="过大"):
        SseDataDecoder(max_frame_bytes=10).feed(b'data: ' + b'x' * 20)


def test_http_tail_failure_after_valid_terminal_does_not_replay_or_downgrade(monkeypatch, tmp_path):
    import httpx
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    response = wire(chat_frame("complete answer", reasoning="thought"))
    response._line_error = httpx.RemoteProtocolError("HTTP trailer missing")
    FakeAsyncClient.stream_responses = [response]
    events = asyncio.run(collect_stream(build_session("terminal_http_tail"), "task"))
    assert events[-1]["payload"]["subtype"] == "success"
    assert len(FakeAsyncClient.stream_payloads) == 1


def test_deepseek_responses_plain_reasoning_is_displayed_and_replayed(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    config = replace(make_runtime_config(), api_format="openai-responses", model="deepseek-flash")
    setup_client(monkeypatch, config)
    reasoning = {"type": "reasoning", "id": "r", "content": [{"type": "reasoning_text", "text": "plain thought"}]}
    answer = {"type": "message", "id": "m", "role": "assistant", "content": [{"type": "output_text", "text": "answer"}]}
    FakeAsyncClient.stream_responses = [wire({"type": "response.reasoning_text.delta", "item_id": "r", "output_index": 0, "content_index": 0, "delta": "plain thought"},
        {"type": "response.completed", "response": {"output": [reasoning, answer]}}),
        wire({"type": "response.completed", "response": {"output": [{**answer, "id": "m2"}]}})]
    session = build_session("deepseek_native_plain")

    async def run():
        first = await collect_stream(session, "first")
        await collect_stream(session, "next")
        return first

    first = asyncio.run(run())
    assert [item["payload"]["delta"] for item in first if item["type"] == "reasoning_delta"] == ["plain thought"]
    assert reasoning in FakeAsyncClient.stream_payloads[1]["input"]
    assert "include" not in FakeAsyncClient.stream_payloads[0]


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses", "anthropic-messages"])
def test_offline_validation_uses_compiler_without_model_calls(monkeypatch, protocol):
    from app.api.routes.llm import validate_llm_contract
    from app.schemas.llm import LlmConfigUpdateRequest
    from types import SimpleNamespace
    FakeAsyncClient.reset()
    monkeypatch.setattr("app.services.llm_client.httpx.AsyncClient", FakeAsyncClient)
    request = LlmConfigUpdateRequest(base_url="http://local/v1", model="alias", api_format=protocol, extra_headers={"x-api-key": "private"})
    response = validate_llm_contract(request, SimpleNamespace(kind="user"))
    assert response.data["validation_state"] == "offline_contract_passed"
    assert response.data["real_service_verified"] is False
    assert "private" not in json.dumps(response.data)
    assert FakeAsyncClient.post_payloads == FakeAsyncClient.stream_payloads == []


def test_deployment_limits_precede_catalog_and_bind_metadata(monkeypatch):
    from app.services.context.provider_adapters import binding
    pipeline = make_pipeline()
    monkeypatch.setattr(pipeline.budget, "get_context_window_for_model", lambda *args: (_ for _ in ()).throw(AssertionError("must not fetch catalog")))
    config = replace(make_runtime_config(), sampling={"deployment_profile": {"context_window": 20000, "max_output_tokens": 12000, "service_version": "v1"}})
    assert LlmClient().output_limit(config) == 12000
    session = AgentSession("declared_window", messages=[context_message_adapter.make_user_message("hello", turn_index=1)])
    prepared = pipeline.prepare_for_llm(session=session, llm_runtime=config, messages=session.messages, turn_index=1)
    assert prepared.state.effective_window == 4000
    assert prepared.state.budget_source == "deployment"
    changed = replace(config, sampling={"deployment_profile": {**config.sampling["deployment_profile"], "service_version": "v2"}})
    assert binding(config) != binding(changed)
    with pytest.raises(ValueError, match="硬上限"):
        LlmClient().output_limit(replace(config, sampling={**config.sampling, "max_output_tokens": 15000}))


def test_stop_seed_parallel_controls_are_mapped_and_unsupported_optional_values_are_omitted():
    tools = [{"type": "function", "function": {"name": "noop", "parameters": {"type": "object"}}}]
    config = replace(make_runtime_config(), sampling={"stop": ["END"], "seed": 0, "parallel_tool_calls": False})
    payload = LlmClient()._payload(config, [], tools, stream=True)
    assert payload["stop"] == ["END"] and payload["seed"] == 0 and payload["parallel_tool_calls"] is False
    messages = replace(config, api_format="anthropic-messages", sampling={"stop": "END", "parallel_tool_calls": False})
    native = LlmClient()._payload(messages, [], tools, stream=True)
    assert native["stop_sequences"] == ["END"]
    assert native["tool_choice"]["disable_parallel_tool_use"] is True
    assert "seed" not in LlmClient()._payload(replace(config, sampling={"seed": 0, "deployment_profile": {"supported_parameters": []}}), [], tools, stream=True)
    assert "stream_options" not in LlmClient()._payload(replace(config, sampling={"deployment_profile": {"supported_parameters": []}}), [], [], stream=True)


def test_global_sampling_does_not_leak_to_another_deployment_or_protocol(monkeypatch):
    from app.services.llm_config_service import llm_config_service
    monkeypatch.setattr(settings, "llm_model", "global-model")
    monkeypatch.setattr(settings, "llm_base_url", "http://global/v1")
    monkeypatch.setattr(settings, "llm_frequency_penalty", 0.4)
    assert llm_config_service._global_sampling_dict(model="other", base_url="http://global/v1")["frequency_penalty"] is None
    assert llm_config_service._global_sampling_dict(model="global-model", base_url="http://global/v1/", api_format="anthropic-messages")["frequency_penalty"] is None
    assert llm_config_service._global_sampling_dict(model="global-model", base_url="http://global/v1/")["frequency_penalty"] == 0.4


def test_reasoning_can_be_restored_only_from_matching_complete_attempt(tmp_path):
    from app.services.context.history_store import history_store
    initialize_store(tmp_path)
    config = replace(make_runtime_config(), model="deepseek-flash")
    session = AgentSession("restore_thought", provider_binding="same-binding", messages=[{"role": "assistant", "content": "answer", "attempt_id": "a"}])
    history_store.start_attempt(session.session_id, "a", {"model": config.model, "api_format": config.api_format, "deployment_binding": "same-binding"})
    history_store.save_attempt(session.session_id, "a", {"content": "answer", "reasoning_content": "", "tool_calls": {}}, "completed")
    assert history_store.restore_reasoning(session, config)
    assert session.messages[0]["reasoning_content"] == ""
    session.messages[0].pop("reasoning_content")
    session.provider_binding = "changed"
    assert not history_store.restore_reasoning(session, config)


def test_summary_requests_are_traced_but_never_recovered_as_conversation(monkeypatch, tmp_path):
    from app.services.context.summarizer import compaction_summarizer
    from app.services.context.history_store import history_store
    from app.services.store import store_service
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    labels = "【目标】task【约束与事实】fact【已完成与证据】done【待办与疑问】todo"
    FakeAsyncClient.post_responses = [FakeResponse(json_data={"choices": [{"message": {"content": labels}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 100, "completion_tokens": 50}})]
    config = replace(make_runtime_config(), trace_context={"session_id": "summary_trace", "purpose": "compaction"})
    assert asyncio.run(compaction_summarizer.summarize([{"role": "user", "content": "task"}], config)) == labels
    with store_service._connect() as conn:
        row = conn.execute("SELECT manifest,response FROM context_attempts WHERE session_id='summary_trace'").fetchone()
        assert json.loads(row["manifest"])["purpose"] == "compaction"
        assert history_store._decode(row["response"])["usage"]["prompt_tokens"] == 100
    assert not history_store.recover_attempts(AgentSession("summary_trace"))


def test_host_context_metadata_and_template_values_remain_bounded():
    from app.services.context.host_context import bounded_host_context, public_host_context, read_host_path
    from app.services.prompt_service import prompt_service
    host = {f"key-{i}": "x" * 2000 for i in range(1000)}
    bounded = bounded_host_context(host, max_chars=2000)
    assert len(bounded) <= 2000
    assert json.loads(bounded)["_omitted_key_count"] > 0
    value = public_host_context({"extras": {"items": [{"ip.addr": "192.0.2.10", "auth": "private"}]}})
    assert read_host_path(value, "/extras/items/0/ip.addr") == "192.0.2.10"
    with pytest.raises(ValueError):
        read_host_path(value, "/extras/items/0/auth")
    session = AgentSession("host_template", host_context={"extras": host})
    rendered = prompt_service._render_template("data={{host.extras}}", session=session)
    assert len(rendered) < 1000 and "host_context_read" in rendered


def test_usage_semantics_does_not_count_openai_cache_twice():
    pipeline = make_pipeline()
    session = AgentSession("usage_semantics")
    usage = {"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 10}
    openai = replace(make_runtime_config(), sampling={"deployment_profile": {"usage_semantics": "openai"}})
    anthropic = replace(openai, sampling={"deployment_profile": {"usage_semantics": "anthropic"}})
    assert pipeline.update_api_usage(session, usage, runtime=openai).last_known_token_estimate == 100
    assert pipeline.update_api_usage(session, usage, runtime=anthropic).last_known_token_estimate == 150


def test_invalid_tool_group_is_not_hidden_by_compaction(monkeypatch):
    from app.services.context.runtime_types import ContextIntegrityError
    pipeline = make_pipeline()
    session = AgentSession("invalid_group", messages=[{"role": "tool", "tool_call_id": "orphan", "content": "x" * 50000}])
    with pytest.raises(ContextIntegrityError):
        asyncio.run(pipeline.prepare_for_llm_async(session=session, llm_runtime=make_runtime_config(), messages=session.messages, turn_index=1, max_output_tokens=1000))


def test_compaction_wait_is_cancelled_without_starting_a_model_request(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def prepare(**kwargs):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr("app.runtime.engine.context_pipeline.prepare_for_llm_async", prepare)
    session = build_session("cancel_compaction")

    async def run():
        task = asyncio.create_task(collect_stream(session, "task"))
        await asyncio.wait_for(entered.wait(), timeout=5)
        session.request_abort()
        return await asyncio.wait_for(task, timeout=2)

    events = asyncio.run(run())
    assert cancelled.is_set()
    assert events[-1]["payload"]["subtype"] == "aborted"
    assert FakeAsyncClient.stream_payloads == []


def test_runtime_limit_also_applies_while_waiting_for_compaction(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1)
    cancelled = []

    async def prepare(**kwargs):
        try:
            await asyncio.Future()
        finally:
            cancelled.append(True)

    monkeypatch.setattr("app.runtime.engine.context_pipeline.prepare_for_llm_async", prepare)
    events = asyncio.run(asyncio.wait_for(collect_stream(build_session("timeout_compaction"), "task"), timeout=5))
    assert cancelled == [True]
    assert events[-1]["payload"]["subtype"] == "error_runtime_limit"
    assert FakeAsyncClient.stream_payloads == []


@pytest.mark.parametrize("key", ["truncation", "instructions", "system", "context_management", "conversation", "background"])
def test_runtime_owned_fields_cannot_bypass_context_compiler(key):
    from app.schemas.llm import LlmConfigUpdateRequest
    with pytest.raises(ValueError):
        LlmConfigUpdateRequest(base_url="http://local/v1", model="alias", extra_body={key: "override"})
    with pytest.raises(ValueError):
        LlmClient()._payload(make_runtime_config(extra_body={key: "override"}), [], [], stream=True)


def test_nested_native_effort_is_migrated_and_conflicts_are_rejected():
    from app.schemas.llm import LlmConfigUpdateRequest
    request = LlmConfigUpdateRequest(base_url="http://local/v1", model="alias", api_format="openai-responses", extra_body={"reasoning": {"effort": "xhigh", "summary": "auto"}})
    assert request.sampling.reasoning_effort == "xhigh"
    assert request.extra_body == {"reasoning": {"summary": "auto"}}
    with pytest.raises(ValueError, match="冲突"):
        LlmConfigUpdateRequest(base_url="http://local/v1", model="alias", api_format="anthropic-messages", extra_body={"output_config": {"effort": "low"}}, sampling={"reasoning_effort": "high"})


def test_explicit_tools_disabled_cannot_execute_hallucinated_calls(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config(extra_body={"tools": []}))
    FakeAsyncClient.stream_responses = [wire(chat_frame(calls=[("forbidden", "conversation_read", {})], finish="tool_calls")), wire(chat_frame("answer without tools"))]
    executed = []

    async def execute(*args, **kwargs):
        executed.append(args)

    monkeypatch.setattr(tool_service, "execute", execute)
    events = asyncio.run(collect_stream(build_session("disabled_tools"), "task"))
    assert events[-1]["payload"]["subtype"] == "success"
    assert not any(event["type"] == "tool_started" for event in events)
    assert executed == []


def test_cancel_during_retry_delay_does_not_send_another_request(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    FakeAsyncClient.stream_responses = [FakeResponse(status_code=503)]
    session = build_session("cancel_backoff")

    async def run():
        events = []
        async for event in agent_engine.stream_chat(session, "task"):
            events.append(event.model_dump(mode="json"))
            if event.type == "stream_retry":
                session.request_abort()
        return events

    events = asyncio.run(run())
    assert events[-1]["payload"]["subtype"] == "aborted"
    assert len(FakeAsyncClient.stream_payloads) == 1


def test_failed_live_run_preserves_one_ui_view_and_one_model_response(monkeypatch, tmp_path):
    from app.services.agent_run_service import agent_run_service
    from app.services.store import store_service
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    FakeAsyncClient.stream_responses = [wire(chat_frame("partial", reasoning="thought", finish=None), {"error": {"message": "bad frame"}})]
    session = build_session("failed_live_view")

    async def run():
        run_id = await agent_run_service.start_chat_run(session, "task")
        await agent_run_service.wait_for_run(run_id)
        return run_id

    run_id = asyncio.run(run())
    assert store_service.get_agent_run(run_id)["status"] == "failed"
    api = context_message_adapter.to_api_messages(session.messages)
    assert [m["content"] for m in api if m["role"] == "assistant"] == ["partial"]
    contents = [block["content"] for item in session.transcript if item["role"] == "assistant" for block in item["blocks"] if block["kind"] == "content"]
    assert contents == ["partial"]


def test_live_tool_cancel_retains_unknown_receipt_and_does_not_rerun(monkeypatch, tmp_path):
    from app.services.agent_run_service import agent_run_service
    from app.services.store import store_service
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    FakeAsyncClient.stream_responses = [wire(chat_frame(reasoning="thought", calls=[("slow", "conversation_read", {})], finish="tool_calls")), wire(chat_frame("inspected", reasoning="next"))]
    session = build_session("cancel_live_tool")
    entered = asyncio.Event()
    executed = []
    original_handler = tool_service._registry.get_handler

    async def slow(session, arguments):
        executed.append(arguments)
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(tool_service._registry, "get_handler", lambda name: slow if name == "conversation_read" else original_handler(name))

    async def run():
        run_id = await agent_run_service.start_chat_run(session, "task")
        await asyncio.wait_for(entered.wait(), timeout=5)
        agent_run_service.request_cancel(session)
        await asyncio.wait_for(agent_run_service.wait_for_run(run_id), timeout=5)
        assert len(FakeAsyncClient.stream_payloads) == 1
        assert any(block.get("status") == "aborted" for item in session.transcript if item["role"] == "assistant" for block in item["blocks"] if block["kind"] == "tool")
        await collect_stream(session, "inspect status only")
        return run_id

    run_id = asyncio.run(run())
    assert store_service.get_agent_run(run_id)["status"] == "cancelled"
    assert len(executed) == 1
    with store_service._connect() as conn:
        assert conn.execute("SELECT status FROM context_tool_executions WHERE session_id=?", (session.session_id,)).fetchone()[0] == "executing"
    assert context_integrity_validator.validate(context_message_adapter.to_api_messages(session.messages)).ok


def test_native_compaction_closes_an_entire_multi_call_batch(monkeypatch, tmp_path, checkpoint_model):
    from app.services.context.context_pipeline import context_pipeline
    from app.services.context.provider_adapters import binding
    initialize_store(tmp_path)
    config = replace(make_runtime_config(), api_format="openai-responses")
    setup_client(monkeypatch, config)
    items = [{"type": "function_call", "id": "c1", "call_id": "compact", "name": "context_compact", "arguments": "{}"},
             {"type": "function_call", "id": "c2", "call_id": "read", "name": "conversation_read", "arguments": "{}"}]
    FakeAsyncClient.stream_responses = [wire({"type": "response.completed", "response": {"output": items}}),
        wire({"type": "response.completed", "response": {"output": [{"type": "message", "id": "m", "content": [{"type": "output_text", "text": "done"}]}]}})]
    session = build_session("native_compact_batch")
    session.provider_binding = binding(config)
    session.messages = [context_message_adapter.make_user_message("old " * 500, turn_index=i) for i in range(1, 10)]
    compact = context_pipeline.compact_session_async
    observed = []

    async def check(**kwargs):
        results = {m.get("tool_call_id") for m in kwargs["session"].messages if m.get("role") == "tool"}
        observed.append(results)
        assert {"compact", "read"} <= results
        return await compact(**kwargs)

    monkeypatch.setattr(context_pipeline, "compact_session_async", check)
    events = asyncio.run(collect_stream(session, "compact and read"))
    assert len(observed) == 1
    assert events[-1]["payload"]["subtype"] == "success"


def test_auto_profile_does_not_mistake_older_non_thinking_deepseek_for_v4():
    from app.services.context.model_profile import resolve_model_profile
    tools = [{"type": "function", "function": {"name": "noop", "parameters": {"type": "object"}}}]
    old = replace(make_runtime_config(), model="deepseek-coder-33b")
    assert not resolve_model_profile(old).requires_reasoning(old, tools)
    assert LlmClient()._payload(old, [{"role": "assistant", "content": "legacy"}], tools, stream=True)
    current = replace(old, model="Deepseekv4.1flash")
    assert resolve_model_profile(current).requires_reasoning(current, tools)


def test_conflicting_thinking_switches_are_rejected_before_saving():
    from app.schemas.llm import LlmConfigUpdateRequest
    with pytest.raises(ValueError, match="冲突"):
        LlmConfigUpdateRequest(base_url="http://local/v1", model="alias", extra_body={"thinking": {"type": "enabled"}, "chat_template_kwargs": {"enable_thinking": False}})


def test_declared_generic_reasoning_field_can_be_required_and_replayed():
    config = replace(make_runtime_config(), sampling={"reasoning_field": "reasoning", "deployment_profile": {"reasoning_required_with_tools": True}})
    tools = [{"type": "function", "function": {"name": "noop", "parameters": {"type": "object"}}}]
    payload = LlmClient()._payload(config, [{"role": "assistant", "reasoning_content": "", "content": "body"}], tools, stream=True)
    assert payload["messages"][0]["reasoning"] == ""


def test_event_envelope_survives_database_replay_and_legacy_rows(tmp_path):
    from app.services.agent_run_service import AgentRunService
    from app.services.store import store_service
    from app.schemas.agent import AgentEvent
    initialize_store(tmp_path)
    service = AgentRunService()
    store_service.create_agent_run(run_id="envelope", session_id="session", status="running")
    event = AgentEvent(type="content_delta", session_id="session", attempt_id="attempt", message_id="message",
        part_id="part", step_id="step", turn_id="turn", context_epoch_id=3, continuation_of="previous", payload={"delta": "正文"})
    asyncio.run(service.publish_event("envelope", event))
    restored = service.list_events_after("envelope", 0)[0]
    assert restored.model_dump(exclude={"timestamp"}) == event.model_dump(exclude={"timestamp"})
    assert restored.event_id == "envelope:1"
    assert "_runtime_envelope" not in restored.payload
    store_service.append_agent_run_event(run_id="envelope", event_type="content_completed", payload={})
    assert service.list_events_after("envelope", 1)[0].protocol_version == 1


def test_diagnostics_are_scoped_paginated_metadata_only(tmp_path):
    from app.services.context.history_store import history_store
    initialize_store(tmp_path)
    for session, attempt, purpose in [("own", "a", "conversation"), ("own", "b", "compaction"), ("other", "secret", "conversation")]:
        history_store.start_attempt(session, attempt, {"purpose": purpose, "model": "local", "request_hash": "digest", "api_key": "KEY", "raw_prompt": "PROMPT"})
        history_store.save_attempt(session, attempt, {"content": "BODY", "reasoning_content": "THOUGHT", "provider_payload": {"signature": "SIGNATURE"}, "usage": {"input_tokens": 42, "unsafe": "SECRET"}}, "completed")
    page = history_store.diagnostics("own", limit=1)
    assert page["items"][0]["purpose"] == "compaction"
    older = history_store.diagnostics("own", before=page["next_before"])
    assert [item["attempt_id"] for item in older["items"]] == ["a"]
    serialized = json.dumps([page, older])
    assert not any(secret in serialized for secret in ["KEY", "PROMPT", "BODY", "THOUGHT", "SIGNATURE", "SECRET", '"secret"'])
    assert page["items"][0]["usage"] == {"input_tokens": 42}


def test_history_search_respects_small_total_budget_and_version_refs(tmp_path):
    from app.services.context.history_store import history_store
    initialize_store(tmp_path)
    old = {"role": "assistant", "message_id": "same", "content": "old 192.0.2.10"}
    history_store.capture("own", {"messages": [old]})
    history_store.capture("own", {"messages": [{**old, "content": "new"}]})
    item = history_store.read("own", query="192.0.2.10")["items"][0]
    assert not item["is_latest_version"]
    assert history_store.read("own", message_id="same", version_digest=item["version_digest"])["items"][0]["content"] == old["content"]
    history_store.capture("own", {"messages": [{"role": "user", "message_id": str(i), "content": "value"} for i in range(20)]})
    assert sum(len(item["content"]) for item in history_store.read("own", limit=3)["items"]) <= 3


def test_engine_events_identify_continuation_and_canonical_message(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    FakeAsyncClient.stream_responses = [wire(chat_frame("first", finish="length")), wire(chat_frame("second"))]
    session = build_session("event_identity")
    events = asyncio.run(collect_stream(session, "continue"))
    parts = [event for event in events if event["type"] == "content_delta"]
    assert len(parts) == 2
    assert parts[0]["message_id"] != parts[1]["message_id"]
    assert parts[1]["continuation_of"] == parts[0]["attempt_id"]
    assert all(any(message.get("message_id") == part["message_id"] for message in session.messages) for part in parts)
    completed = [event for event in events if event["type"] == "content_completed"]
    assert [event["payload"]["status"] for event in completed] == ["incomplete", "completed"]
    assert [block["status"] for message in transcript_service.build_session_transcript(session) for block in message.get("blocks", []) if block["kind"] == "content"] == ["incomplete", "done"]


def test_official_parameter_diagnostics_do_not_guess_local_vllm_behavior():
    config = replace(make_runtime_config(), model="deepseek-flash", base_url="https://api.deepseek.com/v1", sampling={"temperature": 0.5, "top_p": 0.8})
    client = LlmClient()
    request = client._payload(config, [], [], stream=True)
    described = client.describe_parameters(config, request)
    assert described["temperature"]["status"] == "ignored_by_service"
    assert described["top_p"]["status"] == "constrained_by_service"
    local = replace(config, base_url="http://local/v1")
    assert client.describe_parameters(local, request)["temperature"]["status"] == "sent_unverified"


def test_deepseek_responses_offline_check_uses_plain_reasoning(monkeypatch):
    config = replace(make_runtime_config(), api_format="openai-responses", model="deepseek-flash")
    client = LlmClient()
    original = client._payload
    observed = []
    def record(*args, **kwargs):
        request = original(*args, **kwargs)
        observed.append(request)
        return request
    monkeypatch.setattr(client, "_payload", record)
    assert client.validate_config_contract(config)["validation_state"] == "offline_contract_passed"
    assert observed[0]["input"][2]["content"][0]["type"] == "reasoning_text"
    assert "encrypted_content" not in json.dumps(observed[0])


def test_diagnostics_route_enforces_session_access(monkeypatch):
    from app.api.routes import agent as route
    from app.api.deps import AuthContext
    from fastapi import HTTPException
    monkeypatch.setattr(route.store_service, "get_conversation_by_session", lambda _: {"platform_id": 7, "external_user_id": "alice"})
    with pytest.raises(HTTPException) as error:
        asyncio.run(route.context_diagnostics("owned", limit=1, before=None, auth=AuthContext(kind="embed", platform_id=7, external_user_id="bob")))
    assert error.value.status_code == 403


def test_live_snapshot_does_not_mark_previous_incomplete_part_done():
    from app.services.agent_run_service import AgentRunService
    from app.schemas.agent import AgentEvent
    service = AgentRunService()
    session = AgentSession("snapshot")
    session.active_run_view = {"assistant": {"blocks": [
        {"id": "first", "kind": "content", "content": "partial", "status": "incomplete"},
        {"id": "next", "kind": "content", "content": "rest", "status": "streaming"}]}}
    service._apply_event_to_active_view(session, AgentEvent(type="content_completed", session_id="snapshot", part_id="next", payload={"status": "completed"}))
    assert [block["status"] for block in session.active_run_view["assistant"]["blocks"]] == ["incomplete", "done"]


def test_explicit_override_clears_only_that_inherited_default_mode():
    from app.services.llm_config_service import LlmConfigService
    merge = LlmConfigService()._merge_sampling
    platform = {"temperature": None, "top_p": None, "model_defaults": ["temperature", "top_p"]}
    resolved = merge(platform, {"temperature": 0, "model_defaults": []})
    assert resolved["temperature"] == 0
    assert resolved["model_defaults"] == ["top_p"]
    assert merge(resolved, {"model_defaults": ["temperature"]})["temperature"] is None


def test_unknown_deployment_window_is_conservative_and_explicitly_unverified(monkeypatch):
    pipeline = make_pipeline()
    class Registry:
        def get_model(self, model):
            return None
        def get_context_window(self, model, betas=None):
            return 200000
    pipeline.budget.models_registry = Registry()
    config = replace(make_runtime_config(), sampling={"max_output_tokens": 4096})
    session = AgentSession("unknown_window")
    prepared = pipeline.prepare_for_llm(session=session, llm_runtime=config, messages=[{"role": "user", "content": "hi"}], turn_index=1)
    assert prepared.state.context_window == 65536
    assert prepared.events[0].payload["budget_source"] == "unverified_fallback"
    declared = replace(config, sampling={"max_output_tokens": 4096, "context_window": 131072})
    monkeypatch.setattr(pipeline.budget, "_get_registry", lambda: (_ for _ in ()).throw(AssertionError("no catalog")))
    assert pipeline.prepare_for_llm(session=session, llm_runtime=declared, messages=[{"role": "user", "content": "hi"}], turn_index=1).state.context_window == 131072


def test_replay_diagnostics_count_actual_wire_projection_including_empty_state():
    client = LlmClient()
    config = replace(make_runtime_config(), extra_body={"tools": []}, sampling={"reasoning_replay": "disabled"})
    messages = [{"role": "assistant", "content": "body", "reasoning_content": ""}]
    tools = [{"type": "function", "function": {"name": "noop", "parameters": {"type": "object"}}}]
    request = client._payload(config, messages, tools, stream=True)
    assert client.describe_replay(config, request) == {"reasoning_count": 0, "unit": "assistant_messages", "tool_names": []}
    enabled = replace(config, sampling={})
    assert client.describe_replay(enabled, client._payload(enabled, messages, tools, stream=True))["reasoning_count"] == 1
