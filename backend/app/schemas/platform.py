# backend/app/schemas/platform.py
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class PlatformCreateRequest(BaseModel):
    """平台注册请求。"""

    platform_key: str
    display_name: str
    host_type: Literal["embedded", "standalone"] = "embedded"
    description: str = ""
    owner_user_id: int | None = None


class PlatformBaselineFile(BaseModel):
    """平台基线文件。"""

    name: str
    relative_path: str
    section: Literal["input", "work"]
    size: int
    media_type: str


class PlatformBaselineSkill(BaseModel):
    """平台基线技能。"""

    name: str
    description: str
    allowed_tools: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    relative_path: str


class PlatformBaselineSummary(BaseModel):
    """平台基线环境摘要。"""

    platform_key: str
    files: list[PlatformBaselineFile] = Field(default_factory=list)
    skills: list[PlatformBaselineSkill] = Field(default_factory=list)


class PlatformSummary(BaseModel):
    """平台注册摘要。"""

    platform_id: int
    platform_key: str
    display_name: str
    host_type: str
    description: str = ""
    owner_user_id: int
    owner_name: str
    host_secret: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PlatformAdminAssignRequest(BaseModel):
    """平台管理员授权请求。"""

    user_id: int


class EmbedBootstrapRequest(BaseModel):
    """宿主工作台启动请求。"""

    platform_key: str
    external_user_id: str
    external_user_name: str
    external_org_id: str | None = None
    conversation_id: str | None = None
    conversation_key: str | None = None
    host_name: str | None = None
    host_type: str | None = None


class EmbedBootstrapResponse(BaseModel):
    """宿主工作台启动响应。"""

    conversation_id: str
    session_id: str
    embed_token: str
    host_name: str
    host_type: str


class ConversationSummary(BaseModel):
    """会话列表摘要。"""

    conversation_id: str
    session_id: str
    title: str
    host_name: str
    host_type: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_message_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message_count: int = 0
