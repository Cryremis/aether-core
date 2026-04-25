from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def _normalize_provider_env_key(provider_key: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", provider_key.upper()).strip("_")


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _coerce_mapping(raw_value: str | None) -> dict[str, Any]:
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Invalid OAuth JSON configuration") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("OAuth JSON configuration must be an object")
    return parsed


@lru_cache(maxsize=1)
def _load_backend_env_file() -> dict[str, str]:
    env_file = settings.backend_root / ".env"
    if not env_file.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _read_config_value(name: str) -> str:
    value = os.environ.get(name)
    if value is not None:
        return value.strip()
    return _load_backend_env_file().get(name, "").strip()


@dataclass(frozen=True)
class OAuthProviderConfig:
    provider_key: str
    display_name: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scope: str = ""
    token_request_format: str = "form"
    userinfo_request_format: str = "bearer"
    user_id_fields: tuple[str, ...] = ("id", "sub")
    user_name_fields: tuple[str, ...] = ("name", "preferred_username")
    user_email_fields: tuple[str, ...] = ("email",)
    whitelist_match_fields: tuple[str, ...] = ("id",)
    extra_authorize_params: tuple[tuple[str, str], ...] = ()
    extra_token_params: tuple[tuple[str, str], ...] = ()
    extra_userinfo_params: tuple[tuple[str, str], ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret and self.authorize_url and self.token_url and self.userinfo_url)

    def missing_required_fields(self) -> list[str]:
        missing: list[str] = []
        if not self.client_id:
            missing.append("CLIENT_ID")
        if not self.client_secret:
            missing.append("CLIENT_SECRET")
        if not self.authorize_url:
            missing.append("AUTHORIZE_URL")
        if not self.token_url:
            missing.append("TOKEN_URL")
        if not self.userinfo_url:
            missing.append("USERINFO_URL")
        return missing


class OAuthService:
    """Generic OAuth provider registry and request helper."""

    def list_enabled_providers(self) -> list[dict[str, str]]:
        providers: list[dict[str, str]] = []
        for provider_key, config in self._load_provider_configs().items():
            if not config.enabled:
                continue
            providers.append(
                {
                    "provider_key": provider_key,
                    "display_name": config.display_name,
                    "authorize_url_template": self.build_authorize_url(provider_key, "{redirect_uri}"),
                }
            )
        return providers

    def get_provider_config(self, provider_key: str) -> OAuthProviderConfig:
        config = self._load_provider_configs().get(provider_key)
        if config is None:
            raise RuntimeError(f"Unsupported OAuth provider: {provider_key}")
        if not config.enabled:
            raise RuntimeError(f"OAuth provider is not fully configured: {provider_key}")
        return config

    async def exchange_code_for_token(self, provider_key: str, code: str, redirect_uri: str) -> dict[str, Any]:
        config = self.get_provider_config(provider_key)
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "redirect_uri": redirect_uri,
            **dict(config.extra_token_params),
        }
        headers = {"Accept": "application/json"}
        request_kwargs: dict[str, Any] = {"headers": headers}
        if config.token_request_format == "json":
            request_kwargs["json"] = payload
        else:
            request_kwargs["data"] = payload
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(config.token_url, **request_kwargs)
            response.raise_for_status()
        data = response.json()
        if data.get("error") or data.get("errorCode"):
            raise RuntimeError(
                str(
                    data.get("error_description")
                    or data.get("errorDesc")
                    or data.get("error")
                    or "OAuth token exchange failed"
                )
            )
        return data

    async def get_user_info(self, provider_key: str, access_token: str) -> dict[str, Any]:
        config = self.get_provider_config(provider_key)
        headers = {"Accept": "application/json"}
        request_kwargs: dict[str, Any] = {"headers": headers}
        if config.userinfo_request_format == "json":
            request_kwargs["json"] = {
                "client_id": config.client_id,
                "access_token": access_token,
                "scope": config.scope,
                **dict(config.extra_userinfo_params),
            }
            method = "POST"
        else:
            headers["Authorization"] = f"Bearer {access_token}"
            if config.extra_userinfo_params:
                request_kwargs["params"] = dict(config.extra_userinfo_params)
            method = "GET"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(method, config.userinfo_url, **request_kwargs)
            response.raise_for_status()
        data = response.json()
        if data.get("error") or data.get("errorCode"):
            raise RuntimeError(
                str(
                    data.get("error_description")
                    or data.get("errorDesc")
                    or data.get("error")
                    or "OAuth user info request failed"
                )
            )
        return data

    def build_authorize_url(self, provider_key: str, redirect_uri: str, state: str = "") -> str:
        config = self.get_provider_config(provider_key)
        query = {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": redirect_uri,
        }
        if config.scope:
            query["scope"] = config.scope
        if state:
            query["state"] = state
        query.update(dict(config.extra_authorize_params))
        return f"{config.authorize_url}?{urlencode(query)}"

    @lru_cache(maxsize=1)
    def _load_provider_configs(self) -> dict[str, OAuthProviderConfig]:
        providers: dict[str, OAuthProviderConfig] = {}

        for provider_key in _parse_csv(settings.auth_oauth_providers):
            normalized_key = provider_key.strip()
            if not normalized_key:
                continue
            config = self._load_from_env(normalized_key)
            if config is not None:
                providers[normalized_key] = config
                missing_fields = config.missing_required_fields()
                if missing_fields:
                    env_key = _normalize_provider_env_key(normalized_key)
                    missing_env_vars = ", ".join(f"AUTH_OAUTH_{env_key}_{field}" for field in missing_fields)
                    logger.warning(
                        "OAuth provider '%s' is declared but incomplete; missing %s",
                        normalized_key,
                        missing_env_vars,
                    )

        for provider_key, raw_config in _coerce_mapping(settings.auth_oauth_config_json).items():
            if not isinstance(raw_config, dict):
                continue
            providers[str(provider_key)] = self._load_from_mapping(str(provider_key), raw_config)

        return providers

    def reload(self) -> None:
        self._load_provider_configs.cache_clear()
        _load_backend_env_file.cache_clear()

    def _load_from_env(self, provider_key: str) -> OAuthProviderConfig | None:
        env_key = _normalize_provider_env_key(provider_key)
        prefix = f"AUTH_OAUTH_{env_key}_"
        client_id = _read_config_value(f"{prefix}CLIENT_ID")
        client_secret = _read_config_value(f"{prefix}CLIENT_SECRET")
        authorize_url = _read_config_value(f"{prefix}AUTHORIZE_URL")
        token_url = _read_config_value(f"{prefix}TOKEN_URL")
        userinfo_url = _read_config_value(f"{prefix}USERINFO_URL")
        display_name = _read_config_value(f"{prefix}DISPLAY_NAME") or provider_key
        scope = _read_config_value(f"{prefix}SCOPE")
        token_request_format = _read_config_value(f"{prefix}TOKEN_REQUEST_FORMAT").lower() or "form"
        userinfo_request_format = _read_config_value(f"{prefix}USERINFO_REQUEST_FORMAT").lower() or "bearer"
        user_id_fields = tuple(_parse_csv(_read_config_value(f"{prefix}USER_ID_FIELDS")) or ["id", "sub"])
        user_name_fields = tuple(
            _parse_csv(_read_config_value(f"{prefix}USER_NAME_FIELDS")) or ["name", "preferred_username"]
        )
        user_email_fields = tuple(_parse_csv(_read_config_value(f"{prefix}USER_EMAIL_FIELDS")) or ["email"])
        whitelist_match_fields = tuple(
            _parse_csv(_read_config_value(f"{prefix}WHITELIST_MATCH_FIELDS")) or list(user_id_fields)
        )
        extra_authorize_params = tuple(sorted(_coerce_mapping(_read_config_value(f"{prefix}AUTHORIZE_PARAMS_JSON")).items()))
        extra_token_params = tuple(sorted(_coerce_mapping(_read_config_value(f"{prefix}TOKEN_PARAMS_JSON")).items()))
        extra_userinfo_params = tuple(sorted(_coerce_mapping(_read_config_value(f"{prefix}USERINFO_PARAMS_JSON")).items()))

        if not any([client_id, client_secret, authorize_url, token_url, userinfo_url]):
            return None

        return OAuthProviderConfig(
            provider_key=provider_key,
            display_name=display_name,
            client_id=client_id,
            client_secret=client_secret,
            authorize_url=authorize_url,
            token_url=token_url,
            userinfo_url=userinfo_url,
            scope=scope,
            token_request_format=token_request_format,
            userinfo_request_format=userinfo_request_format,
            user_id_fields=user_id_fields,
            user_name_fields=user_name_fields,
            user_email_fields=user_email_fields,
            whitelist_match_fields=whitelist_match_fields,
            extra_authorize_params=extra_authorize_params,
            extra_token_params=extra_token_params,
            extra_userinfo_params=extra_userinfo_params,
        )

    def _load_from_mapping(self, provider_key: str, raw_config: dict[str, Any]) -> OAuthProviderConfig:
        def _read_list(name: str, default: list[str]) -> tuple[str, ...]:
            value = raw_config.get(name, default)
            if isinstance(value, list):
                return tuple(str(item).strip() for item in value if str(item).strip())
            if isinstance(value, str):
                return tuple(_parse_csv(value))
            return tuple(default)

        def _read_pairs(name: str) -> tuple[tuple[str, str], ...]:
            value = raw_config.get(name, {})
            if not isinstance(value, dict):
                return ()
            return tuple(sorted((str(key), str(item)) for key, item in value.items()))

        return OAuthProviderConfig(
            provider_key=provider_key,
            display_name=str(raw_config.get("display_name", provider_key)).strip() or provider_key,
            client_id=str(raw_config.get("client_id", "")).strip(),
            client_secret=str(raw_config.get("client_secret", "")).strip(),
            authorize_url=str(raw_config.get("authorize_url", "")).strip(),
            token_url=str(raw_config.get("token_url", "")).strip(),
            userinfo_url=str(raw_config.get("userinfo_url", "")).strip(),
            scope=str(raw_config.get("scope", "")).strip(),
            token_request_format=str(raw_config.get("token_request_format", "form")).strip().lower() or "form",
            userinfo_request_format=str(raw_config.get("userinfo_request_format", "bearer")).strip().lower() or "bearer",
            user_id_fields=_read_list("user_id_fields", ["id", "sub"]),
            user_name_fields=_read_list("user_name_fields", ["name", "preferred_username"]),
            user_email_fields=_read_list("user_email_fields", ["email"]),
            whitelist_match_fields=_read_list("whitelist_match_fields", ["id", "sub"]),
            extra_authorize_params=_read_pairs("authorize_params"),
            extra_token_params=_read_pairs("token_params"),
            extra_userinfo_params=_read_pairs("userinfo_params"),
        )


oauth_service = OAuthService()
