# backend/app/schemas/agent.py
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentChatRequest(BaseModel):
    """Agent 对话请求。"""

    message: str
    session_id: str | None = None
    allow_network: bool | None = None


class ElicitationResponseItem(BaseModel):
    question_id: str
    selected_options: list[str] = Field(default_factory=list)
    other_text: str | None = None
    notes: str | None = None


class AgentElicitationResponseRequest(BaseModel):
    responses: list[ElicitationResponseItem] = Field(default_factory=list)

class AgentEvent(BaseModel):
    """SSE 事件协议。"""

    type: Literal[
        "session_created",
        "reasoning_delta",
        "reasoning_completed",
        "content_delta",
        "content_completed",
        "tool_call_delta",
        "tool_call_completed",
        "tool_started",
        "tool_progress",
        "tool_finished",
        "artifact_created",
        "runtime_created",
        "runtime_recreated",
        "workboard_snapshot",
        "workboard_updated",
        "elicitation_snapshot",
        "ask_requested",
        "ask_resolved",
        "ask_cancelled",
        "context_status",
        "context_warning",
        "context_compacted",
        "context_recovered",
        "context_blocked",
        "message",
        "result",
        "completed",
        "error",
        "aborted",
    ]
    session_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)


class SkillCard(BaseModel):
    """技能卡片。"""

    name: str
    description: str
    source: Literal["built_in", "host", "platform", "upload"]
    allowed_tools: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
