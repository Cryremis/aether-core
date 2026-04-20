# backend/app/services/network_adapters.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse
import re

import httpx

from app.core.config import settings
from app.services.llm_config_service import RuntimeLlmConfig


class NativeSearchAdapter(Protocol):
    """原生联网搜索适配器协议。"""

    name: str

    def resolve_endpoint(self, runtime_config: RuntimeLlmConfig) -> str | None:
        """判断当前运行时配置是否可由本适配器处理，并返回请求地址。"""

    async def search(
        self,
        *,
        runtime_config: RuntimeLlmConfig,
        endpoint: str,
        query: str,
        allowed_domains: list[str],
        blocked_domains: list[str],
        max_results: int,
    ) -> dict:
        """执行原生联网搜索。"""


@dataclass(frozen=True)
class OpenAIResponsesSearchAdapter:
    """OpenAI Responses 风格原生联网搜索适配器。"""

    name: str = "openai_responses"

    def resolve_endpoint(self, runtime_config: RuntimeLlmConfig) -> str | None:
        endpoint = self._responses_endpoint(runtime_config.base_url)
        if not endpoint:
            return None
        model_name = runtime_config.model.lower()
        supported_prefixes = ("gpt-4.1", "gpt-4o", "gpt-5", "o3", "o4")
        if any(model_name.startswith(prefix) for prefix in supported_prefixes):
            return endpoint
        return None

    async def search(
        self,
        *,
        runtime_config: RuntimeLlmConfig,
        endpoint: str,
        query: str,
        allowed_domains: list[str],
        blocked_domains: list[str],
        max_results: int,
    ) -> dict:
        tool_payload: dict[str, object] = {"type": "web_search_preview"}
        if allowed_domains:
            tool_payload["filters"] = {"allowed_domains": allowed_domains}
        if blocked_domains:
            tool_payload.setdefault("filters", {})["blocked_domains"] = blocked_domains

        payload = {
            "model": runtime_config.model,
            "input": query,
            "tools": [tool_payload],
            "max_output_tokens": min(settings.llm_max_tokens, 2000),
        }
        headers = {
            "Authorization": f"Bearer {runtime_config.api_key}",
            "Content-Type": "application/json",
            **runtime_config.extra_headers,
        }
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        output_items = data.get("output") or []
        text_parts: list[str] = []
        citations: list[dict[str, str]] = []
        for item in output_items:
            for content in item.get("content") or []:
                if content.get("type") != "output_text":
                    continue
                text = str(content.get("text") or "").strip()
                if text:
                    text_parts.append(text)
                for annotation in content.get("annotations") or []:
                    url = str(annotation.get("url") or "").strip()
                    title = str(annotation.get("title") or url).strip()
                    if url:
                        citations.append({"title": title, "url": url})

        return {
            "query": query,
            "provider": self.name,
            "summary": "\n\n".join(text_parts).strip(),
            "results": citations[:max_results],
        }

    def _responses_endpoint(self, base_url: str) -> str | None:
        normalized = base_url.rstrip("/")
        if normalized.endswith("/chat/completions"):
            return f"{normalized[:-len('/chat/completions')]}/responses"
        if normalized.endswith("/responses"):
            return normalized
        if re.search(r"/v\d+$", normalized):
            return f"{normalized}/responses"
        return None


@dataclass(frozen=True)
class GlmSearchAdapter:
    """智谱 GLM 原生联网搜索适配器。"""

    name: str = "glm_web_search"

    def resolve_endpoint(self, runtime_config: RuntimeLlmConfig) -> str | None:
        normalized = runtime_config.base_url.rstrip("/")
        if normalized.endswith("/chat/completions"):
            return normalized
        if re.search(r"/v\d+$", normalized):
            return f"{normalized}/chat/completions"
        return None

    async def search(
        self,
        *,
        runtime_config: RuntimeLlmConfig,
        endpoint: str,
        query: str,
        allowed_domains: list[str],
        blocked_domains: list[str],
        max_results: int,
    ) -> dict:
        if blocked_domains or len(allowed_domains) > 1:
            raise RuntimeError("GLM 原生联网搜索暂不支持当前域名策略")

        web_search_payload: dict[str, object] = {
            "enable": True,
            "search_result": True,
            "count": min(max(1, max_results), 50),
        }
        if allowed_domains:
            web_search_payload["search_domain_filter"] = allowed_domains[0]

        payload = {
            "model": runtime_config.model,
            "messages": [{"role": "user", "content": query}],
            "tools": [{"type": "web_search", "web_search": web_search_payload}],
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {runtime_config.api_key}",
            "Content-Type": "application/json",
            **runtime_config.extra_headers,
        }
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        content = str((((data.get("choices") or [{}])[0]).get("message") or {}).get("content") or "").strip()
        return {
            "query": query,
            "provider": self.name,
            "summary": content,
            "results": [],
        }


@dataclass(frozen=True)
class DashScopeSearchAdapter:
    """阿里 DashScope OpenAI 兼容联网搜索适配器。"""

    name: str = "dashscope_web_search"

    def resolve_endpoint(self, runtime_config: RuntimeLlmConfig) -> str | None:
        normalized = runtime_config.base_url.rstrip("/")
        host = (urlparse(normalized).hostname or "").lower()
        if "dashscope" not in host:
            return None

        model_name = runtime_config.model.lower()
        supported_prefixes = (
            "qwen-plus",
            "qwen-turbo",
            "qwen-max",
            "qwen3.5-plus",
            "qwen3.5-flash",
            "qwen3.5-",
            "qwen3-",
            "qwen3.6-plus",
            "qwen3.5-omni",
            "qwen-omni",
        )
        if not any(model_name.startswith(prefix) for prefix in supported_prefixes):
            return None

        if normalized.endswith("/chat/completions"):
            return normalized
        if re.search(r"/v\d+$", normalized):
            return f"{normalized}/chat/completions"
        return None

    async def search(
        self,
        *,
        runtime_config: RuntimeLlmConfig,
        endpoint: str,
        query: str,
        allowed_domains: list[str],
        blocked_domains: list[str],
        max_results: int,
    ) -> dict:
        if allowed_domains or blocked_domains:
            raise RuntimeError("DashScope 原生联网搜索暂不支持当前域名策略")

        payload: dict[str, object] = {
            "model": runtime_config.model,
            "messages": [{"role": "user", "content": query}],
            "enable_search": True,
            # 按官方 OpenAI 兼容最小能力启用，避免额外参数导致 400。
            "search_options": {"enable_source": True},
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {runtime_config.api_key}",
            "Content-Type": "application/json",
            **runtime_config.extra_headers,
        }
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        message = (((data.get("choices") or [{}])[0]).get("message") or {})
        content = str(message.get("content") or "").strip()
        search_info = message.get("search_info") or []
        results: list[dict[str, str]] = []
        for item in search_info:
            url = str(item.get("link") or item.get("url") or "").strip()
            title = str(item.get("title") or url).strip()
            if not url:
                continue
            results.append({"title": title, "url": url, "source": self.name})
            if len(results) >= max_results:
                break

        return {
            "query": query,
            "provider": self.name,
            "summary": content,
            "results": results,
        }


NATIVE_SEARCH_ADAPTERS: tuple[NativeSearchAdapter, ...] = (
    DashScopeSearchAdapter(),
    GlmSearchAdapter(),
    OpenAIResponsesSearchAdapter(),
)
