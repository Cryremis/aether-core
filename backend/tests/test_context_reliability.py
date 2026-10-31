from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace

import httpx
import pytest

from app.core.config import settings
from app.services.context.history_store import history_store
from app.services.context.integrity import context_integrity_validator
from app.services.context.message_adapter import context_message_adapter
from app.services.context.provider_adapters import NativeStream, binding
from app.services.context.summarizer import compaction_summarizer
from app.services.llm_client import LlmClient
from app.services.llm_config_service import llm_config_service
from app.schemas.llm import LlmConfigUpdateRequest
from app.services.session_types import AgentSession
from test_llm_client import FakeAsyncClient, FakeResponse, make_runtime_config
from test_agent_engine import build_session, collect_stream, initialize_store


def test_reasoning_and_partial_content_are_replayed_after_length(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    seen = []

    async def stream(config, messages, tools, **kwargs):
        seen.append(copy.deepcopy(messages))
        if len(seen) == 1:
            yield {"choices": [{"delta": {"reasoning_content": "准确保留 192.0.2.10", "content": "开头 192.0.2.10"}, "finish_reason": "length"}]}
        else:
            yield {"choices": [{"delta": {"content": "结尾"}, "finish_reason": "stop"}]}

    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", stream)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
    asyncio.run(collect_stream(build_session("reliability_length"), "中文回答"))
    previous = next(message for message in seen[1] if message["role"] == "assistant")
    assert previous["content"] == "开头 192.0.2.10"
    assert previous["reasoning_content"] == "准确保留 192.0.2.10"


def test_request_defaults_do_not_inject_penalties_or_effort(monkeypatch):
    for key in ("llm_temperature", "llm_frequency_penalty", "llm_presence_penalty"):
        monkeypatch.setattr(settings, key, None)
    payload = LlmClient()._payload(make_runtime_config(), [{"role": "user", "content": "你好"}], [], stream=True)
    assert not {"temperature", "frequency_penalty", "presence_penalty", "reasoning_effort", "tool_stream"}.intersection(payload)


def test_model_default_stops_inheritance_and_zero_is_explicit():
    merged = llm_config_service._merge_sampling({"temperature": 0.7, "frequency_penalty": 0.4},
        {"model_defaults": ["temperature"], "frequency_penalty": 0})
    assert merged["temperature"] is None
    assert merged["frequency_penalty"] == 0


@pytest.mark.parametrize("key,value", [("model", "other"), ("messages", []), ("tools", [{"bad": True}]), ("stream", False), ("n", 2)])
def test_extra_body_cannot_replace_runtime_contract(key, value):
    with pytest.raises(ValueError):
        LlmClient()._payload(make_runtime_config(extra_body={key: value}), [], [], stream=True)


def test_legacy_output_limit_is_resolved_before_budgeting():
    config = make_runtime_config(extra_body={"max_tokens": 1200})
    assert LlmClient().output_limit(config) == 1200
    assert LlmClient()._payload(config, [], [], stream=True)["max_tokens"] == 1200


def test_integrity_requires_unique_closed_tool_groups():
    assistant = {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "noop", "arguments": "{}"}}]}
    result = {"role": "tool", "tool_call_id": "a", "content": "ok"}
    assert not context_integrity_validator.validate([assistant]).ok
    assert context_integrity_validator.validate([assistant, result]).ok
    assert not context_integrity_validator.validate([assistant, result, result]).ok


def test_runtime_notices_keep_chronological_position():
    messages = [{"role": "system", "content": "初始"}, {"role": "user", "content": "任务", "private_metadata": "secret"},
                {"role": "system", "content": "之后生效"}]
    compiled = context_message_adapter.to_api_messages(messages)
    assert [message["content"] for message in compiled] == ["初始", "任务", "之后生效"]
    assert "private_metadata" not in compiled[1]


def test_history_survives_compaction_snapshot_and_is_session_scoped(tmp_path):
    initialize_store(tmp_path)
    original = {"role": "tool", "message_id": "original", "content": "x" * 400 + "192.0.2.10"}
    history_store.capture("a", {"messages": [original]})
    history_store.capture("a", {"messages": [{"role": "user", "content": "检查点"}]})
    assert history_store.read("a", message_id="original")["items"][0]["content"].endswith("192.0.2.10")
    assert history_store.read("b", message_id="original")["items"] == []
    assert history_store.snapshot("a")["messages"][0]["content"] == "检查点"
    history_store.delete("a")
    assert history_store.read("a")["items"] == []


