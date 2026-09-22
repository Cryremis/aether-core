from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class ScheduleTargetMode(str, Enum):
    EXISTING_SESSION = "existing_session"
    NEW_SESSION_PER_RUN = "new_session_per_run"


class ScheduleStatus(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    EXPIRED = "expired"
    ARCHIVED = "archived"


class ScheduleRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ScheduleConcurrencyPolicy(str, Enum):
    SKIP = "skip"
    QUEUE = "queue"


class ScheduleMissedRunPolicy(str, Enum):
    SKIP = "skip"
    RUN_ONCE = "run_once"
    CATCH_UP = "catch_up"


class IntervalSchedule(BaseModel):
    type: Literal["interval"]
    every_seconds: int = Field(ge=60, le=31_536_000)
    anchor_at: datetime | None = None


class DailySchedule(BaseModel):
    type: Literal["daily"]
    time: str = Field(pattern=r"^([01]\d|2[0-3]):([0-5]\d)$")


class WorkdaySchedule(BaseModel):
    type: Literal["workday"]
    time: str = Field(pattern=r"^([01]\d|2[0-3]):([0-5]\d)$")


class WeeklySchedule(BaseModel):
    type: Literal["weekly"]
    weekdays: list[int] = Field(min_length=1, max_length=7)
    time: str = Field(pattern=r"^([01]\d|2[0-3]):([0-5]\d)$")


class CronSchedule(BaseModel):
    type: Literal["cron"]
    expression: str = Field(min_length=9, max_length=120)


ScheduleSpec = Annotated[
    IntervalSchedule
    | DailySchedule
    | WorkdaySchedule
    | WeeklySchedule
    | CronSchedule,
    Field(discriminator="type"),
]


class ScheduleTargetRequest(BaseModel):
    mode: ScheduleTargetMode = ScheduleTargetMode.EXISTING_SESSION
    session_id: str | None = None
    new_session_title_prefix: str | None = Field(default=None, max_length=80)
    workspace_policy: Literal["isolated", "shared_with_parent"] = "isolated"


class ScheduleExecutionPolicy(BaseModel):
    timeout_seconds: int = Field(default=900, ge=30, le=7200)
    concurrency_policy: ScheduleConcurrencyPolicy = ScheduleConcurrencyPolicy.SKIP
    missed_run_policy: ScheduleMissedRunPolicy = ScheduleMissedRunPolicy.SKIP
    max_runs: int | None = Field(default=None, ge=1, le=10_000)


class ScheduleCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=32_768)
    target: ScheduleTargetRequest
    schedule: ScheduleSpec
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    execution: ScheduleExecutionPolicy = Field(default_factory=ScheduleExecutionPolicy)


class ScheduleUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=128)
    prompt: str | None = Field(default=None, min_length=1, max_length=32_768)
    target: ScheduleTargetRequest | None = None
    schedule: ScheduleSpec | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    execution: ScheduleExecutionPolicy | None = None


class ScheduleTaskView(BaseModel):
    task_id: str
    title: str
    prompt: str
    status: ScheduleStatus
    target_mode: ScheduleTargetMode
    target_session_id: str | None
    target_conversation_id: str | None
    new_session_title_prefix: str | None
    workspace_policy: str
    schedule: ScheduleSpec
    timezone: str
    starts_at: datetime | None
    ends_at: datetime | None
    next_run_at: datetime | None
    execution: ScheduleExecutionPolicy
    run_count: int
    max_runs: int | None
    last_run_at: datetime | None
    last_successful_run_at: datetime | None
    last_failure_reason: str | None
    consecutive_failure_count: int
    created_via: str
    created_session_id: str | None
    revision: int
    created_at: datetime
    updated_at: datetime
    next_runs: list[datetime] = Field(default_factory=list)


class ScheduleRunView(BaseModel):
    run_id: str
    task_id: str
    trigger_at: datetime
    scheduled_for: datetime
    status: ScheduleRunStatus
    session_id: str | None
    conversation_id: str | None
    agent_run_id: str | None
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    error: str | None
    result_summary: str | None
    created_at: datetime
    updated_at: datetime


class ScheduleListResponse(BaseModel):
    items: list[ScheduleTaskView]
    total: int
