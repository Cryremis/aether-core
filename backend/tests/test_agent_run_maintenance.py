from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import settings
from app.services.agent_run_service import agent_run_service
from app.services.runtime_state import runtime_state_service
from app.services.session_service import session_service
from app.services.store import store_service


def initialize_isolated_runtime(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    store_service._db_path = storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    session_service._sessions.clear()
    settings.storage_root = storage_root
    store_service.initialize()


def test_expire_waiting_input_run_releases_session_and_records_timeout(tmp_path):
    initialize_isolated_runtime(tmp_path)
    session = session_service.get_or_create("sess_waiting_timeout")
    runtime_state_service.request_user_input(
        session,
        {
            "title": "等待确认",
            "blocking": True,
            "questions": [
                {
                    "id": "question",
                    "header": "确认",
                    "question": "是否继续执行？",
                    "options": [{"label": "继续"}, {"label": "取消"}],
                }
            ],
        },
    )
    run = store_service.create_agent_run(
        run_id="run_waiting_timeout",
        session_id=session.session_id,
        status="waiting_input",
    )
    store_service.append_agent_run_event(
        run_id=run["run_id"],
        event_type="completed",
        payload={"subtype": "awaiting_user_input"},
        status="waiting_input",
    )
    expired_at = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    with store_service._connect() as conn:
        conn.execute(
            "UPDATE run_events SET created_at = ? WHERE run_id = ?",
            (expired_at, run["run_id"]),
        )

    expired_count = agent_run_service.expire_waiting_inputs_once(timeout_seconds=1)

    assert expired_count == 1
    assert store_service.get_agent_run(run["run_id"])["status"] == "cancelled"
    assert store_service.get_active_agent_run_for_session(session.session_id) is None
    assert runtime_state_service.get_elicitation(session).pending is None
    timeout_context = session.messages[-1]
    assert timeout_context["kind"] == "elicitation_timeout"
    assert timeout_context["visible_in_transcript"] is False
    assert "是否继续执行？" in timeout_context["content"]
    events = store_service.list_agent_run_events(run["run_id"])
    assert events[-2]["type"] == "aborted"
    assert events[-1]["type"] == "completed"
    assert events[-1]["payload"]["subtype"] == "timeout"


def test_waiting_input_sweeper_task_can_start_and_stop(tmp_path):
    initialize_isolated_runtime(tmp_path)

    async def start_and_stop():
        await agent_run_service.start_background_tasks()
        task = agent_run_service._waiting_input_sweeper_task
        assert task is not None and not task.done()
        await agent_run_service.stop_background_tasks()
        assert agent_run_service._waiting_input_sweeper_task is None
        assert task.done()

    asyncio.run(start_and_stop())
