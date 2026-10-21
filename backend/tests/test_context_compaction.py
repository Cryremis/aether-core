from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.core.config import settings
from app.runtime.engine import agent_engine
from app.services.context.context_budget import ContextBudget, ContextBudgetConfig
from app.services.context.context_pipeline import ContextPipeline, context_pipeline
from app.services.context.context_state_store import ContextStateStore
from app.services.context.runtime_types import (
    CompactionPlan,
    CompactionStrategyName,
    ContextEventType,
    PreparedContext,
)
from app.services.session_service import session_service
from app.services.session_types import AgentSession
from app.services.store import store_service
from app.services.tool_service import tool_service


@dataclass
class FakeLlmRuntime:
    model: str = "test-model"


class FakeModelsRegistry:
    def get_context_window(self, model: str, betas: list[str] | None = None) -> int:
        return 10_000


class NullPersister:
    def persist(self, session: AgentSession) -> None:
        return None


def initialize_store(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    store_service._db_path = storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.storage_root = storage_root
    store_service.initialize()


def build_session(session_id: str) -> AgentSession:
    return session_service.get_or_create(session_id)


def make_pipeline() -> ContextPipeline:
    pipeline = ContextPipeline(
        budget_config=ContextBudgetConfig(provider_safety_tokens=4_000),
        state_store=ContextStateStore(persister=NullPersister()),
        persister=NullPersister(),
    )
    pipeline.budget.models_registry = FakeModelsRegistry()
    return pipeline


def seed_history(session: AgentSession, turns: int = 8, *, old_tool_chars: int = 8_000) -> None:
    for turn in range(1, turns + 1):
        is_old = turn <= turns - 4 or turns <= 4
        session.messages.extend(
            [
                {"role": "user", "content": f"user turn {turn}", "turn_index": turn},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": f"call_{turn}",
                            "type": "function",
                            "function": {"name": "sandbox_shell", "arguments": '{"command":"echo ok"}'},
                        }
                    ],
                    "turn_index": turn,
                },
                {
                    "role": "tool",
                    "tool_call_id": f"call_{turn}",
                    "tool_name": "sandbox_shell",
                    "content": "x" * (old_tool_chars if is_old else 80),
                    "turn_index": turn,
                },
                {"role": "assistant", "content": f"assistant turn {turn}", "turn_index": turn},
            ]
        )


def test_context_budget_uses_three_stage_ratios_and_real_output_reservation():
    budget = ContextBudget(
        config=ContextBudgetConfig(provider_safety_tokens=4_000),
        models_registry=FakeModelsRegistry(),
    )

    assert budget.get_effective_context_window("test-model", max_output_tokens=1_000) == 5_000
    assert budget.get_warning_threshold("test-model", max_output_tokens=1_000) == 3_750
    assert budget.get_hard_limit("test-model", max_output_tokens=1_000) == 4_750
    assert budget.get_compact_target("test-model", max_output_tokens=1_000) == 3_250


def test_warning_is_agent_only_and_does_not_force_compaction():
    pipeline = make_pipeline()
    session = AgentSession("sess_context_warning")
    session.messages = [{"role": "user", "content": "a" * 12_000, "turn_index": 1}]
    raw_messages = [{"role": "system", "content": "security rules"}, *session.messages]
    original_count = len(session.messages)

    prepared = pipeline.prepare_for_llm(
        session=session,
        llm_runtime=FakeLlmRuntime(),
        messages=raw_messages,
        turn_index=2,
        max_output_tokens=1_000,
    )

    assert prepared.ephemeral_warning is True
    assert prepared.compacted is False
    assert len(session.messages) == original_count
    assert "[runtime context]" in str(prepared.messages[-1]["content"])
    assert "[runtime context]" not in str(session.messages[-1]["content"])
    assert [event.type for event in prepared.events] == [ContextEventType.Status]


def test_hard_limit_forces_progressive_compaction_and_preserves_critical_context():
    pipeline = make_pipeline()
    session = AgentSession("sess_context_hard_limit")
    seed_history(session)
    session.messages.append({"role": "user", "content": "current instruction", "turn_index": 9})
    raw_messages = [{"role": "system", "content": "security rules"}, *session.messages]

    prepared = pipeline.prepare_for_llm(
        session=session,
        llm_runtime=FakeLlmRuntime(),
        messages=raw_messages,
        turn_index=10,
        max_output_tokens=1_000,
    )

    assert prepared.compacted is True
    assert prepared.plan is not None
    assert prepared.plan.estimated_tokens_after < prepared.state.blocking_limit
    assert any(message.get("is_compact_summary") for message in session.messages)
    assert any(
        message.get("role") == "user" and message.get("content") == "current instruction"
        for message in session.messages
    )
    assert prepared.events[0].type == ContextEventType.Compacted
    assert prepared.events[0].payload["cause"] == "hard_limit"
    call_ids = {
        tool_call["id"]
        for message in session.messages
        if message.get("role") == "assistant"
        for tool_call in message.get("tool_calls", [])
    }
    result_ids = {
        message["tool_call_id"]
        for message in session.messages
        if message.get("role") == "tool" and message.get("tool_call_id")
    }
    assert result_ids.issubset(call_ids)


