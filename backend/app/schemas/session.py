# backend/app/schemas/session.py
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.agent import SkillCard
from app.schemas.files import FileRecord


class SessionSummary(BaseModel):
    """会话摘要，用于工作台初始化与宿主调试。"""

    session_id: str
    conversation_id: str | None = None
    title: str = "新对话"
    host_name: str
    message_count: int
    allow_network: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    skills: list[SkillCard] = Field(default_factory=list)
    files: list[FileRecord] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    context_state: dict[str, Any] = Field(default_factory=dict)
