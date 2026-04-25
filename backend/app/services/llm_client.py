# backend/app/services/llm_client.py
from __future__ import annotations

import json
from typing import Any, AsyncGenerator

import httpx

from app.core.config import settings
from app.services.llm_config_service import RuntimeLlmConfig


class LlmClient:
    """OpenAI 兼容协议客户端。"""

    def _endpoint(self, config: RuntimeLlmConfig) -> str:
        if not config.api_key:
            raise RuntimeError("未配置 LLM_API_KEY，无法调用模型。")
        if not config.base_url:
            raise RuntimeError("未配置 LLM_BASE_URL，无法调用模型。")
        if not config.model:
            raise RuntimeError("未配置 LLM_MODEL，无法调用模型。")

        base = config.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1") or base.endswith("/v3") or base.endswith("/v4"):
            return f"{base}/chat/completions"
        return f"{base}/chat/completions"

    def _headers(self, config: RuntimeLlmConfig) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(config.extra_headers)
        return headers

    def _payload(
        self,
        config: RuntimeLlmConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": settings.llm_max_tokens,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["tool_stream"] = True
        payload.update(config.extra_body)
        return payload

    async def create_chat_completion(
        self,
        config: RuntimeLlmConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = self._payload(config, messages, tools, stream=False)
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(self._endpoint(config), headers=self._headers(config), json=payload)
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("LLM 未返回有效结果。")
        return choices[0].get("message") or {}

    async def stream_chat_completion(
        self,
        config: RuntimeLlmConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncGenerator[dict[str, Any], None]:
        payload = self._payload(config, messages, tools, stream=True)
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            async with client.stream("POST", self._endpoint(config), headers=self._headers(config), json=payload) as response:
                if response.is_error:
                    await response.aread()
                    response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        parsed = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    yield parsed


llm_client = LlmClient()
