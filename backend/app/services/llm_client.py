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
    _STREAM_PRELUDE_RETRY_LIMIT = 1
    _EFFORT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]

    def _resolve_effort(self, requested: str, supported: list[str]) -> str | None:
        """将请求的 reasoning_effort 映射到模型支持的值,优先向更低力度降级。"""
        if not requested:
            return None
        if requested in supported:
            return requested
        idx = self._EFFORT_ORDER.index(requested) if requested in self._EFFORT_ORDER else 4
        for i in range(idx - 1, -1, -1):
            if self._EFFORT_ORDER[i] in supported:
                return self._EFFORT_ORDER[i]
        for i in range(idx + 1, len(self._EFFORT_ORDER)):
            if self._EFFORT_ORDER[i] in supported:
                return self._EFFORT_ORDER[i]
        return None

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
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        sampling = config.sampling or {}
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "temperature": sampling.get("temperature", settings.llm_temperature),
            "frequency_penalty": sampling.get("frequency_penalty", settings.llm_frequency_penalty),
            "presence_penalty": sampling.get("presence_penalty", settings.llm_presence_penalty),
            "max_tokens": settings.llm_max_tokens,
            "stream": stream,
        }
        top_p = sampling.get("top_p")
        if top_p is not None:
            payload["top_p"] = top_p
        repetition_penalty = sampling.get("repetition_penalty")
        if repetition_penalty is not None:
            payload["repetition_penalty"] = repetition_penalty
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["tool_stream"] = True
        payload.update(config.extra_body)
        # reasoning_effort: per-message 优先,其次 config 级,注入在 extra_body 之后
        effective_effort = reasoning_effort or sampling.get("reasoning_effort")
        if effective_effort:
            resolved = self._resolve_effort(effective_effort, config.reasoning_effort_options)
            if resolved:
                payload["reasoning_effort"] = resolved
        return self._strip_tool_fields_when_disabled(payload)

    def _strip_tool_fields_when_disabled(self, payload: dict[str, Any]) -> dict[str, Any]:
        tools = payload.get("tools")
        if tools not in (None, [], False):
            return payload
        payload.pop("tools", None)
        payload.pop("tool_choice", None)
        payload.pop("tool_stream", None)
        return payload

    def _response_error_detail(self, response: httpx.Response) -> str:
        try:
            text = response.text.strip()
        except Exception:  # noqa: BLE001
            text = ""
        if text:
            return text
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            payload = None
        if payload is None:
            return ""
        if isinstance(payload, (dict, list)):
            try:
                return json.dumps(payload, ensure_ascii=False)
            except Exception:  # noqa: BLE001
                return str(payload)
        return str(payload).strip()

    def _raise_for_status_with_detail(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = self._response_error_detail(response)
            if not detail:
                raise
            request_url = str(exc.request.url) if exc.request is not None else ""
            url_suffix = f" for url '{request_url}'" if request_url else ""
            raise httpx.HTTPStatusError(
                f"LLM request failed with HTTP {response.status_code}{url_suffix}: {detail}",
                request=exc.request,
                response=exc.response,
            ) from exc

    def _should_retry_without_tools(self, exc: httpx.HTTPStatusError, payload: dict[str, Any]) -> bool:
        if "tools" not in payload:
            return False
        return exc.response.status_code in self._TOOL_RETRY_STATUS_CODES

    async def _post_json(self, config: RuntimeLlmConfig, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds, verify=settings.llm_ssl_verify) as client:
            response = await client.post(self._endpoint(config), headers=self._headers(config), json=payload)
            self._raise_for_status_with_detail(response)
            return response.json()

    async def _stream_request(
        self,
        config: RuntimeLlmConfig,
        payload: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        prelude_retry_count = 0
        while True:
            streamed_any_chunk = False
            try:
                async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds, verify=settings.llm_ssl_verify) as client:
                    async with client.stream("POST", self._endpoint(config), headers=self._headers(config), json=payload) as response:
                        if response.is_error:
                            await response.aread()
                            self._raise_for_status_with_detail(response)
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
                            streamed_any_chunk = True
                            yield parsed
                return
            except httpx.TransportError:
                # 仅在还未收到任何 chunk 时重试，避免重放已生成内容导致重复输出。
                if streamed_any_chunk or prelude_retry_count >= self._STREAM_PRELUDE_RETRY_LIMIT:
                    raise
                prelude_retry_count += 1

    async def create_chat_completion(
        self,
        config: RuntimeLlmConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        payload = self._payload(config, messages, tools, stream=False, reasoning_effort=reasoning_effort)
        try:
            data = await self._post_json(config, payload)
        except httpx.HTTPStatusError as exc:
            if not self._should_retry_without_tools(exc, payload):
                raise
            data = await self._post_json(config, self._payload(config, messages, [], stream=False, reasoning_effort=reasoning_effort))

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("LLM 未返回有效结果。")
        return choices[0].get("message") or {}

    async def stream_chat_completion(
        self,
        config: RuntimeLlmConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        reasoning_effort: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        payload = self._payload(config, messages, tools, stream=True, reasoning_effort=reasoning_effort)
        try:
            async for item in self._stream_request(config, payload):
                yield item
        except httpx.HTTPStatusError as exc:
            if not self._should_retry_without_tools(exc, payload):
                raise
            async for item in self._stream_request(config, self._payload(config, messages, [], stream=True, reasoning_effort=reasoning_effort)):
                yield item


llm_client = LlmClient()
