# backend/app/schemas/llm.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class LlmNetworkConfig(BaseModel):
    """LLM 关联的联网能力与策略配置。"""

    enabled: bool = True
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    max_search_results: int = 8
    fetch_timeout_seconds: int = 30


class LlmSamplingParams(BaseModel):
    """LLM 采样参数,支持三层字段级继承(全局 → 平台 → 用户)。

    所有字段默认 None,表示该层未设置、由下层 fallback。
    """

    temperature: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    top_p: float | None = None
    repetition_penalty: float | None = None


class LlmConfigUpdateRequest(BaseModel):
    """更新 LLM 配置请求。"""

    enabled: bool = True
    provider_kind: Literal["litellm"] = "litellm"
    api_format: Literal["openai-compatible"] = "openai-compatible"
    base_url: str
    model: str
    api_key: str | None = None
    clear_api_key: bool = False
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    network: LlmNetworkConfig = Field(default_factory=LlmNetworkConfig)
    sampling: LlmSamplingParams = Field(default_factory=LlmSamplingParams)


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
