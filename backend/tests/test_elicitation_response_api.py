from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

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


def request_question(session) -> None:
    runtime_state_service.request_user_input(
        session,
        {
            "id": "question",
            "title": "确认",
            "blocking": True,
            "questions": [
                {
                    "id": "question",
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
