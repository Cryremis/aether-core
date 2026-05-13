from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class PlatformCreateRequest(BaseModel):
    platform_key: str
    display_name: str
    host_type: Literal["embedded", "standalone"] = "embedded"
    description: str = ""
    owner_user_id: int | None = None


class PlatformBaselineFile(BaseModel):
    name: str
    relative_path: str
    section: Literal["skills", "work", "logs"]
    size: int
    media_type: str


class PlatformBaselineEntry(BaseModel):
    name: str
    relative_path: str
    section: Literal["skills", "work", "logs"]
    kind: Literal["file", "directory"]
    size: int = 0
    media_type: str = ""


class PlatformBaselineFileContent(BaseModel):
    relative_path: str
    media_type: str
    content: str
    truncated: bool = False


class PlatformBaselineWriteRequest(BaseModel):
    relative_path: str
    content: str


class PlatformBaselineDirectoryRequest(BaseModel):
    relative_path: str


class PlatformBaselineMoveRequest(BaseModel):
    source_relative_path: str
    target_relative_path: str


class PlatformBaselineSkill(BaseModel):
    name: str
    description: str
    allowed_tools: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    relative_path: str


class PlatformBaselineSummary(BaseModel):
    platform_key: str
    files: list[PlatformBaselineFile] = Field(default_factory=list)
    entries: list[PlatformBaselineEntry] = Field(default_factory=list)
    skills: list[PlatformBaselineSkill] = Field(default_factory=list)


class PlatformSummary(BaseModel):
    platform_id: int
    platform_key: str
    display_name: str
    host_type: str
    description: str = ""
    owner_user_id: int
    owner_name: str
    host_secret: str
    sandbox_image: str | None = None
    resolved_sandbox_image: str = ""
    sandbox_image_updated_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    admin_user_ids: list[int] = Field(default_factory=list)
    admin_names: list[str] = Field(default_factory=list)


class PlatformRuntimeImageUpdateRequest(BaseModel):
    image: str


class PlatformRuntimeImageSummary(BaseModel):
    platform_id: int
    custom_image: str | None = None
    resolved_image: str
    updated_at: datetime | None = Field(default_factory=lambda: datetime.now(timezone.utc))


class PlatformRuntimeImageBuildSpec(BaseModel):
    target_os: str
    target_arch: str
    image_format: str
    shell: str
    recommended_base: str
    entrypoint: str
    expected_workspace_root: str
    required_directories: list[str] = Field(default_factory=list)
    required_env_vars: list[str] = Field(default_factory=list)
    resource_limits: list[str] = Field(default_factory=list)
    build_steps: list[str] = Field(default_factory=list)
    sample_dockerfile: str
    notes: list[str] = Field(default_factory=list)


class PlatformRuntimeImageGuide(BaseModel):
    platform_id: int
    display_name: str
    current_image: str
    build_spec: PlatformRuntimeImageBuildSpec


class PlatformIntegrationGuideSnippets(BaseModel):
    frontend: str
    backend_env: str
    backend_fastapi: str


class PlatformIntegrationPlaceholder(BaseModel):
    key: str
    label: str
    value: str
    required: bool = True
    description: str = ""


class PlatformIntegrationSnippet(BaseModel):
    snippet_id: str
    title: str
    language: str
    summary: str = ""
    content: str


class PlatformIntegrationMode(BaseModel):
    mode_id: str
    title: str
    summary: str
    access_stage: Literal["quick", "production"] = "production"
    identity_scenario: Literal["authenticated_user", "browser_guest", "ephemeral"] = "authenticated_user"
    use_when: str = ""
    recommended: bool = False
    backend_requirement: str = ""
    identity_requirement: str = ""
    capabilities: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    snippets: list[PlatformIntegrationSnippet] = Field(default_factory=list)


class PlatformIntegrationGuide(BaseModel):
    platform_key: str
    display_name: str
    bind_api_path: str
    frontend_script_path: str
    frontend_script_url: str
    recommended_mode_id: str = "standard_bind_hosted"
    prerequisites: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    placeholders: list[PlatformIntegrationPlaceholder] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    modes: list[PlatformIntegrationMode] = Field(default_factory=list)
    snippets: PlatformIntegrationGuideSnippets


class PlatformAdminAssignRequest(BaseModel):
    user_id: int


class PlatformAdminRecord(BaseModel):
    user_id: int
    full_name: str
    email: str | None = None
    role: str
    assigned_at: datetime | None = None
    is_primary: bool = False


class PlatformRegistrationRequestCreateRequest(BaseModel):
    platform_key: str
    display_name: str
    description: str = ""
    justification: str = ""


class PlatformRegistrationReviewRequest(BaseModel):
    review_comment: str = ""


class PlatformRegistrationRequestSummary(BaseModel):
    request_id: int
    applicant_user_id: int
    applicant_name: str
    applicant_email: str | None = None
    platform_key: str
    display_name: str
    description: str = ""
    justification: str = ""
    status: Literal["pending", "approved", "rejected", "returned", "cancelled"]
    review_comment: str = ""
    reviewed_by: int | None = None
    reviewed_by_name: str | None = None
    reviewed_at: datetime | None = None
    approved_platform_id: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EmbedBootstrapRequest(BaseModel):
    platform_key: str
    external_user_id: str
    external_user_name: str
    external_org_id: str | None = None
    conversation_id: str | None = None
    conversation_key: str | None = None
    host_name: str | None = None


class EmbedBootstrapResponse(BaseModel):
    conversation_id: str
    session_id: str
    embed_token: str
    host_name: str


class ConversationSummary(BaseModel):
    conversation_id: str
    session_id: str
    title: str
    host_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_message_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message_count: int = 0
