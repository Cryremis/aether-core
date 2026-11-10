from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.deps import AuthContext, get_auth_context
from app.core.config import settings
from app.main import app
from app.runtime.event_protocol import make_event
from app.schemas.agent import AgentEvent
from app.services.agent_run_service import agent_run_service
from app.services.runtime_state import runtime_state_service
from app.services.session_service import session_service
from app.services.session_types import AgentSession
from app.services.store import store_service


def initialize_runtime(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    settings.storage_root = storage_root
    store_service._db_path = storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    session_service._sessions.clear()
    agent_run_service._runs.clear()
    agent_run_service._session_runs.clear()
    agent_run_service._resume_admissions.clear()
    agent_run_service._lock = asyncio.Lock()
    store_service.initialize()


def create_session(session_id: str):
    store_service.create_conversation(
        session_id=session_id,
        title="Elicitation API",
        host_name="Test",
        owner_user_id=7,
    )
    return AgentSession(session_id=session_id)


def isolate_session(session, monkeypatch) -> None:
    monkeypatch.setattr(
        session_service,
        "get_or_create",
        lambda session_id=None, **kwargs: session,
    )
    monkeypatch.setattr(session_service, "persist", lambda persisted_session: None)
    async def persist_async(persisted_session):
        session_service.persist(persisted_session)
    monkeypatch.setattr(session_service, "persist_async", persist_async)


def request_question(session, *, request_id="question", blocking=True) -> None:
    runtime_state_service.request_user_input(
        session,
        {
            "id": request_id,
            "title": "确认",
            "blocking": blocking,
            "questions": [
                {
                    "id": request_id,
                    "header": "确认",
                    "question": "是否继续？",
                    "options": [{"label": "继续"}],
                }
            ],
        },
    )


def parse_sse_events(response_text: str) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    for line in response_text.splitlines():
        if line.startswith("data: "):
            events.append(AgentEvent.model_validate_json(line[len("data: "):]))
    return events


def test_blocking_resume_replays_only_events_after_resume_point(tmp_path, monkeypatch):
    initialize_runtime(tmp_path)
    session = create_session(f"sess_elicitation_replay_{uuid4().hex}")
    isolate_session(session, monkeypatch)
    request_question(session)

    events = [
        AgentEvent(
            type="elicitation_snapshot",
            session_id=session.session_id,
            run_id="run_replay",
            seq=15,
            payload={"snapshot": {"old": True}},
        ),
        AgentEvent(
            type="completed",
            session_id=session.session_id,
            run_id="run_replay",
            seq=16,
            payload={"subtype": "awaiting_user_input"},
        ),
        AgentEvent(
            type="run_status_changed",
            session_id=session.session_id,
            run_id="run_replay",
            seq=17,
            payload={"reason": "elicitation_resumed"},
        ),
        AgentEvent(
            type="content_delta",
            session_id=session.session_id,
            run_id="run_replay",
            seq=18,
            payload={"delta": "回答后的内容"},
        ),
        AgentEvent(
            type="completed",
            session_id=session.session_id,
            run_id="run_replay",
            seq=19,
            payload={"subtype": "awaiting_user_input"},
        ),
    ]

    async def fake_resume(*args, **kwargs):
        return "run_replay", 17

    def fake_list_events_after(run_id, after_seq):
        assert run_id == "run_replay"
        assert after_seq == 16
        return [event for event in events if event.seq is not None and event.seq > after_seq]

    async def fake_subscribe_from(run_id, after_seq):
        assert run_id == "run_replay"
        assert after_seq == 16
        return asyncio.Queue(), False

    monkeypatch.setattr(agent_run_service, "resume_elicitation_run", fake_resume)
    monkeypatch.setattr(agent_run_service, "list_events_after", fake_list_events_after)
    monkeypatch.setattr(agent_run_service, "subscribe_from", fake_subscribe_from)
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        kind="user",
        user=SimpleNamespace(user_id=7),
    )
    try:
        response = TestClient(app).post(
            f"/api/v1/agent/{session.session_id}/elicitation/question/respond",
            json={"responses": [{"question_id": "question", "selected_options": ["继续"]}]},
        )
    finally:
        app.dependency_overrides.pop(get_auth_context, None)

    assert response.status_code == 200
    received_events = parse_sse_events(response.text)
    received_sequences = [event.seq for event in received_events if event.seq is not None]
    assert 15 not in received_sequences
    assert 16 not in received_sequences
    assert received_sequences == [17, 18, 19]
    assert [event.type for event in received_events if event.seq == 19] == ["completed"]
    assert received_events[0].run_id == "run_replay"
    assert received_events[0].payload["resume_after_seq"] == 16


