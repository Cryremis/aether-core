"""读取已配置部署的模型卡片。窗口未知时由预算层采用保守值。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit
from weakref import WeakValueDictionary

import httpx

from app.core.config import settings


class ModelLimitDiscovery:
    def __init__(self) -> None:
        self._cache = OrderedDict()
        self._locks = WeakValueDictionary()

    async def resolve(self, runtime, headers: dict[str, str]):
        if not settings.llm_model_discovery_enabled or runtime.api_format not in {"openai-compatible", "openai-responses"}:
            return runtime
        from app.services.context.model_profile import deployment_profile

        profile = deployment_profile(runtime)
        if (runtime.sampling.get("context_window") or profile.get("context_window")) and (
            runtime.sampling.get("max_output_tokens") or profile.get("max_output_tokens")
            or {"max_tokens", "max_completion_tokens", "max_output_tokens"}.intersection(runtime.extra_body)
        ):
            return runtime
        try:
            parts = urlsplit(runtime.base_url)
        except ValueError:
            return runtime
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return runtime
        path = parts.path.rstrip("/")
        for suffix in ("/chat/completions", "/responses"):
            if path.endswith(suffix):
                path = path.removesuffix(suffix)
                break
        endpoint = urlunsplit((parts.scheme, parts.netloc, path + "/models", "", ""))
        fingerprint = hashlib.sha256(json.dumps(headers, sort_keys=True).encode()).digest()
        key = (endpoint, runtime.model, fingerprint)
        lock_key = (asyncio.get_running_loop(), key)
        lock = self._locks.get(lock_key)
        if lock is None:
            lock = self._locks[lock_key] = asyncio.Lock()
        async with lock:
            cached = self._cache.get(key)
            if cached is not None and cached[0] > time.monotonic():
                window, output = cached[1:]
            else:
                window, output = await self._fetch(endpoint, headers, runtime.model)
                self._cache[key] = (time.monotonic() + (3600 if window or output else 60), window, output)
            self._cache.move_to_end(key)
            if len(self._cache) > 256:
                self._cache.popitem(last=False)
        return replace(runtime, discovered_context_window=window, discovered_output_limit=output)

    async def _fetch(self, endpoint: str, headers: dict[str, str], model: str) -> tuple[int | None, int | None]:
        try:
            async with asyncio.timeout(2):
                async with httpx.AsyncClient(verify=settings.llm_ssl_verify, follow_redirects=False) as client:
                    async with client.stream("GET", endpoint, headers=headers) as response:
                        response.raise_for_status()
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > 1_048_576:
                                return None, None
                            body.extend(chunk)
            data = json.loads(body)
            cards = data.get("data") if isinstance(data, dict) else None
            if not isinstance(cards, list):
                return None, None
            card = next((item for item in cards if isinstance(item, dict) and item.get("id") == model), {})
            limit = card.get("limit")
            limit = limit if isinstance(limit, dict) else {}

            def positive(*values):
                return next((value for value in values if type(value) is int and 0 < value <= 100_000_000), None)

            return (
                positive(card.get("max_model_len"), card.get("context_length"), card.get("max_context_length"), limit.get("context")),
                positive(card.get("max_output_tokens"), card.get("max_completion_tokens"), limit.get("output")),
            )
        except (httpx.HTTPError, ValueError, TimeoutError):
            return None, None


model_limit_discovery = ModelLimitDiscovery()
