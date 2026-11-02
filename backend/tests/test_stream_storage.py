from __future__ import annotations

import asyncio
import json

from app.runtime.engine import agent_engine
from app.services.agent_run_service import agent_run_service
from app.services.context.history_store import history_store
from app.services.session_types import AgentSession
from app.services.store import store_service
from test_agent_engine import build_session, initialize_store


def test_chunk_storage_is_linear_and_final_attempt_is_complete(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    session = build_session("linear_stream")
    run_id = "linear_stream_run"
    store_service.create_agent_run(run_id=run_id, session_id=session.session_id)
    arguments = json.dumps({"ops": [{"op": "add_item", "title": "x" * 4000}]})
    chunks = [arguments[i:i + 32] for i in range(0, len(arguments), 32)]
    saves = []
    original_save = history_store.save_attempt
    def save(session_id, attempt_id, response, status="streaming"):
        saves.append((attempt_id, response, status))
        return original_save(session_id, attempt_id, response, status)
    rounds = 0
    async def model_stream(*args, **kwargs):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            for index, fragment in enumerate(chunks):
                yield {"choices": [{"delta": {
                    "content": "a", "reasoning_content": "r",
                    "tool_calls": [{"index": 0, "id": "call" if index == 0 else None,
                                    "function": {"name": "update_workboard" if index == 0 else "",
                                                 "arguments": fragment}}],
                }, "finish_reason": None}]}
            yield {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
        else:
            yield {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}
    monkeypatch.setattr(history_store, "save_attempt", save)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", model_stream)
    async def run():
        async for event in agent_engine.stream_chat(session, "perform", run_id=run_id):
            await agent_run_service._publish(run_id, event)
    asyncio.run(run())
    rows = store_service.list_agent_run_events(run_id, limit=10000)
    deltas = [row["payload"] for row in rows if row["type"] == "tool_call_delta"]
    assert len(deltas) == len(chunks)
    assert all("arguments" not in payload for payload in deltas)
    assert "".join(payload["arguments_delta"] for payload in deltas) == arguments
    journal = history_store._streamed_attempt_response(run_id, deltas[0]["attempt_id"])
    assert journal["tool_calls"]["0"]["arguments"] == arguments
    assert journal["content"] == "a" * len(chunks)
    assert sum(len(json.dumps(payload)) for payload in deltas) < len(arguments) * 40
    # Snapshot writes scale with attempts, not token/chunk count.
    assert len(saves) <= 4
    response = next(response for _, response, status in saves if response.get("tool_calls") and status == "completed")
    assert response["tool_calls"][0]["arguments"] == arguments
    assert response["content"] == "a" * len(chunks)
    assert response["reasoning_content"] == "r" * len(chunks)


def test_crashed_attempt_recovers_journal_without_executing_partial_tool(tmp_path):
    initialize_store(tmp_path)
    session = AgentSession("journal_recovery")
    for run_id in ("crashed", "unrelated"):
        store_service.create_agent_run(run_id=run_id, session_id=run_id, status="failed")
    history_store.start_attempt(session.session_id, "attempt", {
        "run_id": "crashed", "turn_index": 1, "api_format": "openai-compatible",
    })
    def append(run_id, event_type, **payload):
        store_service.append_agent_run_event(run_id=run_id, event_type=event_type, payload=payload)
    append("unrelated", "content_delta", attempt_id="attempt", delta="wrong run")
    append("crashed", "content_delta", attempt_id="other", delta="wrong attempt")
    for text in ("前半", "后半"):
        append("crashed", "content_delta", attempt_id="attempt", delta=text)
    append("crashed", "reasoning_delta", attempt_id="attempt", delta="recover thought")
    append("crashed", "tool_call_delta", attempt_id="attempt", index=0, id="call", tool_name="danger",
           arguments_delta='{"unfinished":')
    assert history_store.recover_attempts(session)
    recovered = session.messages[0]
    assert recovered["content"] == "前半后半"
    assert recovered["reasoning_content"] == "recover thought"
    assert not recovered.get("tool_calls")
    assert recovered["status"] == "incomplete"
    assert not history_store.recover_attempts(session)


def test_completed_snapshot_remains_authoritative_over_journal(tmp_path):
    initialize_store(tmp_path)
    session = AgentSession("completed_snapshot")
    store_service.create_agent_run(run_id="complete", session_id=session.session_id, status="completed")
    history_store.start_attempt(session.session_id, "attempt", {"run_id": "complete", "api_format": "openai-compatible"})
    store_service.append_agent_run_event(run_id="complete", event_type="content_delta",
                                        payload={"attempt_id": "attempt", "delta": "partial"})
    history_store.save_attempt(session.session_id, "attempt", {"content": "complete final response"}, "completed")
    assert history_store.recover_attempts(session)
    assert session.messages[0]["content"] == "complete final response"