def test_duplicate_elicitation_response_is_a_no_op(tmp_path, monkeypatch):
    initialize_runtime(tmp_path)
    session = create_session(f"sess_elicitation_duplicate_{uuid4().hex}")
    isolate_session(session, monkeypatch)
    request_question(session)
    state, _ = runtime_state_service.resolve_elicitation(
        session,
        "question",
        [{"question_id": "question", "selected_options": ["继续"]}],
    )
    revision = state.revision

    def fail_resume(*args, **kwargs):
        raise AssertionError("duplicate response must not resume a run")

    monkeypatch.setattr(agent_run_service, "resume_elicitation_run", fail_resume)
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        kind="user",
        user=SimpleNamespace(user_id=7),
    )
    try:
        response = TestClient(app).post(
            f"/api/v1/agent/{session.session_id}/elicitation/question/respond",
            json={"responses": [{"question_id": "question", "selected_options": ["继续"]}]},
        )
    finally:
        app.dependency_overrides.pop(get_auth_context, None)

    assert response.status_code == 200
    events = parse_sse_events(response.text)
    assert [event.type for event in events] == ["session_created", "ask_resolved"]
    ask_resolved = events[1]
    assert ask_resolved.payload["request_id"] == "question"
    assert ask_resolved.payload["snapshot"]["revision"] == revision
    assert runtime_state_service.get_elicitation(session).revision == revision


def test_replay_drains_live_events_committed_after_the_initial_read(tmp_path, monkeypatch):
    initialize_runtime(tmp_path)
    session = create_session("sess_replay_live_tail")
    isolate_session(session, monkeypatch)
    run_id = "run_replay_live_tail"
    store_service.create_agent_run(run_id=run_id, session_id=session.session_id, status="running")
    store_service.append_agent_run_event(run_id=run_id, event_type="run_started", payload={})
    store_service.append_agent_run_event(run_id=run_id, event_type="content_delta", payload={"delta": "live"})
    store_service.append_agent_run_event(
        run_id=run_id, event_type="completed", payload={"subtype": "success"}, status="completed",
    )
    events = agent_run_service.list_events_after(run_id, 0)

    async def subscribe(*args, **kwargs):
        queue = asyncio.Queue()
        for event in events:
            queue.put_nowait(event)
        return queue, True

    monkeypatch.setattr(agent_run_service, "subscribe_from", subscribe)
    monkeypatch.setattr(agent_run_service, "list_events_after", lambda *_args: events[:1])
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(kind="user", user=SimpleNamespace(user_id=7))
    try:
        response = TestClient(app).get(f"/api/v1/agent/{session.session_id}/runs/{run_id}/events")
    finally:
        app.dependency_overrides.pop(get_auth_context, None)
    sequenced = [event for event in parse_sse_events(response.text) if event.seq is not None]
    assert [event.seq for event in sequenced] == [1, 2, 3]
    assert sequenced[-1].payload["subtype"] == "success"


def test_pause_projection_preserves_current_wait_and_real_timeout():
    waiting = {"status": "waiting_input", "event_cursor": 4}
    assert agent_run_service.is_historical_pause("completed", {"subtype": "awaiting_user_input"}, 2, waiting)
    assert not agent_run_service.is_historical_pause("completed", {"subtype": "awaiting_user_input"}, 4, waiting)
    assert agent_run_service.is_historical_pause("completed", {"subtype": "timeout"}, 3, waiting)
    assert not agent_run_service.is_historical_pause(
        "completed", {"subtype": "timeout"}, 4, {"status": "cancelled", "event_cursor": 4},
    )


