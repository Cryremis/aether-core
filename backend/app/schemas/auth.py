# backend/app/schemas/auth.py
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class PasswordLoginRequest(BaseModel):
    """账号密码登录请求。"""

    username: str
    password: str


class OAuthCallbackRequest(BaseModel):
    """W3 OAuth 回调请求。"""

    code: str
    redirect_uri: str


class AuthTokenResponse(BaseModel):
    """认证令牌响应。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict[str, Any]


class CurrentUserResponse(BaseModel):
    """当前登录用户。"""

    user_id: int
    account_id: str
    username: str | None = None
    full_name: str
    email: str | None = None
    role: Literal["system_admin", "platform_admin", "debug"]
    provider: str


class AdminWhitelistCreateRequest(BaseModel):
    """管理员白名单创建请求。"""

    provider: Literal["w3", "password"]
    provider_user_id: str
    full_name: str | None = None
    email: str | None = None
    role: Literal["system_admin", "platform_admin", "debug"] = "platform_admin"


class AdminWhitelistRecord(BaseModel):
    """管理员白名单记录。"""

    whitelist_id: int
    provider: str
    provider_user_id: str
    full_name: str
    email: str | None = None
    role: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
