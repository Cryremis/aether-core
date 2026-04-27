# backend/app/api/routes/auth.py
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import AuthContext, require_authenticated_user, require_system_admin
from app.schemas.auth import (
    AuthTokenResponse,
    CurrentUserResponse,
    OAuthCallbackRequest,
    PasswordLoginRequest,
    SystemRoleUpdateRequest,
    UserSummary,
)
from app.schemas.common import ApiResponse
from app.services.auth_service import auth_service
from app.services.oauth_service import oauth_service
from app.services.store import store_service

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login/password", response_model=AuthTokenResponse)
def login_with_password(request: PasswordLoginRequest) -> AuthTokenResponse:
    try:
        result = auth_service.login_with_password(request.username, request.password)
    except RuntimeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return AuthTokenResponse(
        access_token=result.token,
        expires_in=result.expires_in,
        user=auth_service.build_user_payload(result.user),
    )


@router.post("/login/oauth/{provider_key}/callback", response_model=AuthTokenResponse)
async def login_with_oauth(provider_key: str, request: OAuthCallbackRequest) -> AuthTokenResponse:
    try:
        result = await auth_service.login_with_oauth(provider_key, request.code, request.redirect_uri)
    except RuntimeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return AuthTokenResponse(
        access_token=result.token,
        expires_in=result.expires_in,
        user=auth_service.build_user_payload(result.user),
    )


@router.get("/oauth/providers")
def list_oauth_providers() -> ApiResponse:
    return ApiResponse(
        message="OAuth 提供方列表",
        data=oauth_service.list_enabled_providers(),
    )


@router.get("/me", response_model=CurrentUserResponse)
def get_current_user(auth: AuthContext = Depends(require_authenticated_user)) -> CurrentUserResponse:
    user = auth.user
    assert user is not None
    managed_platform_ids = store_service.list_managed_platform_ids(user.user_id)
    return CurrentUserResponse(
        user_id=user.user_id,
        account_id=user.account_id,
        username=user.username,
        full_name=user.full_name,
        email=user.email,
        role=user.role,
        provider=user.provider,
        managed_platform_ids=managed_platform_ids,
        managed_platform_count=len(managed_platform_ids),
        can_manage_system=user.role == "system_admin",
        can_manage_platforms=bool(managed_platform_ids) or user.role == "system_admin",
    )


@router.get("/users")
def list_users(_auth: AuthContext = Depends(require_system_admin)) -> ApiResponse:
    items = [UserSummary(**row).model_dump(mode="json") for row in store_service.list_users()]
    return ApiResponse(message="用户列表", data=items)


@router.put("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    request: SystemRoleUpdateRequest,
    auth: AuthContext = Depends(require_system_admin),
) -> ApiResponse:
    target_user = store_service.get_user_by_id(user_id)
    if target_user is None:
        raise HTTPException(status_code=404, detail="目标用户不存在")
    if target_user.user_id == auth.user.user_id and request.role != "system_admin":
        if store_service.count_users_with_role("system_admin") <= 1:
            raise HTTPException(status_code=409, detail="至少需要保留一个系统管理员")
    if target_user.role == "system_admin" and request.role != "system_admin":
        if store_service.count_users_with_role("system_admin") <= 1:
            raise HTTPException(status_code=409, detail="至少需要保留一个系统管理员")
    store_service.update_user_role(user_id=user_id, role=request.role)
    refreshed = store_service.get_user_by_id(user_id)
    assert refreshed is not None
    payload = {
        "user_id": refreshed.user_id,
        "account_id": refreshed.account_id,
        "username": refreshed.username,
        "full_name": refreshed.full_name,
        "email": refreshed.email,
        "role": refreshed.role,
        "provider": refreshed.provider,
        "is_active": refreshed.is_active,
        "last_login_at": refreshed.last_login_at,
        "created_at": refreshed.created_at,
        "managed_platform_ids": store_service.list_managed_platform_ids(refreshed.user_id),
    }
    return ApiResponse(message="用户角色已更新", data=UserSummary(**payload).model_dump(mode="json"))
