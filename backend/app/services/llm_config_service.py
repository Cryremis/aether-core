# backend/app/services/llm_config_service.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.schemas.llm import LlmConfigSummary, LlmNetworkConfig, LlmResolvedConfig, LlmSamplingParams, LlmConfigUpdateRequest
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
    sampling: dict[str, Any] = field(default_factory=dict)
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
            sampling=LlmSamplingParams(
                temperature=settings.llm_temperature,
                frequency_penalty=settings.llm_frequency_penalty,
                presence_penalty=settings.llm_presence_penalty,
            ),
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

    def get_embed_user_summary(self, platform_id: int, external_user_id: str) -> LlmConfigSummary | None:
        row = store_service.get_embed_user_llm_config(platform_id, external_user_id)
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
            sampling=request.sampling.model_dump(exclude_none=True),
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
            sampling=request.sampling.model_dump(exclude_none=True),
        )
        return self._to_summary(row)

    def update_embed_user_config(
        self,
        *,
        platform_id: int,
        external_user_id: str,
        request: LlmConfigUpdateRequest,
    ) -> LlmConfigSummary:
        existing = store_service.get_embed_user_llm_config(platform_id, external_user_id)
        api_key = None
        if request.clear_api_key:
            api_key = None
        elif (request.api_key or "").strip():
            api_key = request.api_key.strip()
        elif existing:
            api_key = existing.get("api_key")

        row = store_service.upsert_embed_user_llm_config(
            platform_id=platform_id,
            external_user_id=external_user_id,
            enabled=request.enabled,
            provider_kind=request.provider_kind,
            api_format=request.api_format,
            base_url=request.base_url.strip(),
            model=request.model.strip(),
            api_key=api_key,
            extra_headers=request.extra_headers,
            extra_body=request.extra_body,
            network=request.network.model_dump(mode="json"),
            sampling=request.sampling.model_dump(exclude_none=True),
        )
        return self._to_summary(row)

    def delete_platform_config(self, platform_id: int) -> None:
        store_service.delete_platform_llm_config(platform_id)

    def delete_user_config(self, user_id: int) -> None:
        store_service.delete_user_llm_config(user_id)

    def delete_embed_user_config(self, platform_id: int, external_user_id: str) -> None:
        store_service.delete_embed_user_llm_config(platform_id, external_user_id)

    def resolve_for_conversation(self, conversation: dict[str, Any]) -> RuntimeLlmConfig:
        owner_user_id = conversation.get("owner_user_id")
        platform_id = conversation.get("platform_id")
        external_user_id = conversation.get("external_user_id")

        # 一次性查询各层配置,复用于主配置解析和采样参数合并
        platform_config = None
        if platform_id:
            platform_config = store_service.get_platform_llm_config(int(platform_id))

        user_config = None
        if owner_user_id:
            user_config = store_service.get_user_llm_config(int(owner_user_id))
        elif platform_id and external_user_id:
            user_config = store_service.get_embed_user_llm_config(int(platform_id), str(external_user_id))

        # 主配置: first-match-wins(保持现有行为)
        if user_config and user_config.get("enabled"):
            config = self._to_runtime("user", user_config)
        elif platform_config and platform_config.get("enabled"):
            config = self._to_runtime("platform", platform_config)
        else:
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
                sampling=self._global_sampling_dict(),
                enabled=True,
            )

        # 采样参数: 字段级 merge(全局 → 平台 → 用户),非 None 的值逐层覆盖
        merged_sampling = self._global_sampling_dict()
        if platform_config and platform_config.get("enabled"):
            merged_sampling = self._merge_sampling(merged_sampling, platform_config.get("sampling") or {})
        if user_config and user_config.get("enabled"):
            merged_sampling = self._merge_sampling(merged_sampling, user_config.get("sampling") or {})
        config.sampling = merged_sampling
        return config

    def resolve_summary_for_user(self, user: StoreUser) -> LlmResolvedConfig:
        user_row = store_service.get_user_llm_config(user.user_id)
        platform = store_service.get_platform_by_key("standalone")
        platform_id = platform["platform_id"] if platform else None

        if user_row and user_row.get("enabled"):
            resolved = self._to_resolved("user", user_row)
        elif platform and platform_id:
            platform_row = store_service.get_platform_llm_config(platform_id)
            if platform_row and platform_row.get("enabled"):
                resolved = self._to_resolved("platform", platform_row)
            else:
                resolved = LlmResolvedConfig(scope="global", **self.get_global_summary().model_dump())
        else:
            resolved = LlmResolvedConfig(scope="global", **self.get_global_summary().model_dump())

        # 采样参数字段级 merge
        resolved.sampling = LlmSamplingParams(**self._resolve_merged_sampling(
            platform_id=platform_id, owner_user_id=user.user_id,
        ))
        return resolved

    def resolve_summary_for_embed_user(self, platform_id: int, external_user_id: str) -> LlmResolvedConfig:
        user_row = store_service.get_embed_user_llm_config(platform_id, external_user_id)
        if user_row and user_row.get("enabled"):
            resolved = self._to_resolved("user", user_row)
        else:
            platform_row = store_service.get_platform_llm_config(platform_id)
            if platform_row and platform_row.get("enabled"):
                resolved = self._to_resolved("platform", platform_row)
            else:
                resolved = LlmResolvedConfig(scope="global", **self.get_global_summary().model_dump())

        resolved.sampling = LlmSamplingParams(**self._resolve_merged_sampling(
            platform_id=platform_id, external_user_id=external_user_id,
        ))
        return resolved

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
            sampling=LlmSamplingParams(**(row.get("sampling") or {})),
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
            sampling=row.get("sampling") or {},
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

    def _global_sampling_dict(self) -> dict[str, Any]:
        """全局采样参数默认值(从 env 读取)。"""
        return {
            "temperature": settings.llm_temperature,
            "frequency_penalty": settings.llm_frequency_penalty,
            "presence_penalty": settings.llm_presence_penalty,
            "top_p": None,
            "repetition_penalty": None,
        }

    def _merge_sampling(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """字段级 merge: override 中非 None 的字段覆盖 base 的对应值。"""
        merged = dict(base)
        for key, value in override.items():
            if value is not None:
                merged[key] = value
        return merged

    def _resolve_merged_sampling(
        self,
        *,
        platform_id: int | None = None,
        owner_user_id: int | None = None,
        external_user_id: str | None = None,
    ) -> dict[str, Any]:
        """采样参数三层字段级继承(用于 UI summary)。"""
        merged = self._global_sampling_dict()
        if platform_id:
            platform_config = store_service.get_platform_llm_config(int(platform_id))
            if platform_config and platform_config.get("enabled"):
                merged = self._merge_sampling(merged, platform_config.get("sampling") or {})
        if owner_user_id:
            user_config = store_service.get_user_llm_config(int(owner_user_id))
            if user_config and user_config.get("enabled"):
                merged = self._merge_sampling(merged, user_config.get("sampling") or {})
        elif platform_id and external_user_id:
            embed_config = store_service.get_embed_user_llm_config(int(platform_id), str(external_user_id))
            if embed_config and embed_config.get("enabled"):
                merged = self._merge_sampling(merged, embed_config.get("sampling") or {})
        return merged

    def _normalize_network(self, value: Any) -> LlmNetworkConfig:
        if isinstance(value, LlmNetworkConfig):
            return value
        if isinstance(value, dict):
            return LlmNetworkConfig(**dict(value))
        return LlmNetworkConfig()

    def _public_network_config(self, config: LlmNetworkConfig) -> LlmNetworkConfig:
        return config


llm_config_service = LlmConfigService()
