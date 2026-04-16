# backend/app/services/oauth_service.py
from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings


class OAuthService:
    """W3 OAuth 交换与用户信息查询服务。"""

    async def exchange_code_for_token(self, code: str, redirect_uri: str) -> dict[str, Any]:
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.auth_w3_client_id,
            "client_secret": settings.auth_w3_client_secret,
            "redirect_uri": redirect_uri,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(self._build_url(settings.auth_w3_token_path), json=payload)
            response.raise_for_status()
        data = response.json()
        if data.get("errorCode"):
            raise RuntimeError(data.get("errorDesc") or "W3 令牌交换失败")
        return data

    async def get_user_info(self, access_token: str) -> dict[str, Any]:
        payload = {
            "client_id": settings.auth_w3_client_id,
            "access_token": access_token,
            "scope": "base.profile",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(self._build_url(settings.auth_w3_userinfo_path), json=payload)
            response.raise_for_status()
        data = response.json()
        if data.get("errorCode"):
            raise RuntimeError(data.get("errorDesc") or "W3 用户信息获取失败")
        return data

    def build_authorize_url(self, redirect_uri: str, state: str = "") -> str:
        from urllib.parse import urlencode

        query = urlencode(
            {
                "response_type": "code",
                "client_id": settings.auth_w3_client_id,
                "redirect_uri": redirect_uri,
                "scope": "base.profile",
                "state": state,
            }
        )
        return f"{self._build_url(settings.auth_w3_authorize_path)}?{query}"

    def _build_url(self, path: str) -> str:
        return f"{settings.auth_w3_base_url.rstrip('/')}{path}"


oauth_service = OAuthService()
