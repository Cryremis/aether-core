# backend/app/api/routes/auth.py
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import AuthContext, require_admin, require_system_admin
from app.schemas.auth import (
    AdminWhitelistCreateRequest,
    AdminWhitelistRecord,
    AuthTokenResponse,
    CurrentUserResponse,
    OAuthCallbackRequest,
    PasswordLoginRequest,
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
def get_current_user(auth: AuthContext = Depends(require_admin)) -> CurrentUserResponse:
    user = auth.user
    assert user is not None
    return CurrentUserResponse(
        user_id=user.user_id,
        account_id=user.account_id,
        username=user.username,
        full_name=user.full_name,
        email=user.email,
        role=user.role,
        provider=user.provider,
    )


@router.post("/admin-whitelist")
def create_admin_whitelist(
    request: AdminWhitelistCreateRequest,
    _auth: AuthContext = Depends(require_system_admin),
) -> ApiResponse:
    display_name = (request.full_name or "").strip() or request.provider_user_id.strip()
    row = store_service.upsert_admin_whitelist(
        provider=request.provider.strip(),
        provider_user_id=request.provider_user_id.strip(),
        full_name=display_name,
        email=request.email,
        role=request.role,
    )
    return ApiResponse(
        message="管理员白名单已更新",
        data=AdminWhitelistRecord(**row).model_dump(mode="json"),
    )


@router.get("/admin-whitelist")
def list_admin_whitelist(_auth: AuthContext = Depends(require_system_admin)) -> ApiResponse:
    items = [AdminWhitelistRecord(**row).model_dump(mode="json") for row in store_service.list_admin_whitelist()]
    return ApiResponse(message="管理员白名单", data=items)