def test_tool_receipt_prevents_reexecution_and_unknown_effect_is_not_retried(tmp_path):
    initialize_store(tmp_path)
    assert history_store.claim_tool("s", "r", "c", "input")[0] == "claimed"
    assert history_store.claim_tool("s", "r", "c", "input")[0] == "executing"
    history_store.finish_tool("s", "r", "c", {"status": "success"})
    assert history_store.claim_tool("s", "r", "c", "input") == ("completed", {"status": "success"})
    with pytest.raises(ValueError):
        history_store.claim_tool("s", "r", "c", "changed")


def test_responses_replays_encrypted_items_without_flattening():
    config = replace(make_runtime_config(), api_format="openai-responses")
    items = [{"type": "reasoning", "id": "reason", "encrypted_content": "opaque"},
             {"type": "function_call", "call_id": "call", "name": "noop", "arguments": "{}"}]
    payload = LlmClient()._payload(config, [{"role": "assistant", "provider_payload": {"binding": binding(config), "items": items}},
        {"role": "tool", "tool_call_id": "call", "content": "ok"}], [], stream=True)
    assert payload["input"][:2] == items
    assert payload["input"][2]["type"] == "function_call_output"
    assert payload["store"] is False
    with pytest.raises(ValueError, match="上下文阶段"):
        LlmClient()._payload(replace(config, model="other"), [{"role": "assistant", "provider_payload": {"binding": binding(config), "items": items}}], [], stream=True)