def test_api_usage_calibrates_local_token_estimate():
    pipeline = make_pipeline()
    session = AgentSession("sess_context_calibration")
    session.messages = [{"role": "user", "content": "a" * 12_000, "turn_index": 1}]
    raw_messages = [{"role": "system", "content": "security rules"}, *session.messages]
    prepared = pipeline.prepare_for_llm(
        session=session,
        llm_runtime=FakeLlmRuntime(),
        messages=raw_messages,
        turn_index=2,
        max_output_tokens=1_000,
    )
    local_estimate = prepared.state.last_known_token_estimate

    pipeline.update_api_usage(session, {"prompt_tokens": int(local_estimate * 1.5)})
    calibrated = pipeline.state_store.get(session)

    assert prepared.state.token_calibration_factor == 1.0
    assert calibrated.token_calibration_factor == pytest.approx(1.5, rel=0.001)
    assert calibrated.last_known_token_estimate == int(local_estimate * 1.5)


def test_context_compact_schema_is_hidden_until_warning(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    session = build_session("sess_context_tool_schema")

    names = {
        schema["function"]["name"]
        for schema in tool_service.list_tool_schemas(session)
    }
    assert "context_compact" not in names
    assert tool_service.get_context_compact_schema()["function"]["name"] == "context_compact"


def test_context_compact_accounts_for_active_tool_schemas(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    session = build_session("sess_context_compact_tool_budget")
    active_tool = {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": "Search documents",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    captured: dict[str, Any] = {}

    def fake_compact_session(**kwargs: Any) -> PreparedContext:
        captured["tools"] = kwargs["tools"]
        plan = CompactionPlan(
            strategy=CompactionStrategyName.ToolResultTruncate,
            estimated_tokens_before=1_000,
            estimated_tokens_after=600,
            estimated_tokens_saved=400,
            messages_affected=1,
            reason="test",
        )
        return PreparedContext(
            messages=[],
            state=context_pipeline.state_store.get(session),
            plan=plan,
        )

    monkeypatch.setattr(tool_service, "list_tool_schemas", lambda current_session: [active_tool])
    monkeypatch.setattr(context_pipeline, "compact_session", fake_compact_session)

    result = asyncio.run(tool_service._handle_context_compact(session, {"strategy": "balanced"}))

    assert result.status == "success"
    assert active_tool in captured["tools"]
    assert tool_service.get_context_compact_schema() in captured["tools"]


async def collect_stream(session: AgentSession, message: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for event in agent_engine.stream_chat(session, message):
        events.append(event.model_dump(mode="json"))
    return events


def test_engine_exposes_context_tool_only_in_warning_and_hides_warning_from_user(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    monkeypatch.setattr(settings, "llm_max_tokens", 1_000)
    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1_800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr(
        "app.runtime.engine.context_pipeline.budget.get_context_window_for_model",
        lambda model, betas=None: 10_000,
    )
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
    monkeypatch.setattr(
        agent_engine,
        "_build_system_messages",
        lambda *args, **kwargs: [{"role": "system", "content": "security rules"}],
    )
    observed: dict[str, Any] = {}

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        observed["tool_names"] = {item["function"]["name"] for item in tools}
        observed["message_tail"] = messages[-1]
        yield {"choices": [{"delta": {"content": "continued in warning zone"}, "finish_reason": "stop"}]}

    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)

    session = build_session("sess_engine_context_warning")
    events = asyncio.run(collect_stream(session, "a" * 12_000))

    assert "context_compact" in observed["tool_names"]
    assert "[runtime context]" in str(observed["message_tail"]["content"])
    assert "[runtime context]" not in str(session.messages[-1]["content"])
    assert all(event["type"] != "context_warning" for event in events)
    assert any(event["type"] == "result" and event["payload"]["subtype"] == "success" for event in events)


def test_agent_can_call_context_compact_and_run_continues(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    monkeypatch.setattr(settings, "llm_max_tokens", 1_000)
    monkeypatch.setattr(settings, "agent_max_turns", 0)
    monkeypatch.setattr(settings, "agent_max_runtime_seconds", 1_800)
    monkeypatch.setattr(settings, "agent_max_stall_rounds", 0)
    monkeypatch.setattr(
        "app.runtime.engine.context_pipeline.budget.get_context_window_for_model",
        lambda model, betas=None: 10_000,
    )
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
    monkeypatch.setattr(
        agent_engine,
        "_build_system_messages",
        lambda *args, **kwargs: [{"role": "system", "content": "security rules"}],
    )
    round_index = {"value": 0}
    observed_tool_names: list[set[str]] = []

    async def fake_stream_chat_completion(config, messages, tools, **kwargs) -> AsyncGenerator[dict, None]:
        observed_tool_names.append({item["function"]["name"] for item in tools})
        current = round_index["value"]
        round_index["value"] += 1
        if current == 0:
            yield {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_context_compact",
                                    "function": {
                                        "name": "context_compact",
                                        "arguments": '{"strategy":"balanced","reason":"test"}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
            return
        yield {"choices": [{"delta": {"content": "finished after compaction"}, "finish_reason": "stop"}]}

    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", fake_stream_chat_completion)

    session = build_session("sess_engine_agent_compact")
    seed_history(session, turns=4, old_tool_chars=2_800)
    events = asyncio.run(collect_stream(session, "continue"))

    assert "context_compact" in observed_tool_names[0]
    assert "context_compact" not in observed_tool_names[-1]
    assert any(event["type"] == "context_compacted" for event in events)
    assert any(message.get("is_compact_summary") for message in session.messages)
    assert any(event["type"] == "result" and event["payload"]["subtype"] == "success" for event in events)
