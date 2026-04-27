# backend/app/services/token_service.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from app.core.config import settings


class TokenService:
    """统一管理内部用户令牌与嵌入令牌。"""

    def create_user_token(self, user_id: int, role: str) -> tuple[str, int]:
        return self._create_token(
            {
                "kind": "user",
                "sub": str(user_id),
                "role": role,
            },
            timedelta(minutes=settings.auth_access_token_expire_minutes),
        )

    def create_admin_token(self, user_id: int, role: str) -> tuple[str, int]:
        return self.create_user_token(user_id, role)

    def create_embed_token(
        self,
        *,
        platform_id: int,
        conversation_id: str,
        external_user_id: str,
    ) -> tuple[str, int]:
        return self._create_token(
            {
                "kind": "embed",
                "sub": external_user_id,
                "platform_id": platform_id,
                "conversation_id": conversation_id,
            },
            timedelta(minutes=settings.auth_embed_token_expire_minutes),
        )

    def decode_token(self, token: str) -> dict[str, Any]:
        try:
            return jwt.decode(token, settings.auth_secret_key, algorithms=[settings.auth_algorithm])
        except JWTError as exc:  # noqa: B904
            raise ValueError("令牌无效或已过期") from exc

    def _create_token(self, payload: dict[str, Any], expires_delta: timedelta) -> tuple[str, int]:
        expire_at = datetime.now(timezone.utc) + expires_delta
        token = jwt.encode({**payload, "exp": expire_at}, settings.auth_secret_key, algorithm=settings.auth_algorithm)
        return token, int(expires_delta.total_seconds())


token_service = TokenService()
