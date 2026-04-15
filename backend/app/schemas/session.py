# backend/app/schemas/session.py
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.schemas.agent import SkillCard
from app.schemas.files import FileRecord


class SessionSummary(BaseModel):
    """会话摘要，用于工作台初始化与宿主调试。"""

    session_id: str
    host_name: str
    host_type: str
    message_count: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    skills: list[SkillCard] = Field(default_factory=list)
    files: list[FileRecord] = Field(default_factory=list)
