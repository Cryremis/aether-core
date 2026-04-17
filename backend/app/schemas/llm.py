# backend/app/schemas/llm.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


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