def test_three_serial_blocking_questions_resume_the_same_run(tmp_path, monkeypatch):
    initialize_runtime(tmp_path)
    session = create_session(f"sess_elicitation_serial_{uuid4().hex}")
    isolate_session(session, monkeypatch)
    run = store_service.create_agent_run(
        run_id="run_elicitation_serial",
        session_id=session.session_id,
        status="waiting_input",
    )
    store_service.append_agent_run_event(
        run_id=run["run_id"],
        event_type="completed",
        payload={"subtype": "awaiting_user_input"},
        status="waiting_input",
    )

    from app.runtime.engine import agent_engine

    engine_calls = 0

    async def fake_stream_chat(stream_session, message, **kwargs):
        nonlocal engine_calls
        engine_calls += 1
        yield make_event(stream_session, "content_delta", round=engine_calls, delta=f"第 {engine_calls} 轮回答")
        if engine_calls < 3:
            yield make_event(
                stream_session,
                "completed",
                elapsed_ms=1,
                subtype="awaiting_user_input",
            )
        else:
            yield make_event(stream_session, "completed", elapsed_ms=1, subtype="success")

    monkeypatch.setattr(agent_engine, "stream_chat", fake_stream_chat)

    resumed_runs: list[str] = []
    resume_sequences: list[int] = []
    for round_index in range(1, 4):
        request_id = f"question_{round_index}"
        runtime_state_service.request_user_input(
            session,
            {
                "id": request_id,
                "title": f"第 {round_index} 个问题",
                "blocking": True,
                "questions": [
                    {
                        "id": request_id,
                        "header": "确认",
                        "question": "是否继续？",
                        "options": [{"label": "继续"}],
                    }
                ],
            },
        )
        _, resume_message = runtime_state_service.resolve_elicitation(
            session,
            request_id,
            [{"question_id": request_id, "selected_options": ["继续"]}],
        )

        async def resume_and_wait():
            run_id, resume_seq = await agent_run_service.resume_elicitation_run(
                session,
                resume_message,
                request_id=request_id,
            )
            await agent_run_service.wait_for_run(run_id)
            return run_id, resume_seq

        run_id, resume_seq = asyncio.run(resume_and_wait())
        resumed_runs.append(run_id)
        resume_sequences.append(resume_seq)

    assert resumed_runs == ["run_elicitation_serial"] * 3
    assert resume_sequences == sorted(resume_sequences)
    assert len(set(resume_sequences)) == 3
    assert engine_calls == 3
    assert store_service.get_agent_run("run_elicitation_serial")["status"] == "completed"
    assert store_service.get_active_agent_run_for_session(session.session_id) is None


def test_answer_replay_crosses_old_pauses_and_concurrent_submit_only_resumes_once(tmp_path, monkeypatch):
    initialize_runtime(tmp_path)
    session = create_session("sess_answer_replay")
    isolate_session(session, monkeypatch)
    request_question(session)
    run_id = "run_answer_replay"
    store_service.create_agent_run(run_id=run_id, session_id=session.session_id, status="waiting_input")
    store_service.append_agent_run_event(
        run_id=run_id, event_type="ask_requested",
        payload={"snapshot": runtime_state_service.get_elicitation(session).model_dump(mode="json")},
    )
    pause = store_service.append_agent_run_event(
        run_id=run_id, event_type="completed", status="waiting_input", payload={"subtype": "awaiting_user_input"},
    )
    from app.runtime.engine import agent_engine

    calls = []

    async def engine(stream_session, message, **kwargs):
        calls.append(kwargs["run_id"])
        await asyncio.sleep(0.02)
        yield make_event(stream_session, "content_delta", delta=f"续跑 {len(calls)}")
        if len(calls) == 1:
            request_question(stream_session, request_id="question-2")
            yield make_event(stream_session, "ask_requested",
                             snapshot=runtime_state_service.get_elicitation(stream_session).model_dump(mode="json"))
            yield make_event(stream_session, "completed", subtype="awaiting_user_input")
        else:
            yield make_event(stream_session, "completed", subtype="success")

    monkeypatch.setattr(agent_engine, "stream_chat", engine)
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(kind="user", user=SimpleNamespace(user_id=7))

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            base = f"/api/v1/agent/{session.session_id}"

            async def answer(question_id):
                response = await client.post(f"{base}/elicitation/{question_id}/respond", json={
                    "responses": [{"question_id": question_id, "selected_options": ["继续"]}],
                })
                assert response.status_code == 200, response.text
                return parse_sse_events(response.text)

            responses = await asyncio.gather(answer("question"), answer("question"))
            for events in responses:
                assert events[0].payload["resume_after_seq"] == pause
                assert [event.payload["delta"] for event in events if event.type == "content_delta"] == ["续跑 1"]
                assert not any(event.type == "error" for event in events)
            committed_ids = [next(event.payload["message"]["id"] for event in events if event.type == "message_committed")
                             for events in responses]
            assert committed_ids[0] == committed_ids[1]
            assert calls == [run_id]
            assert store_service.get_agent_run(run_id)["status"] == "waiting_input"
            await answer("question-2")
            assert calls == [run_id, run_id]

            for cursor in (0, pause, pause + 2):
                replay = await client.get(f"{base}/runs/{run_id}/events", params={"after_seq": cursor})
                events = parse_sse_events(replay.text)
                assert events[1].type == "elicitation_snapshot"
                assert events[1].payload["snapshot"]["pending"] is None
                seqs = [event.seq for event in events if event.seq is not None]
                assert seqs == list(range(cursor + 1, seqs[-1] + 1))
                assert events[-1].payload["subtype"] == "success"
                assert all(event.payload.get("historical_pause") for event in events[:-1] if event.type == "completed")
            again = await answer("question")
            assert again[-1].payload["subtype"] == "success"
            cursor = store_service.get_agent_run(run_id)["event_cursor"]
            current = await client.get(f"{base}/runs/{run_id}/events", params={"after_seq": cursor})
            assert parse_sse_events(current.text)[-1].type == "stream_end"
            assert calls == [run_id, run_id]
            assert len([item for item in session.messages if item.get("request_id") == "question"]) == 1

    try:
        asyncio.run(exercise())
    finally:
        app.dependency_overrides.pop(get_auth_context, None)


