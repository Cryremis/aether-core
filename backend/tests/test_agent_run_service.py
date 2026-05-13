from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.config import settings
from app.runtime.event_protocol import make_event
from app.services.agent_run_service import agent_run_service
from app.services.session_types import AgentSession
from app.services.store import store_service


def initialize_store(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    store_service._db_path = storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.storage_root = storage_root
    store_service.initialize()


def test_agent_run_service_finalizes_active_run_view_into_transcript(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    session = AgentSession(session_id="sess_run_finalize")
    session.messages.append(
        {
            "role": "user",
            "content": "hello",
            "message_id": "m_user_1",
            "turn_index": 1,
            "visible_in_transcript": True,
        }
    )
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
    assert session.transcript[1]["id"] == "live-run_test_1"
    assert session.transcript[1]["blocks"][0]["content"] == "world"
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
