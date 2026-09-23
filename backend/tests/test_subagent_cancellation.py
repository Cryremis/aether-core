from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from app.core.config import settings
from app.runtime.engine import agent_engine
from app.runtime.event_protocol import make_event
from app.services.agent_run_service import agent_run_service
from app.services.session_service import session_service
from app.services.store import store_service
from app.services.subagent_service import subagent_service


def initialize_isolated_runtime(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    store_service._db_path = storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    session_service._sessions.clear()
    settings.storage_root = storage_root
    store_service.initialize()


def test_legacy_subagent_table_migrates_to_stable_entity_contract(tmp_path):
    initialize_isolated_runtime(tmp_path)
    with sqlite3.connect(store_service._db_path) as conn:
        conn.execute("DROP TABLE subagents")
        conn.execute(
            """
            CREATE TABLE subagent_runs (
                child_run_id TEXT PRIMARY KEY,
                workspace_id TEXT,
                parent_run_id TEXT NOT NULL,
                parent_session_id TEXT NOT NULL,
                child_session_id TEXT NOT NULL UNIQUE,
                latest_run_id TEXT NOT NULL,
                name TEXT NOT NULL,
                task TEXT NOT NULL,
                status TEXT NOT NULL,
                result_text TEXT,
                error_text TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT,
                destroyed_at TEXT,
                destroyed_by TEXT,
                destroy_reason TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO subagent_runs(
                child_run_id, workspace_id, parent_run_id, parent_session_id, child_session_id,
                latest_run_id, name, task, status, created_at, updated_at
            ) VALUES (
                'run_legacy', 'ws_legacy', 'run_parent', 'sess_parent', 'sess_child',
                'run_latest', 'researcher', 'research', 'running', '2026-01-01', '2026-01-01'
            )
            """
        )

    store_service.initialize()

    row = store_service.get_subagent("run_legacy")
    assert row is not None
    assert row["latest_run_id"] == "run_latest"
    assert row["cancel_requested_at"] is None
    with sqlite3.connect(store_service._db_path) as conn:
        tables = {
            str(item[0])
            for item in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
    assert "subagent_runs" not in tables
    assert "subagents" in tables


def test_llm_wait_without_chunks_is_interrupted_by_abort(tmp_path):
    initialize_isolated_runtime(tmp_path)
    session = session_service.get_or_create("sess_llm_abort")
    session.begin_run("run_llm_abort")
    stream_closed = False

    async def blocked_stream():
        try:
            await asyncio.sleep(30)
            yield {"choices": []}
        finally:
            nonlocal stream_closed
            stream_closed = True

    async def consume():
        return [
            chunk
            async for chunk in agent_engine._stream_llm_until_abort(
                session,
                "run_llm_abort",
                blocked_stream(),
            )
        ]

    async def drive():
        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        session.request_abort()
        return await asyncio.wait_for(task, timeout=1)

    assert asyncio.run(drive()) == []
    assert stream_closed is True


def test_cancel_waits_for_terminal_state_and_is_idempotent(tmp_path, monkeypatch):
    initialize_isolated_runtime(tmp_path)
    agent_run_service._runs.clear()
    agent_run_service._session_runs.clear()
    parent = session_service.get_or_create("sess_cancel_parent")
    child = session_service.get_or_create("sess_cancel_child", workspace_id=parent.workspace_id)

    async def blocked_stream_chat(session, message, **kwargs):
        await asyncio.sleep(30)
        yield make_event(session, "completed", subtype="success", elapsed_ms=1)

    original_cancel_run = agent_run_service.cancel_run

    async def fast_cancel_run(session, run_id, **kwargs):
        return await original_cancel_run(session, run_id, grace_seconds=0.1)

    monkeypatch.setattr(agent_engine, "stream_chat", blocked_stream_chat)
    monkeypatch.setattr(agent_run_service, "cancel_run", fast_cancel_run)

    async def drive():
        latest_run_id = await agent_run_service.start_chat_run(
            child,
            "long task",
            agent_kind="subagent",
        )
        row = store_service.create_subagent(
            subagent_id="subagent_cancel",
            latest_run_id=latest_run_id,
            workspace_id=parent.workspace_id,
            parent_run_id="run_cancel_parent",
            parent_session_id=parent.session_id,
            child_session_id=child.session_id,
            name="researcher",
            task="long task",
        )
        cancelled = await subagent_service.cancel(parent, "subagent_cancel")
        repeated = await subagent_service.cancel(parent, "subagent_cancel")
        return latest_run_id, row, cancelled, repeated

    latest_run_id, row, cancelled, repeated = asyncio.run(drive())
    assert row["subagent_id"] == "subagent_cancel"
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_outcome"] == "cancelled"
    assert cancelled["cancel_requested_at"] is not None
    assert repeated["cancel_outcome"] == "already_cancelled"
    assert store_service.get_agent_run(latest_run_id)["status"] == "cancelled"
    assert store_service.get_subagent("subagent_cancel")["status"] == "cancelled"


def test_cancel_reports_already_completed_without_rewrite(tmp_path):
    initialize_isolated_runtime(tmp_path)
    parent = session_service.get_or_create("sess_completed_parent")
    child = session_service.get_or_create("sess_completed_child", workspace_id=parent.workspace_id)
    store_service.create_agent_run(
        run_id="run_completed_child",
        session_id=child.session_id,
        status="completed",
    )
    store_service.create_subagent(
        subagent_id="subagent_completed",
        latest_run_id="run_completed_child",
        workspace_id=parent.workspace_id,
        parent_run_id="run_completed_parent",
        parent_session_id=parent.session_id,
        child_session_id=child.session_id,
        name="researcher",
        task="task",
    )
    store_service.update_subagent("subagent_completed", status="completed", result_text="done")

    cancelled = asyncio.run(subagent_service.cancel(parent, "subagent_completed"))

    assert cancelled["status"] == "completed"
    assert cancelled["cancel_outcome"] == "already_completed"
    assert cancelled["cancel_requested_at"] is None


def test_cancel_converges_orphan_active_run_to_cancelled(tmp_path):
    initialize_isolated_runtime(tmp_path)
    parent = session_service.get_or_create("sess_orphan_parent")
    child = session_service.get_or_create("sess_orphan_child", workspace_id=parent.workspace_id)
    store_service.create_agent_run(
        run_id="run_orphan_child",
        session_id=child.session_id,
        status="running",
    )
    store_service.create_subagent(
        subagent_id="subagent_orphan",
        latest_run_id="run_orphan_child",
        workspace_id=parent.workspace_id,
        parent_run_id="run_orphan_parent",
        parent_session_id=parent.session_id,
        child_session_id=child.session_id,
        name="researcher",
        task="task",
    )

    cancelled = asyncio.run(subagent_service.cancel(parent, "subagent_orphan"))

    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_outcome"] == "cancelled"
    run = store_service.get_agent_run("run_orphan_child")
    assert run["status"] == "cancelled"
    assert [event["type"] for event in store_service.list_agent_run_events("run_orphan_child")] == [
        "aborted",
        "completed",
    ]