def test_late_answer_expires_at_deadline_even_before_background_sweep(tmp_path, monkeypatch):
    initialize_runtime(tmp_path)
    session = create_session("sess_answer_deadline")
    isolate_session(session, monkeypatch)
    request_question(session)
    run_id = "run_answer_deadline"
    store_service.create_agent_run(run_id=run_id, session_id=session.session_id, status="waiting_input")
    store_service.append_agent_run_event(
        run_id=run_id, event_type="completed", status="waiting_input", payload={"subtype": "awaiting_user_input"},
    )
    now = datetime(2026, 11, 10, tzinfo=timezone.utc)
    seconds = settings.agent_waiting_input_timeout_seconds
    with store_service._connect() as conn:
        conn.execute("UPDATE run_events SET created_at = ? WHERE run_id = ?",
                     ((now - timedelta(seconds=seconds)).isoformat(), run_id))

    class FrozenTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr("app.services.store.datetime", FrozenTime)
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(kind="user", user=SimpleNamespace(user_id=7))
    try:
        response = TestClient(app).post(
            f"/api/v1/agent/{session.session_id}/elicitation/question/respond",
            json={"responses": [{"question_id": "question", "selected_options": ["继续"]}]},
        )
    finally:
        app.dependency_overrides.pop(get_auth_context, None)
    assert response.status_code == 409
    assert "发送新消息继续" in response.json()["detail"]
    assert runtime_state_service.get_elicitation(session).pending is None
    assert not any(item.get("request_id") == "question" for item in session.messages)
    assert store_service.get_agent_run(run_id)["status"] == "cancelled"
    assert store_service.list_agent_run_events(run_id)[-1]["payload"]["subtype"] == "timeout"


@pytest.mark.parametrize("interruption", ["abort", "restart_before_pause"])
def test_interrupted_blocking_question_does_not_lock_the_composer(tmp_path, monkeypatch, interruption):
    initialize_runtime(tmp_path)
    session = create_session("sess_interrupted_question")
    isolate_session(session, monkeypatch)
    request_question(session)
    run_id = "run_interrupted_question"
    store_service.create_agent_run(
        run_id=run_id, session_id=session.session_id,
        status="waiting_input" if interruption == "abort" else "running",
    )
    base = f"/api/v1/agent/{session.session_id}"
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(kind="user", user=SimpleNamespace(user_id=7))
    try:
        client = TestClient(app)
        if interruption == "abort":
            assert client.post(f"{base}/abort").status_code == 200
        else:
            assert store_service.recover_interrupted_agent_runs() == 1
        snapshot = client.get(f"{base}/elicitation").json()["data"]
        assert snapshot["pending"] is None
        assert snapshot["history"][-1]["status"] == "cancelled"
        rejected = client.post(f"{base}/elicitation/question/respond", json={"responses": []})
        assert rejected.status_code == 409
        assert "发送新消息继续" in rejected.json()["detail"]
        assert store_service.get_active_agent_run_for_session(session.session_id) is None
    finally:
        app.dependency_overrides.pop(get_auth_context, None)


