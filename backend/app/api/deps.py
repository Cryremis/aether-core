# backend/app/api/deps.py
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.store import StoreUser, store_service
from app.services.token_service import token_service

bearer_scheme_optional = HTTPBearer(auto_error=False)


@dataclass
class AuthContext:
    """当前认证上下文。"""

    kind: str
    user: StoreUser | None = None
    role: str | None = None
    platform_id: int | None = None
    conversation_id: str | None = None
    external_user_id: str | None = None


def get_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme_optional),
    access_token: str | None = Query(default=None),
) -> AuthContext:
    raw_token = credentials.credentials if credentials is not None else access_token
    if raw_token is None:
        return AuthContext(kind="anonymous")
    payload = token_service.decode_token(raw_token)
    kind = str(payload.get("kind") or "")
    if kind == "admin":
        user_id = int(payload["sub"])
        user = store_service.get_user_by_id(user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理员账号不存在")
        return AuthContext(kind="admin", user=user, role=user.role)
    if kind == "embed":
        return AuthContext(
            kind="embed",
            platform_id=int(payload["platform_id"]),
            conversation_id=str(payload["conversation_id"]),
            external_user_id=str(payload["sub"]),
        )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="认证令牌无效")


def require_admin(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if auth.kind != "admin" or auth.user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要管理员登录")
    return auth


def require_system_admin(auth: AuthContext = Depends(require_admin)) -> AuthContext:
    if auth.role != "system_admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要系统管理员权限")
    return auth


def require_platform_secret(x_aether_platform_secret: str = Header(default="")) -> dict:
    platform = store_service.get_platform_by_secret(x_aether_platform_secret)
    if platform is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="平台密钥无效")
    return platform
