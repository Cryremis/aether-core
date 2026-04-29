# backend/app/services/llm_client.py
from __future__ import annotations

import json
from typing import Any, AsyncGenerator

import httpx

from app.core.config import settings
from app.services.llm_config_service import RuntimeLlmConfig


class LlmClient:
    """OpenAI 兼容协议客户端。"""

    _TOOL_RETRY_STATUS_CODES = {400, 404, 422, 500, 502, 503, 504}

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
        return self._strip_tool_fields_when_disabled(payload)

    def _strip_tool_fields_when_disabled(self, payload: dict[str, Any]) -> dict[str, Any]:
        tools = payload.get("tools")
        if tools not in (None, [], False):
            return payload
        payload.pop("tools", None)
        payload.pop("tool_choice", None)
        payload.pop("tool_stream", None)
        return payload

    def _should_retry_without_tools(self, exc: httpx.HTTPStatusError, payload: dict[str, Any]) -> bool:
        if "tools" not in payload:
            return False
        return exc.response.status_code in self._TOOL_RETRY_STATUS_CODES

    async def _post_json(self, config: RuntimeLlmConfig, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(self._endpoint(config), headers=self._headers(config), json=payload)
            response.raise_for_status()
            return response.json()

    async def _stream_request(
        self,
        config: RuntimeLlmConfig,
        payload: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
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

    async def create_chat_completion(
        self,
        config: RuntimeLlmConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = self._payload(config, messages, tools, stream=False)
        try:
            data = await self._post_json(config, payload)
        except httpx.HTTPStatusError as exc:
            if not self._should_retry_without_tools(exc, payload):
                raise
            data = await self._post_json(config, self._payload(config, messages, [], stream=False))

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
        try:
            async for item in self._stream_request(config, payload):
                yield item
        except httpx.HTTPStatusError as exc:
            if not self._should_retry_without_tools(exc, payload):
                raise
            async for item in self._stream_request(config, self._payload(config, messages, [], stream=True)):
                yield item


llm_client = LlmClient()