@pytest.mark.parametrize("recover", [True, False])
def test_blocking_answer_model_read_timeout_preserves_output(tmp_path, monkeypatch, recover):
    initialize_runtime(tmp_path)
    session = session_service.get_or_create(create_session("sess_answer_read_timeout").session_id)
    request_question(session)
    run_id = "run_answer_read_timeout"
    store_service.create_agent_run(run_id=run_id, session_id=session.session_id, status="waiting_input")
    store_service.append_agent_run_event(
        run_id=run_id, event_type="completed", status="waiting_input", payload={"subtype": "awaiting_user_input"},
    )
    calls = 0

    async def stream(config, messages, tools, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {"choices": [{"delta": {"content": "已生成的片段"}, "finish_reason": None}]}
            raise httpx.ReadTimeout("test provider timeout")
        if not recover:
            raise httpx.ReadTimeout("test provider timeout again")
        yield {"choices": [{"delta": {"content": "恢复后的输出"}, "finish_reason": None}]}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    monkeypatch.setattr(settings, "agent_model_retry_attempts", 1)
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", stream)
    monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(kind="user", user=SimpleNamespace(user_id=7))
    try:
        response = TestClient(app).post(
            f"/api/v1/agent/{session.session_id}/elicitation/question/respond",
            json={"responses": [{"question_id": "question", "selected_options": ["继续"]}]},
        )
    finally:
        app.dependency_overrides.pop(get_auth_context, None)
    assert response.status_code == 200
    assert calls == 2
    events = parse_sse_events(response.text)
    assert len([event for event in events if event.type == "stream_retry"]) == 1
    assert any(event.type == "content_delta" and event.payload["delta"] == "已生成的片段" for event in events)
    errors = [event for event in events if event.type == "error"]
    if recover:
        assert not errors
        assert events[-1].payload["subtype"] == "success"
        assert any(event.type == "content_delta" and event.payload["delta"] == "恢复后的输出" for event in events)
    else:
        assert [event.payload["error_code"] for event in errors] == ["MODEL_UNAVAILABLE"]
        assert store_service.get_agent_run(run_id)["status"] == "failed"
        assert any("已生成的片段" in str(item.get("content") or "") for item in session.messages)


def test_nonblocking_busy_response_retains_pending_then_starts_one_continuation(tmp_path, monkeypatch):
    initialize_runtime(tmp_path)
    session = create_session("sess_background_answer")
    isolate_session(session, monkeypatch)
    request_question(session, blocking=False)
    store_service.create_agent_run(run_id="original", session_id=session.session_id, status="running")
    from app.runtime.engine import agent_engine

    calls = []

    async def engine(stream_session, message, **kwargs):
        calls.append(kwargs["run_id"])
        yield make_event(stream_session, "content_delta", delta="后台答案已处理")
        yield make_event(stream_session, "completed", subtype="success")

    monkeypatch.setattr(agent_engine, "stream_chat", engine)
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(kind="user", user=SimpleNamespace(user_id=7))

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            path = f"/api/v1/agent/{session.session_id}/elicitation/question/respond"
            payload = {"responses": [{"question_id": "question", "selected_options": ["继续"]}]}
            busy = await client.post(path, json=payload)
            assert busy.status_code == 409
            assert "答案尚未提交" in busy.json()["detail"]
            assert runtime_state_service.get_elicitation(session).pending.id == "question"
            assert not session.messages
            store_service.append_agent_run_event(
                run_id="original", event_type="completed", status="completed", payload={"subtype": "success"},
            )
            first, duplicate = await asyncio.gather(client.post(path, json=payload), client.post(path, json=payload))
            for response in (first, duplicate):
                assert response.status_code == 200, response.text
                events = parse_sse_events(response.text)
                assert events[0].run_id == calls[0] != "original"
                assert [event.type for event in events if event.seq == 1] == ["run_started"]
                assert events[-1].payload["subtype"] == "success"
            assert len(calls) == 1
            assert runtime_state_service.get_elicitation(session).pending is None

    try:
        asyncio.run(exercise())
    finally:
        app.dependency_overrides.pop(get_auth_context, None)
