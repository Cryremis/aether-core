# backend/app/schemas/llm.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from app.core.llm_protocol import RUNTIME_CONTROL_FIELDS


class LlmNetworkConfig(BaseModel):
    """LLM 关联的联网能力与策略配置。"""

    enabled: bool = True
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    max_search_results: int = 8
    fetch_timeout_seconds: int = 30


class LlmDeploymentProfile(BaseModel):
    """人工声明的部署能力；真实服务验证状态由诊断报告单独提供。"""

    service_kind: str = "openai-compatible"
    service_version: str = ""
    endpoint_ref: str = ""
    served_model_name: str = ""
    model_revision: str = ""
    tokenizer_revision: str = ""
    tokenizer_id: str = ""
    chat_template_digest: str = ""
    reasoning_parser: str = ""
    tool_parser: str = ""
    protocol_version: str = ""
    sse_rules_version: str = "standard-sse/v1"
    usage_semantics: Literal["auto", "openai", "anthropic"] = "auto"
    thinking_mode: Literal["auto", "enabled", "disabled"] = "auto"
    context_window: int | None = Field(default=None, ge=1024)
    max_output_tokens: int | None = Field(default=None, ge=1)
    supported_parameters: list[str] | None = None
    effort_options: list[str] | None = None
    reasoning_required_with_tools: bool | None = None
    native_compaction: bool | None = None
    validation_state: Literal["declared"] = "declared"


