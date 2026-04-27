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
    section: Literal["input", "skills", "work", "output", "logs"]
    size: int
    media_type: str


class PlatformBaselineEntry(BaseModel):
    name: str
    relative_path: str
    section: Literal["input", "skills", "work", "output", "logs"]
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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    admin_user_ids: list[int] = Field(default_factory=list)
    admin_names: list[str] = Field(default_factory=list)


class PlatformIntegrationGuideSnippets(BaseModel):
    frontend: str
    backend_env: str
    backend_fastapi: str


class PlatformIntegrationGuide(BaseModel):
    platform_key: str
    display_name: str
    bind_api_path: str
    frontend_script_path: str
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
