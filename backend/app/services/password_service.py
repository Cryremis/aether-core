# backend/app/services/password_service.py
from __future__ import annotations

import base64
import hashlib
import hmac
import os


class PasswordService:
    """密码哈希与校验服务。"""

    _iterations = 120_000

    def hash_password(self, password: str) -> str:
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, self._iterations)
        return "pbkdf2_sha256${}${}${}".format(
            self._iterations,
            base64.b64encode(salt).decode("utf-8"),
            base64.b64encode(digest).decode("utf-8"),
        )

    def verify_password(self, plain_password: str, hashed_password: str | None) -> bool:
        if not hashed_password:
            return False
        try:
            scheme, iterations, salt_b64, digest_b64 = hashed_password.split("$", 3)
        except ValueError:
            return False
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            plain_password.encode("utf-8"),
            base64.b64decode(salt_b64.encode("utf-8")),
            int(iterations),
        )
        return hmac.compare_digest(digest, base64.b64decode(digest_b64.encode("utf-8")))


password_service = PasswordService()
