from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from app.core.config import settings
from app.services.context.message_adapter import context_message_adapter
from app.services.context.runtime_types import ContextOverflowError
from app.services.context.summarizer import compaction_summarizer
from app.services.llm_client import llm_client
from app.services.provider.limit_discovery import ModelLimitDiscovery
from app.services.session_types import AgentSession
from test_context_compaction import make_pipeline
from test_agent_engine import build_session, collect_stream, initialize_store
from test_llm_client import make_runtime_config


def runtime(**sampling):
    return replace(make_runtime_config(), sampling={"context_window": 10_000, "max_output_tokens": 1_000, **sampling})


def test_prepare_and_wire_check_use_same_calibrated_estimate():
    pipeline = make_pipeline()
    session = AgentSession("calibrated")
    session.context_state["token_calibration_factor"] = 0.5
    session.messages = [context_message_adapter.make_user_message("a" * 22_000, turn_index=1)]
    config = replace(runtime(), token_calibration_factor=0.5)
    prepared = asyncio.run(pipeline.prepare_for_llm_async(
        session=session, llm_runtime=config, messages=session.messages, turn_index=1))
    payload = llm_client._payload(config, prepared.messages, [], stream=True)
    assert llm_client.estimate_payload_tokens(config, payload) > 5_000
    assert prepared.state.last_known_token_estimate < prepared.state.warning_threshold == 4_250
    assert prepared.state.blocking_limit == 5_000
    with pytest.raises(ContextOverflowError) as error:
        llm_client._payload(replace(config, token_calibration_factor=1), prepared.messages, [], stream=True)
    assert error.value.public_payload()["context_window"] == 10_000


def test_one_large_new_message_keeps_full_content_when_recent_window_must_shrink(monkeypatch):
    pipeline = make_pipeline()
    session = AgentSession("recent")
    session.messages = [
        context_message_adapter.make_user_message("a" * 10_000, turn_index=1),
        context_message_adapter.make_user_message("b" * 7_000, turn_index=2),
        context_message_adapter.make_user_message("current " * 900, turn_index=3),
    ]
    latest = session.messages[-1]["content"]

    async def unavailable(*args):
        raise RuntimeError("摘要暂不可用")

    monkeypatch.setattr(compaction_summarizer, "summarize", unavailable)
    prepared = asyncio.run(pipeline.prepare_for_llm_async(
        session=session, llm_runtime=runtime(), messages=session.messages, turn_index=3))
    assert prepared.compacted
    assert prepared.plan.estimated_tokens_after < prepared.plan.estimated_tokens_before
    assert prepared.messages[-1]["content"] == latest
    assert any(item.get("is_boundary_marker") and item["compression_meta"]["source_message_ids"] for item in session.messages)
    llm_client._payload(runtime(), prepared.messages, [], stream=True)


def test_pinned_input_over_budget_is_rejected_without_summarizing(monkeypatch):
    pipeline = make_pipeline()
    session = AgentSession("pinned")
    session.messages = [context_message_adapter.make_user_message("长" * 8_000, turn_index=1)]

    async def unexpected(*args):
        pytest.fail("不可压缩的输入不应发起摘要请求")

    monkeypatch.setattr(compaction_summarizer, "summarize", unexpected)
    with pytest.raises(ContextOverflowError) as error:
        asyncio.run(pipeline.prepare_for_llm_async(
            session=session, llm_runtime=runtime(), messages=session.messages, turn_index=1))
    assert error.value.public_payload()["reason"] == "pinned_over_budget"
    assert session.messages[0]["content"] == "长" * 8_000


def test_engine_reports_oversized_message_without_calling_provider(tmp_path, monkeypatch):
    initialize_store(tmp_path)
    config = runtime()
    monkeypatch.setattr("app.runtime.engine.llm_config_service.resolve_for_conversation", lambda _: config)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda _: [])

    async def unexpected(*args, **kwargs):
        pytest.fail("不可压缩的输入不应发送给模型")
        yield

    monkeypatch.setattr(llm_client, "stream_chat_completion", unexpected)
    session = build_session("oversized_input")
    message = "长" * 8_000
    events = asyncio.run(collect_stream(session, message))
    blocked = next(item["payload"] for item in events if item["type"] == "context_blocked")
    assert blocked["reason"] == "pinned_over_budget"
    assert blocked["estimated_tokens"] > blocked["effective_window"] == 5_000
    assert events[-1]["payload"]["subtype"] == "error_context_overflow"
    assert any(item["content"] == message for item in session.messages if item["role"] == "user")


def test_soft_only_request_with_no_compressible_history_continues_with_cooldown():
    pipeline = make_pipeline()
    session = AgentSession("cooldown")
    session.messages = [context_message_adapter.make_user_message("a" * 13_500, turn_index=1)]
    args = dict(session=session, llm_runtime=runtime(), messages=session.messages, turn_index=1)
    first = asyncio.run(pipeline.prepare_for_llm_async(**args))
    second = asyncio.run(pipeline.prepare_for_llm_async(**args))
    assert first.state.warning_threshold <= first.state.last_known_token_estimate <= first.state.blocking_limit
    assert first.state.last_compaction_attempt_at == second.state.last_compaction_attempt_at
    assert first.state.last_compaction_attempt_at
    assert not first.compacted and not second.compacted


def test_endpoint_limits_are_cached_and_explicit_limits_win(monkeypatch):
    monkeypatch.setattr(settings, "llm_model_discovery_enabled", True)
    calls = []
    original_client = httpx.AsyncClient

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [
            {"id": "other", "max_model_len": 128_000},
            {"id": "private-alias", "max_model_len": 64_000, "max_output_tokens": 4_000},
        ]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(respond)))
    config = replace(make_runtime_config(), base_url="http://local.test/v1/chat/completions", model="private-alias", sampling={})
    discovery = ModelLimitDiscovery()
    for _ in range(2):
        resolved = asyncio.run(discovery.resolve(config, {"Authorization": "Bearer test"}))
        assert llm_client.context_limits(resolved) == (64_000, "endpoint_metadata")
        assert llm_client.output_limit(resolved) == 4_000
    explicit = replace(resolved, sampling={"context_window": 32_000, "max_output_tokens": 2_000})
    assert llm_client.context_limits(explicit) == (32_000, "deployment")
    assert llm_client.output_limit(explicit) == 2_000
    assert len(calls) == 1
    assert calls[0].url.path == "/v1/models"