class LlmSamplingParams(BaseModel):
    """LLM 采样参数,支持三层字段级继承(全局 → 平台 → 用户)。

    所有字段默认 None,表示该层未设置、由下层 fallback。
    """

    temperature: float | None = Field(default=None, ge=0, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    repetition_penalty: float | None = Field(default=None, gt=0, le=2)
    reasoning_effort: str | int | None = None
    max_output_tokens: int | None = Field(default=None, ge=1)
    context_window: int | None = Field(default=None, ge=1024)
    native_compaction: bool | None = None
    reasoning_replay: Literal["auto", "disabled"] | None = None
    reasoning_field: Literal["reasoning_content", "reasoning"] | None = None
    model_profile: Literal["auto", "generic", "deepseek-thinking"] | None = None
    stream_usage: bool | None = None
    deployment_revision: str | None = Field(default=None, max_length=256)
    compaction_max_output_tokens: int | None = Field(default=None, ge=128)
    compaction_max_calls: int | None = Field(default=None, ge=1, le=64)
    deployment_profile: LlmDeploymentProfile | None = None
    stop: str | list[str] | None = None
    seed: int | None = Field(default=None, strict=True)
    parallel_tool_calls: bool | None = None
    # 显式使用模型默认值会停止上层继承；0 仍然是合法的明确值。
    model_defaults: list[Literal["temperature", "frequency_penalty", "presence_penalty", "top_p", "repetition_penalty", "reasoning_effort", "stop", "seed", "parallel_tool_calls"]] = Field(default_factory=list)

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def validate_effort(cls, value):
        if isinstance(value, bool) or (isinstance(value, int) and not 1 <= value <= 100):
            raise ValueError("数值思考力度必须在 1–100 之间")
        if isinstance(value, str) and (not value.strip() or (value.isdecimal() and not 1 <= int(value) <= 100)):
            raise ValueError("思考力度不能为空，数值必须在 1–100 之间")
        return value

    @field_validator("stop")
    @classmethod
    def validate_stop(cls, value):
        if value is not None and (not value or (isinstance(value, list) and (len(value) > 16 or any(not item for item in value)))):
            raise ValueError("stop 必须为非空字符串或最多 16 个非空字符串")
        return value


class LlmConfigUpdateRequest(BaseModel):
    """更新 LLM 配置请求。"""

    enabled: bool = True
    provider_kind: Literal["litellm"] = "litellm"
    api_format: Literal["openai-compatible", "openai-responses", "anthropic-messages"] = "openai-compatible"
    base_url: str
    model: str
    api_key: str | None = None
    clear_api_key: bool = False
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    network: LlmNetworkConfig = Field(default_factory=LlmNetworkConfig)
    sampling: LlmSamplingParams = Field(default_factory=LlmSamplingParams)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_sampling(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        extra = dict(value.get("extra_body") or {})
        source = value.get("sampling") or {}
        sampling = source.model_dump() if isinstance(source, LlmSamplingParams) else dict(source)
        aliases = {key: key for key in ("temperature", "frequency_penalty", "presence_penalty", "top_p", "repetition_penalty", "reasoning_effort", "stop", "seed", "parallel_tool_calls")}
        aliases.update({key: "max_output_tokens" for key in ("max_tokens", "max_completion_tokens", "max_output_tokens")})
        limits = [key for key in ("max_tokens", "max_completion_tokens", "max_output_tokens") if key in extra]
        if len(limits) > 1:
            raise ValueError("输出预算不能同时设置多个协议字段")
        for key, target in aliases.items():
            if key not in extra:
                continue
            if target in sampling.get("model_defaults", []) or (sampling.get(target) is not None and sampling[target] != extra[key]):
                raise ValueError(f"extra_body.{key} 与标准采样配置冲突，请仅保留一个来源")
            sampling[target] = extra[key]
            # max_completion_tokens 仍作为旧 Chat 服务的字段选择标记保留。
            if key != "max_completion_tokens":
                extra.pop(key)
        effort_container = "reasoning" if value.get("api_format") == "openai-responses" else "output_config" if value.get("api_format") == "anthropic-messages" else None
        if effort_container and isinstance(extra.get(effort_container), dict) and "effort" in extra[effort_container]:
            container = dict(extra[effort_container])
            effort = container.pop("effort")
            if "reasoning_effort" in sampling.get("model_defaults", []) or (sampling.get("reasoning_effort") is not None and sampling["reasoning_effort"] != effort):
                raise ValueError("额外请求体中的思考力度与标准配置冲突")
            sampling["reasoning_effort"] = effort
            if container:
                extra[effort_container] = container
            else:
                extra.pop(effort_container)
        value.update(extra_body=extra, sampling=sampling)
        return value

    @model_validator(mode="after")
    def validate_deployment(self):
        deployment = self.sampling.deployment_profile
        for key in self.sampling.model_defaults:
            if getattr(self.sampling, key) is not None:
                raise ValueError(f"{key} 不能同时指定明确值和模型默认模式")
        if deployment and deployment.served_model_name and deployment.served_model_name != self.model:
            raise ValueError("部署声明的 served_model_name 与当前模型不一致")
        if self.sampling.native_compaction and self.api_format != "openai-responses":
            raise ValueError("原生压缩目前只适用于 Responses 协议")
        if deployment and self.sampling.native_compaction and deployment.native_compaction is False:
            raise ValueError("部署声明不支持原生压缩")
        if deployment and deployment.context_window and self.sampling.context_window and self.sampling.context_window > deployment.context_window:
            raise ValueError("上下文预算超过部署硬上限")
        if deployment and deployment.max_output_tokens and self.sampling.max_output_tokens and self.sampling.max_output_tokens > deployment.max_output_tokens:
            raise ValueError("输出预算超过部署硬上限")
        if deployment and deployment.max_output_tokens and self.sampling.compaction_max_output_tokens and self.sampling.compaction_max_output_tokens > deployment.max_output_tokens:
            raise ValueError("摘要输出预算超过部署硬上限")
        if self.api_format != "openai-compatible":
            unsupported = [key for key in ("frequency_penalty", "presence_penalty", "repetition_penalty", "seed") if getattr(self.sampling, key) is not None]
            if self.api_format == "openai-responses" and self.sampling.stop is not None:
                unsupported.append("stop")
            if unsupported:
                raise ValueError(f"当前原生协议适配器未声明支持参数: {unsupported}")
        for key in ("thinking", "chat_template_kwargs", "stream_options"):
            if key in self.extra_body and not isinstance(self.extra_body[key], dict):
                raise ValueError(f"{key} 必须是 JSON 对象")
        if self.api_format != "openai-compatible":
            for key in ("reasoning", "output_config"):
                if key in self.extra_body and not isinstance(self.extra_body[key], dict):
                    raise ValueError(f"{key} 必须是 JSON 对象")
        if deployment and deployment.thinking_mode != "auto":
            requested = (self.extra_body.get("thinking") or {}).get("type")
            if requested in {"enabled", "disabled"} and requested != deployment.thinking_mode:
                raise ValueError("thinking 与部署声明的思考模式冲突")
        enable_thinking = (self.extra_body.get("chat_template_kwargs") or {}).get("enable_thinking")
        if enable_thinking is not None:
            if not isinstance(enable_thinking, bool):
                raise ValueError("chat_template_kwargs.enable_thinking 必须为布尔值")
            mode = "enabled" if enable_thinking else "disabled"
            requested = (self.extra_body.get("thinking") or {}).get("type")
            if requested in {"enabled", "disabled"} and requested != mode:
                raise ValueError("thinking 与 chat_template_kwargs.enable_thinking 冲突")
            if deployment and deployment.thinking_mode != "auto" and deployment.thinking_mode != mode:
                raise ValueError("chat_template_kwargs 与部署声明的思考模式冲突")
        return self

    @field_validator("extra_body")
    @classmethod
    def validate_runtime_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        protected = RUNTIME_CONTROL_FIELDS
        if protected.intersection(value) or ("tools" in value and value["tools"] != []):
            raise ValueError("额外请求体不能覆盖模型、消息、工具或请求控制字段")
        limits = [value[key] for key in ("max_tokens", "max_completion_tokens", "max_output_tokens") if key in value]
        if len(limits) > 1 or any(not isinstance(item, int) or isinstance(item, bool) or item < 1 for item in limits):
            raise ValueError("输出预算必须是唯一的正整数配置")
        return value


class LlmConfigSummary(BaseModel):
    """LLM 配置摘要。"""

    enabled: bool = True
    provider_kind: str = "litellm"
    api_format: str = "openai-compatible"
    base_url: str = ""
    model: str = ""
    has_api_key: bool = False
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    network: LlmNetworkConfig = Field(default_factory=LlmNetworkConfig)
    sampling: LlmSamplingParams = Field(default_factory=LlmSamplingParams)
    updated_at: datetime | None = Field(default_factory=lambda: datetime.now(timezone.utc))


class LlmResolvedConfig(BaseModel):
    """实际生效的 LLM 配置。"""

    scope: Literal["user", "platform", "global"]
    enabled: bool = True
    provider_kind: str = "litellm"
    api_format: str = "openai-compatible"
    base_url: str = ""
    model: str = ""
    has_api_key: bool = False
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    network: LlmNetworkConfig = Field(default_factory=LlmNetworkConfig)
    sampling: LlmSamplingParams = Field(default_factory=LlmSamplingParams)
    reasoning_effort_options: list[str] = Field(default_factory=lambda: ["none", "minimal", "low", "medium", "high", "xhigh", "max"])
    sampling_sources: dict[str, str] = Field(default_factory=dict)
    model_profile_id: str = "generic/v1"
    reasoning_required_with_tools: bool = False
