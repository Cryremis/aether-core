# backend/app/services/llm_config_service.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.schemas.llm import LlmConfigSummary, LlmNetworkConfig, LlmResolvedConfig, LlmConfigUpdateRequest
from app.services.store import StoreUser, store_service


@dataclass
class RuntimeLlmConfig:
    """运行期使用的 LLM 配置。"""

    scope: str
    provider_kind: str
    api_format: str
    base_url: str
    model: str
    api_key: str
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    network: LlmNetworkConfig = field(default_factory=LlmNetworkConfig)
    enabled: bool = True


class LlmConfigService:
    """管理全局、平台、用户三级 LLM 配置。"""

    def get_global_summary(self) -> LlmConfigSummary:
        return LlmConfigSummary(
            enabled=True,
            provider_kind="litellm",
            api_format="openai-compatible",
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            has_api_key=bool(settings.llm_api_key),
            extra_headers={},
            extra_body={},
            network=self._public_network_config(self._global_network_config()),
            updated_at=None,
        )

    def get_platform_summary(self, platform_id: int) -> LlmConfigSummary | None:
        row = store_service.get_platform_llm_config(platform_id)
        if row is None:
            return None
        return self._to_summary(row)

    def get_user_summary(self, user_id: int) -> LlmConfigSummary | None:
        row = store_service.get_user_llm_config(user_id)
        if row is None:
            return None
        return self._to_summary(row)

    def update_platform_config(self, platform_id: int, request: LlmConfigUpdateRequest) -> LlmConfigSummary:
        row = store_service.upsert_platform_llm_config(
            platform_id=platform_id,
            enabled=request.enabled,
            provider_kind=request.provider_kind,
            api_format=request.api_format,
            base_url=request.base_url.strip(),
            model=request.model.strip(),
            api_key=(request.api_key or "").strip() or None,
            extra_headers=request.extra_headers,
            extra_body=request.extra_body,
            network=request.network.model_dump(mode="json"),
        )
        return self._to_summary(row)

    def update_user_config(self, user: StoreUser, request: LlmConfigUpdateRequest) -> LlmConfigSummary:
        existing = store_service.get_user_llm_config(user.user_id)
        api_key = None
        if request.clear_api_key:
            api_key = None
        elif (request.api_key or "").strip():
            api_key = request.api_key.strip()
        elif existing:
            api_key = existing.get("api_key")

        row = store_service.upsert_user_llm_config(
            user_id=user.user_id,
            enabled=request.enabled,
            provider_kind=request.provider_kind,
            api_format=request.api_format,
            base_url=request.base_url.strip(),
            model=request.model.strip(),
            api_key=api_key,
            extra_headers=request.extra_headers,
            extra_body=request.extra_body,
            network=request.network.model_dump(mode="json"),
        )
        return self._to_summary(row)

    def delete_platform_config(self, platform_id: int) -> None:
        store_service.delete_platform_llm_config(platform_id)

    def delete_user_config(self, user_id: int) -> None:
        store_service.delete_user_llm_config(user_id)

    def resolve_for_conversation(self, conversation: dict[str, Any]) -> RuntimeLlmConfig:
        owner_user_id = conversation.get("owner_user_id")
        platform_id = conversation.get("platform_id")

        if owner_user_id:
            user_config = store_service.get_user_llm_config(int(owner_user_id))
            if user_config and user_config.get("enabled"):
                return self._to_runtime("user", user_config)

        if platform_id:
            platform_config = store_service.get_platform_llm_config(int(platform_id))
            if platform_config and platform_config.get("enabled"):
                return self._to_runtime("platform", platform_config)

        return RuntimeLlmConfig(
            scope="global",
            provider_kind="litellm",
            api_format="openai-compatible",
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            extra_headers={},
            extra_body={},
            network=self._global_network_config(),
            enabled=True,
        )

    def resolve_summary_for_user(self, user: StoreUser) -> LlmResolvedConfig:
        user_row = store_service.get_user_llm_config(user.user_id)
        if user_row and user_row.get("enabled"):
            return self._to_resolved("user", user_row)

        platform = store_service.get_platform_by_key("standalone")
        if platform is not None:
            platform_row = store_service.get_platform_llm_config(platform["platform_id"])
            if platform_row and platform_row.get("enabled"):
                return self._to_resolved("platform", platform_row)

        global_summary = self.get_global_summary()
        return LlmResolvedConfig(scope="global", **global_summary.model_dump())

    def _to_summary(self, row: dict[str, Any]) -> LlmConfigSummary:
        return LlmConfigSummary(
            enabled=bool(row.get("enabled", True)),
            provider_kind=str(row.get("provider_kind") or "litellm"),
            api_format=str(row.get("api_format") or "openai-compatible"),
            base_url=str(row.get("base_url") or ""),
            model=str(row.get("model") or ""),
            has_api_key=bool(row.get("has_api_key")),
            extra_headers=row.get("extra_headers") or {},
            extra_body=row.get("extra_body") or {},
            network=self._public_network_config(self._normalize_network(row.get("network"))),
            updated_at=row.get("updated_at"),
        )

    def _to_runtime(self, scope: str, row: dict[str, Any]) -> RuntimeLlmConfig:
        return RuntimeLlmConfig(
            scope=scope,
            provider_kind=str(row.get("provider_kind") or "litellm"),
            api_format=str(row.get("api_format") or "openai-compatible"),
            base_url=str(row.get("base_url") or ""),
            model=str(row.get("model") or ""),
            api_key=str(row.get("api_key") or ""),
            extra_headers=row.get("extra_headers") or {},
            extra_body=row.get("extra_body") or {},
            network=self._normalize_network(row.get("network")),
            enabled=bool(row.get("enabled", True)),
        )

    def _to_resolved(self, scope: str, row: dict[str, Any]) -> LlmResolvedConfig:
        return LlmResolvedConfig(scope=scope, **self._to_summary(row).model_dump())

    def _global_network_config(self) -> LlmNetworkConfig:
        return LlmNetworkConfig(
            enabled=settings.llm_network_enabled,
            allowed_domains=list(settings.llm_network_allowed_domains),
            blocked_domains=list(settings.llm_network_blocked_domains),
            max_search_results=settings.llm_network_max_search_results,
            fetch_timeout_seconds=settings.llm_network_fetch_timeout_seconds,
        )

    def _normalize_network(self, value: Any) -> LlmNetworkConfig:
        if isinstance(value, LlmNetworkConfig):
            return value
        if isinstance(value, dict):
            return LlmNetworkConfig(**dict(value))
        return LlmNetworkConfig()

    def _public_network_config(self, config: LlmNetworkConfig) -> LlmNetworkConfig:
        return config


llm_config_service = LlmConfigService()
