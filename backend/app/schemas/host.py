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

    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    description: str = Field(min_length=1, max_length=16_384)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    endpoint: str = Field(min_length=1, max_length=2048)
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
    session_id: str | None = Field(default=None, min_length=1, description="已有会话 ID；不存在、已删除或无权访问时拒绝。新建会话请省略。")
    conversation_id: str | None = Field(default=None, min_length=1, description="已有对话 ID；与 session_id 同时提供时必须对应同一对话。")
    conversation_key: str | None = None
    visibility: Literal["normal", "hidden"] = "normal"
    context: HostContextDescriptor = Field(default_factory=HostContextDescriptor)
    tools: list[HostToolDescriptor] = Field(default_factory=list)
    skills: list[HostSkillDescriptor] = Field(default_factory=list)
    system_prompts: list[HostSystemPromptDescriptor] = Field(default_factory=list)
    apis: list[HostApiDescriptor] = Field(default_factory=list)
    tool_source_id: str = Field(default="host:bind", min_length=1, max_length=128)
    tool_update_mode: Literal[
        "replace_all",
        "replace_source",
        "replace_all_if_source_missing",
    ] = "replace_all"
    tool_refresh_policy: Literal["static_run", "round_boundary"] = "static_run"


class HostConversationCreateRequest(BaseModel):
    """宿主控制面创建会话请求。"""

    external_user_id: str = Field(min_length=1, max_length=256)
    external_user_name: str = Field(default="Host User", max_length=256)
    external_org_id: str | None = Field(default=None, max_length=256)
    conversation_key: str | None = Field(default=None, max_length=256)
    idempotency_key: str | None = Field(default=None, max_length=256)
    title: str = Field(default="新对话", max_length=200)
    host_name: str = Field(default="Host Platform", max_length=200)
    visibility: Literal["normal", "hidden"] = "normal"
    context: HostContextDescriptor = Field(default_factory=HostContextDescriptor)
    tools: list[HostToolDescriptor] = Field(default_factory=list, max_length=256)
    skills: list[HostSkillDescriptor] = Field(default_factory=list)
    system_prompts: list[HostSystemPromptDescriptor] = Field(default_factory=list)
    apis: list[HostApiDescriptor] = Field(default_factory=list)


class HostConversationPatchRequest(BaseModel):
    """宿主控制面更新会话请求。"""

    expected_revision: int | None = Field(default=None, ge=0)
    title: str | None = Field(default=None, max_length=200)
    visibility: Literal["normal", "hidden"] | None = None
    pinned: bool | None = None
    archived: bool | None = None
    metadata: dict[str, Any] | None = None


class HostMessageRequest(BaseModel):
    """宿主控制面发送消息请求。"""

    message: str = Field(min_length=1)
    allow_network: bool | None = None
    client_message_id: str | None = Field(default=None, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=256)
    reasoning_effort: str | None = None
    response_mode: Literal["stream", "poll"] = "poll"


class HostAssistantMessage(BaseModel):
    """带运行状态的 AI 回复投影。"""

    message_id: str
    run_id: str | None = None
    agent_id: str | None = None
    subagent_id: str | None = None
    content: str
    run_status: str | None = None
    created_at: str


class HostToolCatalogReplaceRequest(BaseModel):
    """Atomically replace all tools owned by one source, or the complete host catalog."""

    source_id: str = Field(default="host:api", min_length=1, max_length=128)
    replace_all: bool = False
    expected_revision: int | None = Field(default=None, ge=0)
    tools: list[HostToolDescriptor] = Field(default_factory=list, max_length=256)


class HostToolUpsertOperation(BaseModel):
    op: Literal["upsert"]
    tool: HostToolDescriptor


class HostToolRemoveOperation(BaseModel):
    op: Literal["remove"]
    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


class HostToolCatalogPatchRequest(BaseModel):
    """Apply an atomic set of source-owned tool upserts and removals."""

    source_id: str = Field(default="host:api", min_length=1, max_length=128)
    expected_revision: int | None = Field(default=None, ge=0)
    operations: list[HostToolUpsertOperation | HostToolRemoveOperation] = Field(
        min_length=1,
        max_length=256,
    )


class HostBindingSummary(BaseModel):
    """宿主绑定摘要。"""

    host_name: str
    session_id: str
    workspace_id: str
    tool_count: int
    skill_count: int
    api_count: int


class HostWorkspaceMember(BaseModel):
    """Workspace 成员投影。"""

    session_id: str
    conversation_id: str | None = None
    role: str
    parent_session_id: str | None = None
    title: str | None = None
    visibility: str | None = None
    joined_at: str
    last_active_at: str


class HostWorkspaceSummary(BaseModel):
    """宿主可见的共享 Workspace 摘要。"""

    workspace_id: str
    owner_session_id: str
    owner_conversation_id: str | None = None
    status: str
    revision: int
    baseline_root: str
    created_at: str
    updated_at: str
    members: list[HostWorkspaceMember] = Field(default_factory=list)
    runtime: dict[str, Any] | None = None
