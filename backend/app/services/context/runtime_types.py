# backend/app/services/context/runtime_types.py
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


CONTEXT_MESSAGE_SCHEMA_VERSION = 2


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_message_id(prefix: str = "msg") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class ContextEventType(str, Enum):
    Status = "context_status"
    Warning = "context_warning"
    Compacted = "context_compacted"
    Recovered = "context_recovered"
    Blocked = "context_blocked"


class CompactionStrategyName(str, Enum):
    NoOp = "noop"
    ToolResultTruncate = "tool_result_truncate"
    MicroCompact = "micro_compact"
    TranscriptSummary = "transcript_summary"
    ReactiveOverflow = "reactive_overflow"
    HardFail = "hard_fail"


@dataclass
class ContextSessionState:
    session_id: str
    model_id: str = ""
    context_window: int = 200_000
    effective_window: int = 180_000
    target_input_tokens: int = 144_000
    warning_threshold: int = 147_000
    error_threshold: int = 147_000
    blocking_limit: int = 177_000
    last_known_token_estimate: int = 0
    percent_used: float = 0.0
    last_api_usage: dict[str, int] = field(default_factory=dict)
    compaction_count: int = 0
    consecutive_compaction_failures: int = 0
    last_compaction_at: str | None = None
    last_compaction_strategy: str | None = None
    last_boundary_message_id: str | None = None
    reactive_retry_count: int = 0
    version: int = 1

    @classmethod
    def from_dict(cls, session_id: str, payload: dict[str, Any] | None) -> "ContextSessionState":
        if not payload:
            return cls(session_id=session_id)
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        data = {key: value for key, value in payload.items() if key in allowed}
        data["session_id"] = session_id
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "model_id": self.model_id,
            "context_window": self.context_window,
            "effective_window": self.effective_window,
            "target_input_tokens": self.target_input_tokens,
            "warning_threshold": self.warning_threshold,
            "error_threshold": self.error_threshold,
            "blocking_limit": self.blocking_limit,
            "last_known_token_estimate": self.last_known_token_estimate,
            "percent_used": self.percent_used,
            "last_api_usage": dict(self.last_api_usage),
            "compaction_count": self.compaction_count,
            "consecutive_compaction_failures": self.consecutive_compaction_failures,
            "last_compaction_at": self.last_compaction_at,
            "last_compaction_strategy": self.last_compaction_strategy,
            "last_boundary_message_id": self.last_boundary_message_id,
            "reactive_retry_count": self.reactive_retry_count,
            "version": self.version,
        }


@dataclass(frozen=True)
class ContextEvent:
    type: ContextEventType
    payload: dict[str, Any]


@dataclass
class CompactionPlan:
    strategy: CompactionStrategyName
    estimated_tokens_before: int
    estimated_tokens_after: int
    estimated_tokens_saved: int
    messages_affected: int
    reason: str
    integrity_risk: str = "low"
    reversible: bool = True
    requires_llm_summary: bool = False


@dataclass
class PreparedContext:
    messages: list[dict[str, Any]]
    state: ContextSessionState
    events: list[ContextEvent] = field(default_factory=list)
    compacted: bool = False
    plan: CompactionPlan | None = None


class ContextOverflowError(RuntimeError):
    def __init__(self, message: str, *, state: ContextSessionState, token_estimate: int):
        super().__init__(message)
        self.state = state
        self.token_estimate = token_estimate

