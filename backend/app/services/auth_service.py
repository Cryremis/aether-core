# backend/app/services/auth_service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.oauth_service import oauth_service
from app.services.password_service import password_service
from app.services.store import StoreUser, store_service
from app.services.token_service import token_service


@dataclass
class AuthResult:
    """登录结果。"""

    token: str
    expires_in: int
    user: StoreUser


class AuthService:
    """Handle authentication for internal users."""

    def _extract_first_non_empty(self, source: dict[str, Any], field_names: tuple[str, ...]) -> str:
        for field_name in field_names:
            value = str(source.get(field_name) or "").strip()
            if value:
                return value
        return ""

    def login_with_password(self, username: str, password: str) -> AuthResult:
        user = store_service.get_user_by_username(username)
        if user is None or not password_service.verify_password(password, user.password_hash):
            raise RuntimeError("用户名或密码错误")
        if not user.is_active:
            raise RuntimeError("账号已被禁用")
        store_service.update_user_login_metadata(user.user_id)
        token, expires_in = token_service.create_user_token(user.user_id, user.role)
        return AuthResult(token=token, expires_in=expires_in, user=user)

    async def login_with_oauth(self, provider_key: str, code: str, redirect_uri: str) -> AuthResult:
        provider_config = oauth_service.get_provider_config(provider_key)
        token_data = await oauth_service.exchange_code_for_token(provider_key, code, redirect_uri)
        access_token = str(token_data.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError(f"{provider_config.display_name} 未返回 access_token")

        user_info = await oauth_service.get_user_info(provider_key, access_token)
        provider_user_id = self._extract_first_non_empty(user_info, provider_config.user_id_fields)
        if not provider_user_id:
            raise RuntimeError(f"无法从 {provider_config.display_name} 获取唯一用户标识")

        user = store_service.create_or_update_oauth_user(
            provider=provider_key,
            provider_user_id=provider_user_id,
            full_name=self._extract_first_non_empty(user_info, provider_config.user_name_fields) or provider_user_id,
            email=self._extract_first_non_empty(user_info, provider_config.user_email_fields) or None,
        )
        if not user.is_active:
            raise RuntimeError("账号已被禁用")

        token, expires_in = token_service.create_user_token(user.user_id, user.role)
        return AuthResult(token=token, expires_in=expires_in, user=user)

    def build_user_payload(self, user: StoreUser) -> dict[str, Any]:
        return {
            "user_id": user.user_id,
            "account_id": user.account_id,
            "username": user.username,
            "full_name": user.full_name,
            "email": user.email,
            "role": user.role,
            "provider": user.provider,
        }


auth_service = AuthService()
