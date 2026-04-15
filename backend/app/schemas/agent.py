# backend/app/schemas/agent.py
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentChatRequest(BaseModel):
    """Agent 对话请求。"""

    message: str
    session_id: str | None = None


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
        "message",
        "result",
        "completed",
        "error",
    ]
    session_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)


class SkillCard(BaseModel):
    """技能卡片。"""

    name: str
    description: str
    source: Literal["built_in", "host", "upload"]
    allowed_tools: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
