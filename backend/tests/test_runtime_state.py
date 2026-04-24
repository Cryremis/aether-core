from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.services.runtime_state import runtime_state_service
from app.services.session_service import session_service


def initialize_session(tmp_path: Path, session_id: str = "sess_runtime_state"):
    settings.storage_root = tmp_path / "storage"
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
