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
    sampling_sources: dict[str, str] = field(default_factory=dict)
    reasoning_effort_options: list[str] = field(default_factory=lambda: ["none", "minimal", "low", "medium", "high", "xhigh", "max"])
    enabled: bool = True
    trace_context: dict[str, Any] = field(default_factory=dict)


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
                sampling_sources={key: "模型默认" if value is None else "全局" for key, value in self._global_sampling_dict().items()},
                enabled=True,
            )

        # 采样参数: 字段级 merge(全局 → 平台 → 用户),非 None 的值逐层覆盖
        merged_sampling = self._global_sampling_dict(model=config.model, base_url=config.base_url, api_format=config.api_format)
        if platform_config and platform_config.get("enabled") and self._same_deployment(platform_config, {"model": config.model, "base_url": config.base_url, "api_format": config.api_format, "sampling": config.sampling}):
            merged_sampling = self._merge_sampling(merged_sampling, self._row_sampling(platform_config))
        if user_config and user_config.get("enabled"):
            merged_sampling = self._merge_sampling(merged_sampling, self._row_sampling(user_config))
        config.sampling = merged_sampling
        config.sampling_sources = self._sampling_sources(merged_sampling, platform_config, user_config, config.model, config.base_url, config.api_format)
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
        resolved.sampling_sources = self._sampling_sources(resolved.sampling.model_dump(),
            store_service.get_platform_llm_config(platform_id) if platform_id else None, user_row, resolved.model, resolved.base_url, resolved.api_format)
        return self._with_profile_summary(resolved)

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
        resolved.sampling_sources = self._sampling_sources(resolved.sampling.model_dump(),
            store_service.get_platform_llm_config(platform_id), user_row, resolved.model, resolved.base_url, resolved.api_format)
        return self._with_profile_summary(resolved)

    def _with_profile_summary(self, resolved: LlmResolvedConfig) -> LlmResolvedConfig:
        from app.services.context.model_profile import resolve_model_profile
        profile = resolve_model_profile(resolved)
        resolved.model_profile_id = profile.profile_id
        resolved.reasoning_required_with_tools = profile.requires_reasoning(resolved, [True])
        if profile.effort_options:
            resolved.reasoning_effort_options = list(profile.effort_options)
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
            sampling=LlmSamplingParams(**self._row_sampling(row)),
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
            sampling=self._row_sampling(row),
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

    def _global_sampling_dict(self, *, model: str | None = None, base_url: str | None = None, api_format: str = "openai-compatible") -> dict[str, Any]:
        """全局采样参数默认值(从 env 读取)。"""
        applicable = model is None or (model == settings.llm_model and str(base_url or "").rstrip("/") == settings.llm_base_url.rstrip("/") and api_format == "openai-compatible")
        return {
            "temperature": settings.llm_temperature if applicable else None,
            "frequency_penalty": settings.llm_frequency_penalty if applicable else None,
            "presence_penalty": settings.llm_presence_penalty if applicable else None,
            "top_p": None,
            "repetition_penalty": None,
            "reasoning_effort": None,
        }

    def _row_sampling(self, row: dict[str, Any]) -> dict[str, Any]:
        """兼容旧扩展参数；保持原有有效值，并让预算与来源可见。"""
        sampling = dict(row.get("sampling") or {})
        extra = row.get("extra_body") or {}
        container = extra.get("reasoning" if row.get("api_format") == "openai-responses" else "output_config") if row.get("api_format") in {"openai-responses", "anthropic-messages"} else None
        if isinstance(container, dict) and sampling.get("reasoning_effort") is None and container.get("effort") is not None:
            sampling["reasoning_effort"] = container["effort"]
        for key in ("temperature", "frequency_penalty", "presence_penalty", "top_p", "repetition_penalty", "reasoning_effort", "stop", "seed", "parallel_tool_calls"):
            if key in extra:
                if key in sampling.get("model_defaults", []):
                    raise ValueError(f"旧配置 extra_body.{key} 与模型默认模式冲突，请重新保存配置")
                sampling[key] = extra[key]
        limits = [extra[key] for key in ("max_tokens", "max_completion_tokens", "max_output_tokens") if key in extra]
        if len(limits) > 1:
            raise ValueError("旧配置有多个输出预算，请重新保存配置")
        if limits:
            sampling["max_output_tokens"] = limits[0]
        return sampling

    def _merge_sampling(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """字段级 merge: override 中非 None 的字段覆盖 base 的对应值。"""
        merged = dict(base)
        defaults = set(base.get("model_defaults", []))
        for key in override.get("model_defaults", []):
            merged[key] = None
            defaults.add(key)
        for key, value in override.items():
            if value is not None and key != "model_defaults":
                merged[key] = value
                defaults.discard(key)
        if defaults or "model_defaults" in merged or "model_defaults" in override:
            merged["model_defaults"] = sorted(defaults)
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
        platform_config = store_service.get_platform_llm_config(int(platform_id)) if platform_id else None
        selected_user = store_service.get_user_llm_config(int(owner_user_id)) if owner_user_id else (
            store_service.get_embed_user_llm_config(int(platform_id), str(external_user_id)) if platform_id and external_user_id else None)
        target = selected_user if selected_user and selected_user.get("enabled") else platform_config
        if target and target.get("enabled"):
            merged = self._global_sampling_dict(model=target.get("model"), base_url=target.get("base_url"), api_format=target.get("api_format") or "openai-compatible")
        if platform_id:
            platform_config = store_service.get_platform_llm_config(int(platform_id))
            if platform_config and platform_config.get("enabled") and (not target or self._same_deployment(platform_config, target)):
                merged = self._merge_sampling(merged, self._row_sampling(platform_config))
        if owner_user_id:
            user_config = store_service.get_user_llm_config(int(owner_user_id))
            if user_config and user_config.get("enabled"):
                merged = self._merge_sampling(merged, self._row_sampling(user_config))
        elif platform_id and external_user_id:
            embed_config = store_service.get_embed_user_llm_config(int(platform_id), str(external_user_id))
            if embed_config and embed_config.get("enabled"):
                merged = self._merge_sampling(merged, self._row_sampling(embed_config))
        return merged

    def _same_deployment(self, source, target) -> bool:
        if source.get("model") != target.get("model") or str(source.get("base_url", "")).rstrip("/") != str(target.get("base_url", "")).rstrip("/") or (source.get("api_format") or "openai-compatible") != (target.get("api_format") or "openai-compatible"):
            return False
        source_revision = (source.get("sampling") or {}).get("deployment_revision")
        target_revision = (target.get("sampling") or {}).get("deployment_revision")
        return not (source_revision and target_revision and source_revision != target_revision)

    def _sampling_sources(self, values, platform, user, model, base_url, api_format="openai-compatible") -> dict[str, str]:
        sources = {key: "模型默认" if value is None else "全局" for key, value in values.items()}
        for label, row in (("平台", platform), ("个人", user)):
            if not row or not row.get("enabled") or not self._same_deployment(row, {"model": model, "base_url": base_url, "api_format": api_format, "sampling": values}):
                continue
            sampling = self._row_sampling(row)
            for key in sampling.get("model_defaults", []):
                sources[key] = f"模型默认（{label}指定）"
            for key, value in sampling.items():
                if value is not None and key != "model_defaults":
                    sources[key] = label
        return sources

    def _normalize_network(self, value: Any) -> LlmNetworkConfig:
        if isinstance(value, LlmNetworkConfig):
            return value
        if isinstance(value, dict):
            return LlmNetworkConfig(**dict(value))
        return LlmNetworkConfig()

    def _public_network_config(self, config: LlmNetworkConfig) -> LlmNetworkConfig:
        return config


llm_config_service = LlmConfigService()
