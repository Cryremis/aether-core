from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


WorkItemStatus = Literal["pending", "in_progress", "completed", "blocked", "cancelled"]
WorkboardStatus = Literal["idle", "active", "completed", "blocked"]
WorkItemPriority = Literal["low", "medium", "high"]
ElicitationKind = Literal["clarification", "confirmation", "decision", "missing_info", "approval"]
ElicitationStatus = Literal["pending", "resolved", "cancelled", "expired"]


class WorkItem(BaseModel):
    id: str
    title: str
    active_form: str | None = None
    status: WorkItemStatus = "pending"
    priority: WorkItemPriority = "medium"
    owner: str = "assistant"
    depends_on: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    notes: str | None = None
    source: str = "assistant"
    evidence_refs: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    completed_at: str | None = None


class WorkboardState(BaseModel):
    session_id: str
    revision: int = 0
    status: WorkboardStatus = "idle"
    items: list[WorkItem] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now_iso)


class ElicitationOption(BaseModel):
    label: str
    description: str | None = None


class ElicitationQuestion(BaseModel):
    id: str
    header: str
    question: str
    options: list[ElicitationOption] = Field(default_factory=list)
    multi_select: bool = False
    allow_other: bool = True
    allow_notes: bool = False


class ElicitationAnswer(BaseModel):
    question_id: str
    selected_options: list[str] = Field(default_factory=list)
    other_text: str | None = None
    notes: str | None = None
    rendered_answer: str = ""


class ElicitationRequest(BaseModel):
    id: str
    kind: ElicitationKind = "clarification"
    title: str
    blocking: bool = True
    source_agent: str | None = None
    related_work_items: list[str] = Field(default_factory=list)
    questions: list[ElicitationQuestion] = Field(default_factory=list)
    preview_text: str | None = None
    status: ElicitationStatus = "pending"
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    resolved_at: str | None = None
    cancelled_at: str | None = None
    answers: list[ElicitationAnswer] = Field(default_factory=list)


class ElicitationState(BaseModel):
    session_id: str
    revision: int = 0
    pending: ElicitationRequest | None = None
    history: list[ElicitationRequest] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now_iso)

