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
    external_org_id: str | None = None


def embedded_organization(auth: AuthContext) -> str | None:
    org = auth.external_org_id
    if org is None and auth.conversation_id:
        # 兼容未声明组织的旧 token，从它签发时绑定的会话恢复组织，不能用请求目标替代。
        anchor = store_service.get_conversation(auth.conversation_id)
        if anchor and anchor.get("platform_id") == auth.platform_id and anchor.get("external_user_id") == auth.external_user_id:
            org = anchor.get("external_org_id")
    return org


def can_access_embedded_conversation(auth: AuthContext, conversation: dict) -> bool:
    return (conversation.get("platform_id") == auth.platform_id
            and conversation.get("external_user_id") == auth.external_user_id
            and conversation.get("external_org_id") == embedded_organization(auth))


def get_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme_optional),
    access_token: str | None = Query(default=None),
) -> AuthContext:
    raw_token = credentials.credentials if credentials is not None else access_token
    if raw_token is None:
        return AuthContext(kind="anonymous")
    try:
        payload = token_service.decode_token(raw_token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    kind = str(payload.get("kind") or "")
    if kind in {"user", "admin"}:
        user_id = int(payload["sub"])
        user = store_service.get_user_by_id(user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户账号不存在")
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被禁用")
        return AuthContext(kind="user", user=user, role=user.role)
    if kind == "embed":
        return AuthContext(
            kind="embed",
            platform_id=int(payload["platform_id"]),
            conversation_id=str(payload["conversation_id"]),
            external_user_id=str(payload["sub"]),
            external_org_id=(
                str(payload["external_org_id"])
                if payload.get("external_org_id") is not None
                else None
            ),
        )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="认证令牌无效")


def require_authenticated_user(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if auth.kind != "user" or auth.user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要登录")
    return auth


def require_admin(auth: AuthContext = Depends(require_authenticated_user)) -> AuthContext:
    return auth


def require_system_admin(auth: AuthContext = Depends(require_authenticated_user)) -> AuthContext:
    if auth.role != "system_admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要系统管理员权限")
    return auth


def require_platform_secret(x_aether_platform_secret: str = Header(default="")) -> dict:
    platform = store_service.get_platform_by_secret(x_aether_platform_secret)
    if platform is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="平台密钥无效")
    return platform
