from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.config import settings
from app.runtime.event_protocol import make_event
from app.services.agent_run_service import agent_run_service
from app.services.session_service import session_service
from app.services.session_types import AgentSession
from app.services.store import store_service


def initialize_store(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    store_service._db_path = storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.storage_root = storage_root
    store_service.initialize()


def test_agent_run_service_finalizes_transcript_from_persisted_messages_only(tmp_path):
    initialize_store(tmp_path)
    session = AgentSession(session_id="sess_run_finalize")
    session.messages = [
        {
            "role": "user",
            "content": "hello",
            "message_id": "m_user_1",
            "turn_index": 1,
            "visible_in_transcript": True,
        },
        {
            "role": "assistant",
            "content": "world",
            "message_id": "m_assistant_1",
            "turn_index": 1,
            "visible_in_transcript": True,
            "blocks": [
                {"id": "reasoning_1", "kind": "reasoning", "content": "thinking"},
                {"id": "content_1", "kind": "content", "content": "world", "status": "done"},
                {"id": "elapsed_1", "kind": "elapsed", "elapsed_ms": 111},
            ],
        },
    ]
    session.transcript = [{"id": "m_user_1", "role": "user", "content": "hello"}]
    session.active_run_view = {
        "run_id": "run_test_1",
        "session_id": session.session_id,
        "status": "completed",
        "started_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:01+00:00",
        "assistant": {
            "id": "live-run_test_1",
            "role": "assistant",
            "blocks": [
                {"id": "content_1", "kind": "content", "content": "world", "status": "done"},
                {"id": "elapsed_1", "kind": "elapsed", "elapsed_ms": 111},
            ],
            "elapsedMs": 111,
            "streaming": False,
            "response_started_at": "2026-01-01T00:00:00+00:00",
        },
    }

    agent_run_service._finalize_transcript(session)

    assert len(session.transcript) == 2
    assert session.transcript[0]["id"] == "m_user_1"
    assert session.transcript[1]["role"] == "assistant"
    assert session.transcript[1]["id"] == "m_assistant_1"
    assert session.transcript[1]["blocks"][0]["kind"] == "reasoning"
    assert session.transcript[1]["blocks"][1]["content"] == "world"
    assert session.transcript[1]["elapsedMs"] == 111
def test_agent_run_service_drops_stale_transcript_items_not_backed_by_messages(tmp_path):
    initialize_store(tmp_path)
    session = AgentSession(session_id="sess_run_filter")
    session.messages = [
        {
            "role": "user",
            "content": "hello",
            "message_id": "m_user_1",
            "turn_index": 1,
            "visible_in_transcript": True,
        }
    ]
    session.transcript = [
        {"id": "m_user_1", "role": "user", "content": "hello"},
        {"id": "ghost_assistant", "role": "assistant", "blocks": [], "elapsedMs": None, "streaming": False},
    ]

    agent_run_service._finalize_transcript(session)

    assert [item["id"] for item in session.transcript] == ["m_user_1"]


def test_agent_run_service_closes_run_when_finalize_fails(tmp_path, monkeypatch):
    """回归: transcript 收尾失败不能让会话永久停留在执行中。"""
    initialize_store(tmp_path)
    agent_run_service._runs.clear()
    agent_run_service._session_runs.clear()
    session = session_service.get_or_create("sess_finalize_failure")

    from app.runtime.engine import agent_engine

    async def fake_stream_chat(stream_session, message, **kwargs):
        yield make_event(stream_session, "result", subtype="success", is_error=False, result="done")
        yield make_event(stream_session, "completed", subtype="success", elapsed_ms=1)

    def fail_finalize(_session):
        raise RuntimeError("finalize failed")

    monkeypatch.setattr(agent_engine, "stream_chat", fake_stream_chat)
    monkeypatch.setattr(agent_run_service, "_finalize_transcript", fail_finalize)

    async def drive():
        run_id = await agent_run_service.start_chat_run(session, "hello")
        await agent_run_service.wait_for_run(run_id)
        return run_id

    run_id = asyncio.run(drive())

    assert store_service.get_agent_run(run_id)["status"] == "completed"
    assert agent_run_service.get_session_run_id(session.session_id) is None
    assert session.current_run_id() is None
    assert run_id not in agent_run_service._runs


def test_agent_run_service_cancel_run_clears_active_state(tmp_path, monkeypatch):
    """回归: 强制取消必须写终态并清理内存 run，不允许会话被僵尸任务锁死。"""
    initialize_store(tmp_path)
    agent_run_service._runs.clear()
    agent_run_service._session_runs.clear()
    session = session_service.get_or_create("sess_cancel_run")

    from app.runtime.engine import agent_engine

    async def fake_stream_chat(stream_session, message, **kwargs):
        await asyncio.Future()
        if False:
            yield

    monkeypatch.setattr(agent_engine, "stream_chat", fake_stream_chat)

    async def drive():
        run_id = await agent_run_service.start_chat_run(session, "long task")
        await asyncio.sleep(0)
        await agent_run_service.cancel_run(session, run_id, grace_seconds=0.1)
        return run_id

    run_id = asyncio.run(drive())

    assert store_service.get_agent_run(run_id)["status"] == "cancelled"
    assert agent_run_service.get_session_run_id(session.session_id) is None
    assert session.current_run_id() is None
    assert run_id not in agent_run_service._runs
