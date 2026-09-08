from __future__ import annotations

import base64
import hashlib
import json
import uuid
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.services.store import store_service, utcnow_iso


class SecretStoreError(RuntimeError):
    pass


class SecretStoreService:
    """使用应用密钥加密保存能力凭据，公开配置中只出现不可逆引用。"""

    def _fernet(self) -> Fernet:
        if settings.app_env.lower() == "production" and not settings.capability_secret_encryption_key:
            raise SecretStoreError("生产环境必须配置 CAPABILITY_SECRET_ENCRYPTION_KEY")
        source = settings.capability_secret_encryption_key or settings.auth_secret_key
        if not source:
            raise SecretStoreError("未配置能力密钥加密密钥")
        key = base64.urlsafe_b64encode(hashlib.sha256(source.encode("utf-8")).digest())
        return Fernet(key)

    def put(self, payload: dict[str, Any], secret_id: str | None = None) -> str:
        secret_id = secret_id or f"sec_{uuid.uuid4().hex}"
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ciphertext = self._fernet().encrypt(encoded).decode("ascii")
        now = utcnow_iso()
        with store_service._connect() as conn:
            conn.execute(
                "INSERT INTO capability_secrets(secret_id, ciphertext, created_at, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(secret_id) DO UPDATE SET ciphertext=excluded.ciphertext, updated_at=excluded.updated_at",
                (secret_id, ciphertext, now, now),
            )
        return secret_id

    def get(self, secret_id: str | None) -> dict[str, Any]:
        if not secret_id:
            return {}
        with store_service._connect() as conn:
            row = conn.execute(
                "SELECT ciphertext FROM capability_secrets WHERE secret_id=?", (secret_id,)
            ).fetchone()
        if not row:
            return {}
        try:
            raw = self._fernet().decrypt(str(row["ciphertext"]).encode("ascii"))
            value = json.loads(raw.decode("utf-8"))
        except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
            raise SecretStoreError("能力凭据无法解密，请重新配置") from exc
        return value if isinstance(value, dict) else {}

    def delete(self, secret_id: str | None) -> bool:
        if not secret_id:
            return False
        with store_service._connect() as conn:
            result = conn.execute("DELETE FROM capability_secrets WHERE secret_id=?", (secret_id,))
        return result.rowcount > 0


secret_store_service = SecretStoreService()
