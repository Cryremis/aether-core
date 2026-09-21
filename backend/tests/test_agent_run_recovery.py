from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import settings
from app.services.session_service import SessionService
from app.services.session_types import AgentSession
from app.services.store import store_service


def initialize_store(tmp_path: Path) -> None:
    settings.storage_root = tmp_path / "storage"
    store_service._db_path = settings.metadata_db_path  # noqa: SLF001
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    store_service.initialize()


def test_recovery_marks_interrupted_agent_runs_failed(tmp_path):
    initialize_store(tmp_path)
    run = store_service.create_agent_run(
        run_id="run_interrupted",
        session_id="sess_recovery",
        status="running",
        request={"message": "hello"},
    )

    assert run["status"] == "running"

    recovered_count = store_service.recover_interrupted_agent_runs()

    assert recovered_count == 1
    recovered = store_service.get_agent_run("run_interrupted")
    assert recovered["status"] == "failed"
    assert recovered["finished_at"] is not None
    events = store_service.list_agent_run_events("run_interrupted")
    assert [event["type"] for event in events] == ["error", "completed"]
    assert store_service.recover_interrupted_agent_runs() == 0
    assert len(store_service.list_agent_run_events("run_interrupted")) == 2


def test_active_database_run_blocks_new_run_with_clear_error(tmp_path):
    initialize_store(tmp_path)
    store_service.create_agent_run(
        run_id="run_active",
        session_id="sess_conflict",
        status="running",
    )

    with pytest.raises(RuntimeError, match="当前会话已有执行中的任务"):
        store_service.create_agent_run(
            run_id="run_conflict",
            session_id="sess_conflict",
            status="running",
        )


def test_session_loading_clears_terminal_active_run_view(tmp_path):
    initialize_store(tmp_path)
    store_service.create_agent_run(
        run_id="run_terminal",
        session_id="sess_view_recovery",
        status="running",
    )
    store_service.recover_interrupted_agent_runs()
    session = AgentSession(
        session_id="sess_view_recovery",
        workspace_id="ws_view_recovery",
        active_run_view={"run_id": "run_terminal", "status": "running"},
    )

    SessionService().reconcile_active_run_view(session)

    assert session.active_run_view is None
