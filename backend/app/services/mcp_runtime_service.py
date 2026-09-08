from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import time
import uuid
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from app.core.config import settings
from app.services.session_types import AgentSession
from app.services.store import store_service
from app.services.session_runtime_service import session_runtime_service
from app.services.secret_store_service import secret_store_service


class McpRuntimeError(RuntimeError):
    pass


class McpRuntimeService:
    """以短连接方式发现和调用 MCP，避免跨事件循环泄漏连接与子进程。"""

    def __init__(self) -> None:
        self._oauth_flows: dict[str, dict[str, Any]] = {}
        self._catalog_cache: dict[str, dict[str, Any]] = {}

    async def discover(self, session: AgentSession, config: dict[str, Any], *, auth_provider=None) -> list[dict[str, Any]]:
        async with await self._client(session, config, auth_provider=auth_provider) as client:
            result = await asyncio.wait_for(client.list_tools(), timeout=self._timeout(config, "startup_timeout_sec", 10))
        return [
            {
                "name": self.tool_name(config["name"], tool.name),
                "description": tool.description or f"{config['name']} MCP tool",
                "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
                "kind": "mcp",
                "mcp_server": config["name"],
                "mcp_tool": tool.name,
                "mcp_config_id": config["id"],
            }
            for tool in result.tools
        ]

    async def ensure_catalog(self, session: AgentSession) -> dict[str, str]:
        from app.services.capability_service import capability_service
        from app.services.tool_catalog_service import tool_catalog_service
        status: dict[str, str] = {}
        effective = capability_service.list_effective_mcp(session, include_secrets=True)
        effective_ids = {str(item["id"]) for item in effective}
        for source_id in list(session.host_tool_collections):
            if source_id.startswith("mcp:") and source_id.removeprefix("mcp:") not in effective_ids:
                tool_catalog_service.replace(session, [], source_id=source_id, replace_all=False)
        for config in effective:
            source_id = f"mcp:{config['id']}"
            cache_key = f"{session.session_id}:{config['id']}"
            cached = self._catalog_cache.get(cache_key)
            now = time.monotonic()
            if cached and cached.get("expires_at", 0) > now:
                descriptors = cached.get("descriptors", [])
                tool_catalog_service.replace(session, descriptors, source_id=source_id, replace_all=False)
                status[config["name"]] = "connected"
                continue
            if cached and cached.get("retry_at", 0) > now:
                status[config["name"]] = cached.get("status", "unavailable")
                continue
            try:
                descriptors = await self.discover(session, config)
                tool_catalog_service.replace(session, descriptors, source_id=source_id, replace_all=False)
                self._catalog_cache[cache_key] = {
                    "descriptors": descriptors,
                    "expires_at": now + 60,
                    "failures": 0,
                }
                status[config["name"]] = "connected"
            except Exception as exc:
                tool_catalog_service.replace(session, [], source_id=source_id, replace_all=False)
                failures = int((cached or {}).get("failures", 0)) + 1
                failure_status = f"failed: {str(exc)[:160]}"
                self._catalog_cache[cache_key] = {
                    "descriptors": [],
                    "failures": failures,
                    "retry_at": now + min(2 ** failures, 60),
                    "status": failure_status,
                }
                status[config["name"]] = failure_status
        active_keys = {f"{session.session_id}:{config['id']}" for config in effective}
        for cache_key in list(self._catalog_cache):
            if cache_key.startswith(f"{session.session_id}:") and cache_key not in active_keys:
                self._catalog_cache.pop(cache_key, None)
        return status

    async def invoke(self, session: AgentSession, config: dict[str, Any], tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with await self._client(session, config) as client:
            result = await asyncio.wait_for(
                client.call_tool(tool_name, arguments, read_timeout_seconds=timedelta(seconds=self._timeout(config, "tool_timeout_sec", 60))),
                timeout=self._timeout(config, "tool_timeout_sec", 60) + 1,
            )
        payload = result.model_dump(mode="json", exclude_none=True)
        encoded = str(payload)
        if len(encoded) > 200_000:
            raise McpRuntimeError("MCP 工具输出超过 200 KB 限制")
        return {"server": config["name"], "tool": tool_name, "result": payload}

    def resolve_config(self, session: AgentSession, config_id: str) -> dict[str, Any]:
        from app.services.capability_service import capability_service
        item = next((entry for entry in capability_service.list_effective_mcp(session, include_secrets=True) if entry.get("id") == config_id), None)
        if not item:
            raise McpRuntimeError("MCP 配置已删除或被禁用")
        return item

    async def _client(self, session: AgentSession, config: dict[str, Any], *, auth_provider=None):
        stack = AsyncExitStack()
        try:
            if config["transport"] == "stdio":
                if session.workspace is None:
                    raise McpRuntimeError("会话沙箱未初始化")
                await session_runtime_service.ensure_runtime(session.workspace)
                runtime = store_service.get_session_runtime(session.session_id)
                if not runtime or runtime.get("status") != "running" or not runtime.get("container_name"):
                    raise McpRuntimeError("STDIO MCP 需要已运行的会话沙箱")
                command = list(config.get("command") or []) + list(config.get("args") or [])
                docker_env: list[str] = []
                process_env = os.environ.copy()
                for key, value in (config.get("env") or {}).items():
                    # 只把变量名放进进程参数，避免凭据出现在宿主机进程列表中。
                    process_env[str(key)] = str(value)
                    docker_env.extend(["-e", str(key)])
                params = StdioServerParameters(
                    command=settings.sandbox_docker_command,
                    args=["exec", "-i", *docker_env, runtime["container_name"], *command],
                    env=process_env,
                )
                read, write = await stack.enter_async_context(stdio_client(params))
            else:
                await self._validate_remote_url(str(config["url"]))
                if config.get("auth") == "oauth" and auth_provider is None:
                    auth_provider = self._oauth_provider(config)
                client = httpx.AsyncClient(
                    headers={str(k): str(v) for k, v in (config.get("headers") or {}).items()},
                    auth=auth_provider,
                    verify=settings.http_client_ssl_verify,
                    follow_redirects=False,
                    timeout=self._timeout(config, "tool_timeout_sec", 60),
                )
                await stack.enter_async_context(client)
                read, write, _ = await stack.enter_async_context(streamable_http_client(str(config["url"]), http_client=client))
            mcp_session = await stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(mcp_session.initialize(), timeout=self._timeout(config, "startup_timeout_sec", 10))
            return _McpClientContext(stack, mcp_session)
        except Exception:
            await stack.aclose()
            raise

    async def begin_oauth(self, session: AgentSession, config: dict[str, Any]) -> dict[str, str]:
        if config.get("auth") != "oauth" or config.get("transport") != "streamable_http":
            raise McpRuntimeError("该 MCP 未配置 OAuth")
        flow_id = f"oauth_{uuid.uuid4().hex}"
        loop = asyncio.get_running_loop()
        redirect_ready: asyncio.Future[str] = loop.create_future()
        callback_ready: asyncio.Future[tuple[str, str | None]] = loop.create_future()

        async def redirect_handler(url: str) -> None:
            if not redirect_ready.done():
                redirect_ready.set_result(url)

        async def callback_handler() -> tuple[str, str | None]:
            return await asyncio.wait_for(callback_ready, timeout=300)

        provider = self._oauth_provider(
            config, redirect_handler=redirect_handler, callback_handler=callback_handler
        )
        task = asyncio.create_task(self.discover(session, config, auth_provider=provider))
        flow = {"id": flow_id, "callback": callback_ready, "task": task, "state": None}
        self._oauth_flows[flow_id] = flow
        try:
            authorization_url = await asyncio.wait_for(redirect_ready, timeout=30)
        except Exception:
            task.cancel()
            self._oauth_flows.pop(flow_id, None)
            raise
        state = (parse_qs(urlparse(authorization_url).query).get("state") or [None])[0]
        if not state:
            task.cancel()
            self._oauth_flows.pop(flow_id, None)
            raise McpRuntimeError("OAuth 服务未返回 state")
        flow["state"] = state
        self._oauth_flows[state] = flow
        def cleanup(completed: asyncio.Task) -> None:
            self._oauth_flows.pop(state, None)
            self._oauth_flows.pop(flow_id, None)
            if not completed.cancelled():
                completed.exception()
        task.add_done_callback(cleanup)
        return {"authorization_url": authorization_url, "state": state}

    async def complete_oauth(self, *, code: str, state: str) -> None:
        flow = self._oauth_flows.get(state)
        if not flow:
            raise McpRuntimeError("OAuth 授权已过期，请重新发起")
        callback = flow["callback"]
        if not callback.done():
            callback.set_result((code, state))
        try:
            await asyncio.wait_for(asyncio.shield(flow["task"]), timeout=60)
        finally:
            self._oauth_flows.pop(state, None)
            self._oauth_flows.pop(flow["id"], None)

    def revoke_oauth(self, config: dict[str, Any]) -> None:
        secret_ref = config.get("secret_ref")
        values = secret_store_service.get(secret_ref)
        values.pop("oauth_tokens", None)
        values.pop("oauth_client", None)
        if secret_ref:
            secret_store_service.put(values, secret_ref)

    def _oauth_provider(self, config: dict[str, Any], *, redirect_handler=None, callback_handler=None) -> OAuthClientProvider:
        secret_ref = config.get("secret_ref")
        if not secret_ref:
            raise McpRuntimeError("OAuth 凭据存储未初始化")
        callback_url = f"{settings.resolved_app_public_base_url}/api/v1/capabilities/mcp/oauth/callback"
        metadata = OAuthClientMetadata(
            redirect_uris=[callback_url],
            client_name="AetherCore",
            scope=config.get("oauth_scopes") or None,
        )
        return OAuthClientProvider(
            str(config["url"]),
            metadata,
            _EncryptedOAuthStorage(secret_ref),
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            timeout=300,
        )

    async def _validate_remote_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise McpRuntimeError("远程 MCP 仅允许 HTTPS")
        infos = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        for info in infos:
            address = ipaddress.ip_address(info[4][0])
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast:
                raise McpRuntimeError("远程 MCP 地址解析到受保护网络")

    @staticmethod
    def tool_name(server: str, tool: str) -> str:
        return f"mcp__{server}__{tool}"[:128]

    @staticmethod
    def _timeout(config: dict[str, Any], key: str, default: int) -> int:
        return min(max(int(config.get(key, default)), 1), 300)


class _McpClientContext:
    def __init__(self, stack: AsyncExitStack, session: ClientSession) -> None:
        self.stack = stack
        self.session = session

    async def __aenter__(self) -> ClientSession:
        return self.session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stack.aclose()


class _EncryptedOAuthStorage:
    def __init__(self, secret_ref: str) -> None:
        self.secret_ref = secret_ref

    async def get_tokens(self) -> OAuthToken | None:
        value = secret_store_service.get(self.secret_ref).get("oauth_tokens")
        return OAuthToken.model_validate(value) if value else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        values = secret_store_service.get(self.secret_ref)
        values["oauth_tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        secret_store_service.put(values, self.secret_ref)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        value = secret_store_service.get(self.secret_ref).get("oauth_client")
        return OAuthClientInformationFull.model_validate(value) if value else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        values = secret_store_service.get(self.secret_ref)
        values["oauth_client"] = client_info.model_dump(mode="json", exclude_none=True)
        secret_store_service.put(values, self.secret_ref)


mcp_runtime_service = McpRuntimeService()
