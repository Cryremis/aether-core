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
    """管理员认证服务。"""

    def _resolve_w3_whitelist(self, user_info: dict[str, Any]) -> dict[str, Any] | None:
        identifiers = [
            str(user_info.get("uuid") or "").strip(),
            str(user_info.get("uid") or "").strip(),
            str(user_info.get("employeeNumber") or "").strip(),
        ]
        for identifier in identifiers:
            if not identifier:
                continue
            whitelist = store_service.get_whitelist_entry("w3", identifier)
            if whitelist is not None:
                return whitelist
        return None

    def login_with_password(self, username: str, password: str) -> AuthResult:
        user = store_service.get_user_by_username(username)
        if user is None or not password_service.verify_password(password, user.password_hash):
            raise RuntimeError("用户名或密码错误")
        token, expires_in = token_service.create_admin_token(user.user_id, user.role)
        return AuthResult(token=token, expires_in=expires_in, user=user)

    async def login_with_w3(self, code: str, redirect_uri: str) -> AuthResult:
        token_data = await oauth_service.exchange_code_for_token(code, redirect_uri)
        access_token = token_data.get("access_token")
        if not access_token:
            raise RuntimeError("W3 未返回 access_token")
        user_info = await oauth_service.get_user_info(access_token)
        provider_user_id = str(user_info.get("uuid") or "").strip()
        if not provider_user_id:
            raise RuntimeError("无法从 W3 获取唯一用户标识 uuid")

        user = store_service.get_user_by_provider("w3", provider_user_id)
        if user is None:
            whitelist = self._resolve_w3_whitelist(user_info)
            if whitelist is None:
                raise RuntimeError("当前 W3 账号未被授权为 AetherCore 管理员")
            user = store_service.create_user_from_whitelist(
                provider="w3",
                provider_user_id=provider_user_id,
                full_name=user_info.get("displayNameCn") or whitelist["full_name"],
                email=user_info.get("email") or whitelist.get("email"),
                role=whitelist["role"],
            )
        token, expires_in = token_service.create_admin_token(user.user_id, user.role)
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
