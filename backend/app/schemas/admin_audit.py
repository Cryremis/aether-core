from datetime import datetime

from pydantic import BaseModel, Field


class PlatformAuditOverviewItem(BaseModel):
    platform_id: int
    platform_key: str
    display_name: str
    owner_user_id: int
    owner_name: str
    admin_count: int = 0
    hosted_user_count: int = 0
    conversation_count: int = 0
    message_count: int = 0
    active_runtime_count: int = 0
    runtime_count: int = 0
    last_activity_at: datetime | None = None


class SystemAuditOverview(BaseModel):
    platform_count: int = 0
    platforms_with_traffic_count: int = 0
    hosted_user_count: int = 0
    internal_user_count: int = 0
    platform_admin_assignment_count: int = 0
    pending_registration_request_count: int = 0
    conversation_count: int = 0
    message_count: int = 0
    active_runtime_count: int = 0
    runtime_count: int = 0
    platforms: list[PlatformAuditOverviewItem] = Field(default_factory=list)
