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


class AuditTrendPoint(BaseModel):
    """单日审计趋势点。日增消息按会话创建日归因估算,累计为精确值。"""

    date: str
    new_users: int = 0
    new_conversations: int = 0
    new_messages: int = 0
    total_users: int = 0
    total_conversations: int = 0
    total_messages: int = 0


class AuditPlatformSeries(BaseModel):
    """按平台分解的时序(平台对比堆叠视图)。用户为当日活跃去重计数。"""

    platform_id: int
    display_name: str
    points: list[AuditTrendPoint] = Field(default_factory=list)


class AuditTrends(BaseModel):
    points: list[AuditTrendPoint] = Field(default_factory=list)
    platform_series: list[AuditPlatformSeries] = Field(default_factory=list)