def test_anthropic_empty_thinking_signature_survives_stream_and_replay():
    config = replace(make_runtime_config(), api_format="anthropic-messages")
    stream = NativeStream(config)
    stream.decode({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
    stream.decode({"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "signed-state"}})
    stream.decode({"type": "content_block_stop", "index": 0})
    final = stream.decode({"type": "message_stop"})[0]
    saved = final["provider_payload"]
    replay = LlmClient()._payload(config, [{"role": "assistant", "provider_payload": saved}], [], stream=True)
    assert replay["messages"][0]["content"] == [{"type": "thinking", "thinking": "", "signature": "signed-state"}]


@pytest.mark.parametrize("lines", [
    ['data: {"choices":[{"delta":{"content":"半段"}}]}'],
    ['data: {"oops":', ''],
    ['data: {"error":{"message":"bad template"}}'],
])
def test_invalid_or_unfinished_stream_does_not_succeed(monkeypatch, lines):
    FakeAsyncClient.stream_responses = [FakeResponse(lines=lines)]
    monkeypatch.setattr("app.services.llm_client.httpx.AsyncClient", FakeAsyncClient)

    async def collect():
        return [chunk async for chunk in LlmClient().stream_chat_completion(make_runtime_config(), [], [])]

    with pytest.raises((RuntimeError, httpx.RemoteProtocolError)):
        asyncio.run(collect())


def test_exact_facts_include_tail_beyond_old_320_character_cut():
    facts = compaction_summarizer.exact_facts([{"content": "a" * 1000 + " 192.0.2.10 http://example.invalid/a"}])
    assert "192.0.2.10" in facts
    assert "http://example.invalid/a" in facts
    tool_facts = compaction_summarizer.exact_facts([{"tool_calls": [{"function": {
        "arguments": json.dumps({"ip": "2001:db8::1", "path": "C:\\Work\\file_v1.2.txt"})}}]}])
    assert "2001:db8::1" in tool_facts
    assert "C:\\Work\\file_v1.2.txt" in tool_facts


def test_configuration_migrates_legacy_parameters_and_rejects_conflicts():
    config = LlmConfigUpdateRequest(base_url="http://local/v1", model="alias", extra_body={"temperature": 0, "max_tokens": 1234})
    assert config.sampling.temperature == 0
    assert config.sampling.max_output_tokens == 1234
    assert config.extra_body == {}
    with pytest.raises(ValueError, match="冲突"):
        LlmConfigUpdateRequest(base_url="http://local/v1", model="alias", extra_body={"temperature": 0.5}, sampling={"model_defaults": ["temperature"]})
    with pytest.raises(ValueError, match="冲突"):
        LlmClient().output_limit(replace(make_runtime_config(extra_body={"max_tokens": 10}), sampling={"max_output_tokens": 20}))


def test_stale_writer_cannot_overwrite_or_resurrect_ledger(tmp_path):
    initialize_store(tmp_path)
    revision = history_store.capture("cas", {"messages": [{"content": "first"}]}, expected_revision=0)
    history_store.capture("cas", {"messages": [{"content": "second"}]}, expected_revision=revision)
    with pytest.raises(RuntimeError, match="冲突"):
        history_store.capture("cas", {"messages": [{"content": "stale"}]}, expected_revision=revision)
    assert history_store.snapshot("cas")["messages"][0]["content"] == "second"
    history_store.delete("cas")
    with pytest.raises(RuntimeError, match="已删除"):
        history_store.capture("cas", {"messages": []}, expected_revision=revision)


def test_attempt_recovery_keeps_tool_only_response_and_does_not_resurrect_checkpointed_attempt(tmp_path):
    initialize_store(tmp_path)
    session = AgentSession("attempt_recovery")
    history_store.start_attempt(session.session_id, "attempt", {"api_format": "openai-compatible", "run_id": "gone", "turn_index": 1})
    history_store.save_attempt(session.session_id, "attempt", {"tool_calls": {"0": {"id": "call", "name": "noop", "arguments": "{}"}}}, "completed")
    assert history_store.recover_attempts(session)
    assert session.messages[0]["tool_calls"][0]["id"] == "call"
    history_store.capture(session.session_id, {"messages": session.messages})
    session.messages = [{"role": "user", "content": "checkpoint"}]
    history_store.capture(session.session_id, {"messages": session.messages})
    assert not history_store.recover_attempts(session)


def test_optional_ledger_encryption_covers_thoughts_and_receipts(monkeypatch, tmp_path):
    from app.services.store import store_service
    initialize_store(tmp_path)
    monkeypatch.setattr(settings, "capability_secret_encryption_key", "test-encryption-key")
    history_store.capture("encrypted", {"messages": [{"message_id": "m", "content": "body", "reasoning_content": "private-thought"}]})
    history_store.claim_tool("encrypted", "r", "c", "hash")
    history_store.finish_tool("encrypted", "r", "c", {"data": "private-receipt"})
    with store_service._connect() as conn:
        payload = conn.execute("SELECT payload FROM context_records WHERE session_id='encrypted'").fetchone()[0]
        assert payload.startswith("encrypted:") and "private-thought" not in payload
        receipt = conn.execute("SELECT payload FROM context_tool_executions WHERE session_id='encrypted'").fetchone()[0]
        assert "private-receipt" not in receipt
    assert history_store.snapshot("encrypted")["messages"][0]["reasoning_content"] == "private-thought"
    assert "reasoning_content" not in history_store.read("encrypted", message_id="m")["items"][0]


def test_epoch_keeps_trusted_rules_and_current_user_but_drops_private_state():
    from app.services.context.epoch import rebase_context
    session = AgentSession("epoch")
    rule = {"role": "system", "message_id": "rule", "content": "loaded skill rule"}
    user = {"role": "user", "message_id": "user", "content": "192.0.2.10"}
    session.messages = [rule, {"role": "assistant", "content": "history", "reasoning_content": "private-thought", "provider_payload": {"items": ["opaque"]}}, user]
    rebase_context(session, current_user_id="user", reason="model changed")
    assert session.messages[0] == rule
    assert session.messages[-1] == user
    assert "private-thought" not in json.dumps(session.messages)
    assert "opaque" not in json.dumps(session.messages)
    assert session.context_epoch == 1


def test_responses_final_items_are_ordered_and_never_duplicate_published_text():
    config = replace(make_runtime_config(), api_format="openai-responses")
    stream = NativeStream(config)
    stream.decode({"type": "response.output_text.delta", "item_id": "msg", "output_index": 1, "content_index": 0, "delta": "中文"})
    message = {"type": "message", "id": "msg", "role": "assistant", "content": [{"type": "output_text", "text": "中文"}]}
    reasoning = {"type": "reasoning", "id": "r", "encrypted_content": "opaque"}
    assert stream.decode({"type": "response.output_item.done", "output_index": 1, "item": message}) == []
    stream.decode({"type": "response.output_item.done", "output_index": 0, "item": reasoning})
    final = stream.decode({"type": "response.completed", "response": {"output": [reasoning, message]}})
    assert len(final) == 1
    assert final[0]["provider_payload"]["items"] == [reasoning, message]


def test_native_compaction_window_replaces_prefix_without_duplicate_system():
    config = replace(make_runtime_config(), api_format="openai-responses")
    items = [{"role": "system", "content": "root"}, {"type": "compaction", "encrypted_content": "state"}]
    payload = LlmClient()._payload(config, [{"role": "system", "content": "root"},
        {"role": "assistant", "provider_payload": {"binding": binding(config), "items": items, "complete_window": True}},
        {"role": "user", "content": "next task"}], [], stream=True)
    assert payload["input"] == [*items, {"role": "user", "content": "next task"}]


def test_responses_accepts_final_only_items_and_detects_changed_text():
    config = replace(make_runtime_config(), api_format="openai-responses")
    item = {"type": "message", "id": "m", "content": [{"type": "output_text", "text": "answer"}]}
    final = NativeStream(config).decode({"type": "response.completed", "response": {"output": [item]}})
    assert final[0]["choices"][0]["delta"]["content"] == "answer"
    stream = NativeStream(config)
    stream.decode({"type": "response.output_text.delta", "item_id": "m", "delta": "different"})
    with pytest.raises(RuntimeError, match="不一致"):
        stream.decode({"type": "response.completed", "response": {"output": [item]}})


def test_async_summary_failure_preserves_original_and_rules(monkeypatch):
    from test_context_compaction import make_pipeline
    pipeline = make_pipeline()
    session = AgentSession("summary_failure")
    session.messages = [{"role": "system", "content": "active rule", "message_id": "rule"}] + [
        {"role": "user" if index % 2 == 0 else "assistant", "content": "history " * 1000, "turn_index": index + 1}
        for index in range(12)]
    context_message_adapter.normalize_session(session)
    original = copy.deepcopy(session.messages)

    async def fail(*args):
        raise RuntimeError("summary unavailable")

    monkeypatch.setattr(compaction_summarizer, "summarize", fail)
    with pytest.raises(RuntimeError, match="summary unavailable"):
        asyncio.run(pipeline.compact_session_async(session=session, llm_runtime=make_runtime_config(),
            messages=[{"role": "system", "content": "root"}, *session.messages], turn_index=13, max_output_tokens=1000))
    assert session.messages == original


def test_subagent_notice_waits_for_tool_dependency_to_close():
    messages = [{"role": "assistant", "tool_calls": [{"id": "c"}]},
                {"role": "user", "content": "notification", "kind": "subagent_result"},
                {"role": "tool", "tool_call_id": "c", "content": "done"}]
    compiled = context_message_adapter.to_api_messages(messages)
    assert [item["role"] for item in compiled] == ["assistant", "tool", "user"]
    assert context_integrity_validator.validate(compiled).ok


def test_native_checkpoint_keeps_system_rule_once_and_rejects_concurrent_change(monkeypatch):
    from test_context_compaction import make_pipeline
    from app.services.context.runtime_types import ContextIntegrityError
    pipeline = make_pipeline()
    session = AgentSession("native_checkpoint")
    session.messages = [{"role": "system", "content": "active rule", "message_id": "rule"}] + [
        {"role": "user" if index % 2 == 0 else "assistant", "content": "x" * 2000, "turn_index": index + 1}
        for index in range(12)]
    config = replace(make_runtime_config(), api_format="anthropic-messages")

    async def summarize(*args):
        return "【目标】task【约束与事实】facts【已完成与证据】done【待办与疑问】todo"

    monkeypatch.setattr(compaction_summarizer, "summarize", summarize)
    prepared = asyncio.run(pipeline.compact_session_async(session=session, llm_runtime=config,
        messages=[{"role": "system", "content": "root"}, *session.messages], turn_index=13, max_output_tokens=1000))
    assert [m["content"] for m in prepared.messages if m["role"] == "system"] == ["root", "active rule"]
    assert prepared.plan.requires_llm_summary
    assert session.context_epoch == 1

    session.messages.extend([{"role": "user", "content": "y" * 2000, "turn_index": i} for i in range(14, 24)])

    async def changed(*args):
        session.messages.append({"role": "user", "content": "new inbox", "turn_index": 24})
        return await summarize()

    monkeypatch.setattr(compaction_summarizer, "summarize", changed)
    with pytest.raises(ContextIntegrityError, match="新消息"):
        asyncio.run(pipeline.compact_session_async(session=session, llm_runtime=config,
            messages=[{"role": "system", "content": "root"}, *session.messages], turn_index=24, max_output_tokens=1000))
    assert session.messages[-1]["content"] == "new inbox"


def test_deployment_probe_preserves_multi_turn_tool_and_thought_history(monkeypatch):
    from app.cli.context_probe import run_probe
    seen = []

    async def stream(self, config, messages, tools):
        seen.append(copy.deepcopy(messages))
        if messages[-1]["role"] == "user":
            expected = messages[-1]["content"].split("value 为 ")[1].split("，再")[0]
            yield {"choices": [{"delta": {"reasoning_content": "think", "tool_calls": [{"index": 0, "id": f"c{len(seen)}", "function": {"name": "echo_probe", "arguments": json.dumps({"value": expected})}}]}, "finish_reason": "tool_calls"}]}
        else:
            expected = json.loads(messages[-1]["content"])["value"]
            yield {"choices": [{"delta": {"content": expected}, "finish_reason": "stop"}]}

    monkeypatch.setattr(LlmClient, "stream_chat_completion", stream)
    result = asyncio.run(run_probe(make_runtime_config(), turns=3))
    assert result["passed"]
    assert len(seen) == 6
    assert seen[1][-2]["reasoning_content"] == "think"


def test_run_resume_route_replays_gap_and_filters_live_duplicates(monkeypatch):
    from app.api.routes import agent as route
    from app.schemas.agent import AgentEvent
    session = AgentSession("resume")
    monkeypatch.setattr(route, "_ensure_session_access", lambda *args: session)
    monkeypatch.setattr(route.store_service, "get_agent_run", lambda run_id: {"session_id": "resume"})
    queued = []

    def event(seq, kind="content_delta"):
        return AgentEvent(type=kind, run_id="run", session_id="resume", seq=seq, payload={"delta": str(seq)})

    async def subscribe(run_id, after_seq):
        assert after_seq == 1
        queue = asyncio.Queue()
        await queue.put(event(2))
        await queue.put(event(3, "completed"))
        queued.append(queue)
        return queue, True

    async def unsubscribe(*args):
        return None

    monkeypatch.setattr(route.agent_run_service, "subscribe_from", subscribe)
    monkeypatch.setattr(route.agent_run_service, "unsubscribe", unsubscribe)
    monkeypatch.setattr(route.agent_run_service, "list_events_after", lambda *args: [event(2)])

    async def collect():
        response = await route.stream_run_events("resume", "run", auth=None, after_seq=1)
        return [frame async for frame in response.body_iterator]

    frames = asyncio.run(collect())
    assert sum("id: 2\n" in frame for frame in frames) == 1
    assert "id: 3\n" in frames[-1]
    assert len(queued) == 1


def test_run_resume_route_rejects_other_session(monkeypatch):
    from app.api.routes import agent as route
    from fastapi import HTTPException
    monkeypatch.setattr(route, "_ensure_session_access", lambda *args: AgentSession("own"))
    monkeypatch.setattr(route.store_service, "get_agent_run", lambda run_id: {"session_id": "other"})
    with pytest.raises(HTTPException) as error:
        asyncio.run(route.stream_run_events("own", "run", auth=None, after_seq=0))
    assert error.value.status_code == 404


def test_checkpoint_transcript_expands_only_current_branch_sources(tmp_path):
    from app.services.transcript_service import transcript_service
    initialize_store(tmp_path)
    first = {"role": "user", "message_id": "first", "content": "original 192.0.2.10"}
    discarded = {"role": "user", "message_id": "discarded", "content": "old branch"}
    history_store.capture("branch", {"messages": [first, discarded]})
    checkpoint = {"role": "user", "message_id": "checkpoint", "content": "summary", "visible_in_transcript": False,
                  "compression_meta": {"source_message_ids": ["first"]}}
    history_store.capture("branch", {"messages": [checkpoint]})
    newest = {"role": "assistant", "message_id": "newest", "content": "new answer"}
    session = AgentSession("branch", messages=[checkpoint, newest])
    items = transcript_service.build_session_transcript(session)
    assert [item["id"] for item in items] == ["first", "newest"]
    assert items[0]["content"] == "original 192.0.2.10"


def test_v2_load_creates_idempotent_backup_and_restores_only_unambiguous_thought(monkeypatch, tmp_path):
    from app.services.session_service import SessionService
    initialize_store(tmp_path)
    path = tmp_path / "session.json"
    payload = {"session_id": "legacy", "workspace_id": "workspace", "message_schema_version": 2,
        "messages": [{"role": "assistant", "message_id": "a", "content": "answer", "blocks": [
            {"kind": "reasoning", "content": "thought"}, {"kind": "content", "content": "answer"}]}]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = SessionService()
    monkeypatch.setattr(service, "_metadata_path", lambda session_id: path)
    first = service._load_from_disk("legacy")
    second = service._load_from_disk("legacy")
    assert first.message_schema_version == second.message_schema_version == 3
    assert first.messages[0]["reasoning_content"] == "thought"
    backups = list(tmp_path.glob("session.pre-context-v3.*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == payload


def test_twelve_checkpoint_generations_keep_original_sources_without_summary_duplication(tmp_path):
    initialize_store(tmp_path)
    working = [{"role": "user", "message_id": "origin", "content": "IP 192.0.2.10"}]
    expected = ["origin"]
    for index in range(12):
        history_store.capture("long", {"messages": working})
        checkpoint = context_message_adapter.make_summary_message(content="summary", turn_index=index,
            compression_meta={"source_message_ids": [item["message_id"] for item in working]})
        user = {"role": "user", "message_id": f"user-{index}", "content": f"turn {index}"}
        working = [checkpoint, user]
        expected.append(user["message_id"])
    expanded = history_store.expand_history("long", working)
    assert [item["message_id"] for item in expanded] == expected
    assert expanded[0]["content"] == "IP 192.0.2.10"


def test_tool_name_fragments_are_assembled_before_execution(monkeypatch, tmp_path):
    from app.services.tool_result_service import ToolExecutionResult
    initialize_store(tmp_path)
    calls = []
    rounds = []

    async def stream(config, messages, tools, **kwargs):
        rounds.append(messages)
        if len(rounds) == 1:
            yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "fragmented", "function": {"name": "sandbox_", "arguments": '{"command":'}}]}}]}
            yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "shell", "arguments": '"echo ok"}'}}]}, "finish_reason": "tool_calls"}]}
        else:
            yield {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}

    async def execute(session, name, arguments):
        calls.append((name, arguments))
        return ToolExecutionResult.success("tool.done", "done")

    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", stream)
    monkeypatch.setattr("app.runtime.engine.tool_service.execute", execute)
    events = asyncio.run(collect_stream(build_session("fragmented_name"), "run echo"))
    assert calls == [("sandbox_shell", {"command": "echo ok"})]
    assert any(item["type"] == "result" and item["payload"]["subtype"] == "success" for item in events)


@pytest.mark.parametrize("effort", [0, 101, True])
def test_invalid_numeric_effort_is_rejected(effort):
    with pytest.raises(ValueError):
        LlmConfigUpdateRequest(base_url="http://local/v1", model="alias", sampling={"reasoning_effort": effort})


def test_xhigh_effort_is_sent_unchanged_and_anonymous_endpoint_has_no_fake_auth():
    config = replace(make_runtime_config(), api_key="", sampling={"reasoning_effort": "xhigh"})
    payload = LlmClient()._payload(config, [], [], stream=True)
    assert payload["reasoning_effort"] == "xhigh"
    assert "Authorization" not in LlmClient()._headers(config)
