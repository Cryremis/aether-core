from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


InternalUserRole = Literal["system_admin", "user"]


class PasswordLoginRequest(BaseModel):
    username: str
    password: str


class OAuthCallbackRequest(BaseModel):
    code: str
    redirect_uri: str


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict[str, Any]


class CurrentUserResponse(BaseModel):
    user_id: int
    account_id: str
    username: str | None = None
    full_name: str
    email: str | None = None
    role: InternalUserRole
    provider: str
    managed_platform_ids: list[int] = Field(default_factory=list)
    managed_platform_count: int = 0
    can_manage_system: bool = False
    can_manage_platforms: bool = False

class UserSummary(BaseModel):
    user_id: int
    account_id: str
    username: str | None = None
    full_name: str
    email: str | None = None
    role: InternalUserRole
    provider: str
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    managed_platform_ids: list[int] = Field(default_factory=list)


class SystemRoleUpdateRequest(BaseModel):
    role: InternalUserRole
