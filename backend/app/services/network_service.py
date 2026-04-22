# backend/app/services/network_service.py
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.services.llm_config_service import RuntimeLlmConfig
from app.services.network_adapters import NATIVE_SEARCH_ADAPTERS
from app.services.session_types import AgentSession


class NetworkPolicyError(RuntimeError):
    """联网策略不允许时抛出的错误。"""


class ProviderNativeSearchError(RuntimeError):
    """原生联网搜索不可用时抛出的错误。"""


@dataclass
class SearchResultItem:
    """标准化搜索结果条目。"""

    title: str
    url: str
    snippet: str = ""
    source: str = ""


class _HtmlToTextParser(HTMLParser):
    """轻量 HTML 文本提取器。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._current_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if tag == "a":
            self._current_href = next((value for key, value in attrs if key == "href" and value), None)
        if tag in {"p", "div", "section", "article", "header", "footer", "li", "br", "h1", "h2", "h3", "h4"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return
        if tag == "a":
            self._current_href = None
        if tag in {"p", "div", "section", "article", "header", "footer", "li"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        text = data.strip()
        if not text:
            return
        if self._current_href:
            self._parts.append(f"{text} ({self._current_href})")
        else:
            self._parts.append(text)

    def get_text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(part.strip() for part in self._parts if part.strip())).strip()


class NetworkService:
    """统一处理联网策略、原生联网搜索和受控网页抓取。"""

    async def web_search(
        self,
        *,
        session: AgentSession,
        runtime_config: RuntimeLlmConfig,
        query: str,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
        max_results: int | None = None,
    ) -> dict[str, Any]:
        del session
        if not runtime_config.network.enabled:
            raise NetworkPolicyError("当前模型配置未开启 web_search")

        adapter_entry = self._resolve_search_adapter(runtime_config)
        if adapter_entry is None:
            raise NetworkPolicyError("当前模型未提供原生联网搜索能力")

        adapter, endpoint = adapter_entry
        merged_allowed, merged_blocked = self._merge_domain_policy(
            runtime_config,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
        )
        result = await adapter.search(
            runtime_config=runtime_config,
            endpoint=endpoint,
            query=query,
            allowed_domains=merged_allowed,
            blocked_domains=merged_blocked,
            max_results=max_results or runtime_config.network.max_search_results,
        )
        result["strategy"] = adapter.name
        return result

    async def web_fetch(
        self,
        *,
        runtime_config: RuntimeLlmConfig,
        url: str,
        format_type: str = "markdown",
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        if not runtime_config.network.enabled:
            raise NetworkPolicyError("当前模型配置未开启 web_fetch")
        self._validate_url_policy(runtime_config, url)

        timeout = min(
            max(1, timeout_seconds or runtime_config.network.fetch_timeout_seconds or settings.llm_network_fetch_timeout_seconds),
            120,
        )
        headers = {
            "User-Agent": settings.llm_network_user_agent,
            "Accept": "text/html, text/plain;q=0.9, application/xhtml+xml;q=0.8, */*;q=0.1",
        }
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            body = await response.aread()

        if len(body) > settings.llm_network_fetch_max_bytes:
            raise RuntimeError(f"响应体过大，超过 {settings.llm_network_fetch_max_bytes} 字节限制")

        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        text = body.decode(response.encoding or "utf-8", errors="ignore")
        content = text if format_type == "html" else (self._html_to_text(text) if "html" in content_type else text)
        return {
            "url": url,
            "format": format_type,
            "content_type": content_type or "text/plain",
            "content": content,
            "truncated": False,
        }

    def supports_web_search(self, runtime_config: RuntimeLlmConfig) -> bool:
        return self._resolve_search_adapter(runtime_config) is not None

    def _resolve_search_adapter(self, runtime_config: RuntimeLlmConfig):
        for adapter in NATIVE_SEARCH_ADAPTERS:
            endpoint = adapter.resolve_endpoint(runtime_config)
            if endpoint:
                return adapter, endpoint
        return None

    def _merge_domain_policy(
        self,
        runtime_config: RuntimeLlmConfig,
        *,
        allowed_domains: list[str] | None,
        blocked_domains: list[str] | None,
    ) -> tuple[list[str], list[str]]:
        merged_allowed = self._normalize_domains(runtime_config.network.allowed_domains)
        merged_blocked = self._normalize_domains(runtime_config.network.blocked_domains)
        merged_allowed.extend(self._normalize_domains(allowed_domains or []))
        merged_blocked.extend(self._normalize_domains(blocked_domains or []))
        return sorted(set(merged_allowed)), sorted(set(merged_blocked))

    def _validate_url_policy(self, runtime_config: RuntimeLlmConfig, url: str) -> None:
        self._validate_http_url(url)
        allowed_domains, blocked_domains = self._merge_domain_policy(runtime_config, allowed_domains=None, blocked_domains=None)
        if not self._is_url_allowed(url, allowed_domains=allowed_domains, blocked_domains=blocked_domains):
            raise NetworkPolicyError("目标 URL 不在允许的联网域名策略范围内")

    def _is_url_allowed(self, url: str, *, allowed_domains: list[str], blocked_domains: list[str]) -> bool:
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return False
        if any(host == domain or host.endswith(f".{domain}") for domain in blocked_domains):
            return False
        if allowed_domains:
            return any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)
        return True

    def _normalize_domains(self, domains: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in domains:
            domain = item.strip().lower()
            if not domain:
                continue
            normalized.append(domain)
        return normalized

    def _validate_http_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("仅支持 http / https URL")

    def _html_to_text(self, html_content: str) -> str:
        parser = _HtmlToTextParser()
        parser.feed(html.unescape(html_content))
        return parser.get_text()


network_service = NetworkService()
