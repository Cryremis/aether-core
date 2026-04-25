from __future__ import annotations

import threading
import time
from pathlib import Path

from app.core.config import settings
from app.services.runtime_state import runtime_state_service
from app.services.runtime_state.models import WorkItem, WorkboardState
from app.services.runtime_state.store import runtime_state_store
from app.services.session_service import session_service


def initialize_session(tmp_path: Path, session_id: str = "sess_runtime_state"):
    settings.storage_root = tmp_path / "storage"
    session_service._sessions.clear()
    session = session_service.get_or_create(session_id)
    return session


def test_workboard_is_persisted_and_restored(tmp_path):
    session = initialize_session(tmp_path, "sess_workboard")
    updated = runtime_state_service.update_workboard(
        session,
        {
            "items": [
                {"title": "Inspect runtime", "status": "completed", "priority": "high"},
                {"title": "Render dock", "status": "in_progress"},
            ]
        },
    )

    assert updated.revision >= 1
    assert len(updated.items) == 2
    assert updated.items[0].status == "completed"

    reloaded = session_service.get_or_create(session.session_id)
    restored = runtime_state_service.get_workboard(reloaded)
    assert restored.revision == updated.revision
    assert [item.title for item in restored.items] == ["Inspect runtime", "Render dock"]


def test_elicitation_round_trip_generates_resume_message(tmp_path):
    session = initialize_session(tmp_path, "sess_elicitation")
    request = runtime_state_service.request_user_input(
        session,
        {
            "title": "Choose deployment mode",
            "questions": [
                {
                    "id": "deploy_mode",
                    "header": "Mode",
                    "question": "Which deployment mode should be used?",
                    "options": [
                        {"label": "Single tenant"},
                        {"label": "Multi tenant"},
                    ],
                    "allow_notes": True,
                }
            ],
        },
    )

    state, resume_message = runtime_state_service.resolve_elicitation(
        session,
        request.id,
        [
            {
                "question_id": "deploy_mode",
                "selected_options": ["Multi tenant"],
                "notes": "Need isolated quotas later",
            }
        ],
    )

    assert state.pending is None
    assert state.history[-1].status == "resolved"
    assert "Choose deployment mode" in resume_message
    assert "Multi tenant" in resume_message


def test_workboard_ops_support_add_update_remove_reorder(tmp_path):
    session = initialize_session(tmp_path, "sess_workboard_ops")
    runtime_state_service.update_workboard(
        session,
        {
            "ops": [
                {"op": "add_item", "id": "a", "title": "First task", "status": "pending"},
                {"op": "add_item", "id": "b", "title": "Second task", "status": "pending"},
                {"op": "update_item", "id": "a", "status": "in_progress", "notes": "working"},
                {"op": "reorder_items", "ordered_ids": ["b", "a"]},
                {"op": "remove_item", "id": "b"},
            ]
        },
    )

    workboard = runtime_state_service.get_workboard(session)
    assert [item.id for item in workboard.items] == ["a"]
    assert workboard.items[0].status == "in_progress"
    assert workboard.items[0].notes == "working"


def test_workboard_updates_are_atomic_under_concurrent_writes(tmp_path, monkeypatch):
    session = initialize_session(tmp_path, "sess_workboard_atomic")
    runtime_state_service.update_workboard(
        session,
        {
            "ops": [
                {"op": "add_item", "id": "a", "title": "Task A", "status": "pending"},
                {"op": "add_item", "id": "b", "title": "Task B", "status": "pending"},
            ]
        },
    )

    original_apply_ops = runtime_state_service._apply_workboard_ops

    def delayed_apply_ops(current_items, operations, now):
        if any(op.get("id") == "a" and op.get("op") == "update_item" for op in operations):
            time.sleep(0.12)
        return original_apply_ops(current_items, operations, now)

    monkeypatch.setattr(runtime_state_service, "_apply_workboard_ops", delayed_apply_ops)

    first_done = threading.Event()

    def update_a():
        runtime_state_service.update_workboard(
            session,
            {"ops": [{"op": "update_item", "id": "a", "title": "Task A edited"}]},
        )
        first_done.set()

    def update_b():
        time.sleep(0.02)
        runtime_state_service.update_workboard(
            session,
            {"ops": [{"op": "update_item", "id": "b", "title": "Task B edited"}]},
        )

    thread_a = threading.Thread(target=update_a)
    thread_b = threading.Thread(target=update_b)
    thread_a.start()
    thread_b.start()
    thread_a.join()
    thread_b.join()

    assert first_done.is_set()

    workboard = runtime_state_service.get_workboard(session)
    assert {item.id: item.title for item in workboard.items} == {
      "a": "Task A edited",
      "b": "Task B edited",
    }


def test_workboard_get_normalizes_duplicate_item_ids(tmp_path):
    session = initialize_session(tmp_path, "sess_workboard_normalize")
    runtime_state_store.save(
        session,
        "workboard",
        WorkboardState(
            session_id=session.session_id,
            items=[
                WorkItem(id="dup", title="Task 1", status="pending"),
                WorkItem(id="dup", title="Task 2", status="pending"),
            ],
        ),
    )

    workboard = runtime_state_service.get_workboard(session)
    assert [item.id for item in workboard.items] == ["dup", "dup_2"]
    assert [item.title for item in workboard.items] == ["Task 1", "Task 2"]


def test_replace_all_assigns_unique_ids_for_duplicates(tmp_path):
    session = initialize_session(tmp_path, "sess_workboard_replace_all_unique")
    workboard = runtime_state_service.update_workboard(
        session,
        {
            "items": [
                {"id": "same", "title": "Task 1", "status": "pending"},
                {"id": "same", "title": "Task 2", "status": "pending"},
                {"title": "Task 3", "status": "pending"},
            ]
        },
    )

    ids = [item.id for item in workboard.items]
    assert ids[0] == "same"
    assert ids[1] == "same_2"
    assert ids[2].startswith("work_")
    assert len(set(ids)) == 3


def test_workboard_title_update_keeps_active_form_in_sync(tmp_path):
    session = initialize_session(tmp_path, "sess_workboard_title_sync")
    runtime_state_service.update_workboard(
        session,
        {
            "ops": [
                {
                    "op": "add_item",
                    "id": "task_1",
                    "title": "Old title",
                    "active_form": "Old title",
                    "status": "pending",
                }
            ]
        },
    )

    workboard = runtime_state_service.update_workboard(
        session,
        {
            "ops": [
                {
                    "op": "update_item",
                    "id": "task_1",
                    "title": "New title",
                }
            ]
        },
    )

    assert workboard.items[0].title == "New title"
    assert workboard.items[0].active_form == "New title"
