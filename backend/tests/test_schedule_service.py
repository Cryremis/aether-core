from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.config import settings
from app.schemas.schedule import ScheduleCreateRequest
from app.services.scheduling.errors import SchedulePermissionError
from app.services.scheduling.models import ScheduleIdentity
from app.services.scheduling.scheduler import ScheduleScheduler
from app.services.scheduling.service import schedule_service
from app.services.store import store_service


def initialize_store(tmp_path: Path) -> None:
    settings.storage_root = tmp_path / "storage"
    store_service._db_path = settings.metadata_db_path  # noqa: SLF001
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    store_service.initialize()


def create_request(**overrides):
    payload = {
        "title": "每日进展",
        "prompt": "总结当前进展",
        "target": {"mode": "new_session_per_run"},
        "schedule": {"type": "interval", "every_seconds": 60},
        "timezone": "UTC",
    }
    payload.update(overrides)
    return ScheduleCreateRequest.model_validate(payload)


def test_agent_created_schedule_requires_approval(tmp_path) -> None:
    initialize_store(tmp_path)
    settings.agent_schedules_require_approval = True

    task = schedule_service.create(
        create_request(),
        identity=ScheduleIdentity(owner_user_id=1),
        created_via="agent",
        created_session_id="sess_agent",
    )

    assert task.status.value == "pending_approval"
    approved = schedule_service.approve(
        task.task_id, identity=ScheduleIdentity(owner_user_id=1)
    )
    assert approved.status.value == "active"
    assert approved.next_run_at is not None


def test_ui_created_schedule_starts_active(tmp_path) -> None:
    initialize_store(tmp_path)
    settings.agent_schedules_require_approval = True

    task = schedule_service.create(
        create_request(),
        identity=ScheduleIdentity(owner_user_id=1),
        created_via="ui",
    )

    assert task.status.value == "active"


def test_other_identity_cannot_access_schedule(tmp_path) -> None:
    initialize_store(tmp_path)
    task = schedule_service.create(
        create_request(),
        identity=ScheduleIdentity(owner_user_id=1),
        created_via="ui",
    )

    with pytest.raises(SchedulePermissionError):
        schedule_service.get(task.task_id, identity=ScheduleIdentity(owner_user_id=2))


def test_scheduler_materializes_each_occurrence_once(tmp_path) -> None:
    initialize_store(tmp_path)
    task = schedule_service.create(
        create_request(),
        identity=ScheduleIdentity(owner_user_id=1),
        created_via="ui",
    )
    due_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    store_service.update_schedule_task(
        task.task_id,
        {"next_run_at": due_at.isoformat()},
        expected_revision=int(store_service.get_schedule_task(task.task_id)["revision"]),
    )
    scheduler = ScheduleScheduler()

    assert asyncio.run(scheduler.tick_once()) == 1
    assert asyncio.run(scheduler.tick_once()) == 0

    updated = store_service.get_schedule_task(task.task_id)
    assert updated is not None
    assert updated["run_count"] == 1
    assert datetime.fromisoformat(updated["next_run_at"]) > due_at
    runs = store_service.list_schedule_task_runs(task.task_id)
    assert len(runs) == 1
    assert runs[0]["status"] == "queued"


def test_run_claim_and_terminal_state_are_persisted(tmp_path) -> None:
    initialize_store(tmp_path)
    task = schedule_service.create(
        create_request(),
        identity=ScheduleIdentity(owner_user_id=1),
        created_via="ui",
    )
    due_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    store_service.update_schedule_task(
        task.task_id,
        {"next_run_at": due_at.isoformat()},
        expected_revision=int(store_service.get_schedule_task(task.task_id)["revision"]),
    )
    scheduler = ScheduleScheduler()
    asyncio.run(scheduler.tick_once())
    queued = store_service.list_schedule_task_runs(task.task_id)[0]

    claimed = store_service.claim_next_schedule_run(
        lease_owner="test-worker",
        lease_expires_at=(datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(),
    )
    assert claimed is not None
    assert claimed["run_id"] == queued["run_id"]
    assert claimed["status"] == "running"
    assert store_service.claim_next_schedule_run(
        lease_owner="test-worker-2",
        lease_expires_at=(datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(),
    ) is None

    finished = store_service.finish_schedule_run(
        str(queued["run_id"]),
        status="succeeded",
        session_id="sess_run",
        conversation_id="conv_run",
        agent_run_id="agent_run",
        result_summary="完成",
    )
    assert finished is not None
    assert finished["status"] == "succeeded"
    assert finished["duration_ms"] is not None
    updated = store_service.get_schedule_task(task.task_id)
    assert updated["last_successful_run_at"] is not None
    assert updated["consecutive_failure_count"] == 0


def test_pause_resume_and_archive_lifecycle(tmp_path) -> None:
    initialize_store(tmp_path)
    task = schedule_service.create(
        create_request(),
        identity=ScheduleIdentity(owner_user_id=1),
        created_via="ui",
    )

    paused = schedule_service.pause(
        task.task_id, identity=ScheduleIdentity(owner_user_id=1)
    )
    assert paused.status.value == "paused"
    resumed = schedule_service.resume(
        task.task_id, identity=ScheduleIdentity(owner_user_id=1)
    )
    assert resumed.status.value == "active"
    archived = schedule_service.archive(
        task.task_id, identity=ScheduleIdentity(owner_user_id=1)
    )
    assert archived.status.value == "archived"
