# backend/app/schemas/host.py
from typing import Any, Literal

from pydantic import BaseModel, Field


class HostAuthDescriptor(BaseModel):
    """宿主用户认证凭证。"""

    token: str | None = None
    token_header: str = "Authorization"
    token_prefix: str = "Bearer"
    custom_headers: dict[str, str] = Field(default_factory=dict)
    refresh_token: str | None = None
    refresh_endpoint: str | None = None
    expires_at: float | None = None


class HostToolDescriptor(BaseModel):
    """宿主注入的工具描述。"""

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    endpoint: str
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    requires_auth: bool = True
    auth_inject: bool = True


class HostSkillDescriptor(BaseModel):
    """宿主注入的技能描述。"""

    name: str
    description: str
    content: str
    allowed_tools: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class HostSystemPromptDescriptor(BaseModel):
    """宿主动态注入的系统提示词描述。"""

    key: str
    content: str
    enabled: bool = True


class HostApiDescriptor(BaseModel):
    """宿主注入的 API 描述。"""

    name: str
    description: str
    base_url: str
    headers: dict[str, str] = Field(default_factory=dict)


class HostContextDescriptor(BaseModel):
    """宿主注入的上下文。"""

    user: dict[str, Any] = Field(default_factory=dict)
    page: dict[str, Any] = Field(default_factory=dict)
    extras: dict[str, Any] = Field(default_factory=dict)
    auth: HostAuthDescriptor | None = None


class HostBindRequest(BaseModel):
    """宿主绑定请求。"""

    platform_key: str
    host_name: str
    session_id: str | None = None
    conversation_id: str | None = None
    conversation_key: str | None = None
    context: HostContextDescriptor = Field(default_factory=HostContextDescriptor)
    tools: list[HostToolDescriptor] = Field(default_factory=list)
    skills: list[HostSkillDescriptor] = Field(default_factory=list)
    system_prompts: list[HostSystemPromptDescriptor] = Field(default_factory=list)
    apis: list[HostApiDescriptor] = Field(default_factory=list)


class HostBindingSummary(BaseModel):
    """宿主绑定摘要。"""

    host_name: str
    session_id: str
    tool_count: int
    skill_count: int
    api_count: int
