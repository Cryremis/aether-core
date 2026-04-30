# backend/app/services/tool_service.py
"""统一管理内置工具与宿主工具代理。"""
from __future__ import annotations

import ast
import inspect
import json
import re
from typing import Any, Callable, Awaitable
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.sandbox.runner import sandbox_runner
from app.services.artifact_service import artifact_service
from app.services.file_service import file_service
from app.services.llm_config_service import RuntimeLlmConfig, llm_config_service
from app.services.network_service import network_service
from app.services.runtime_state import runtime_state_service
from app.services.session_service import session_service
from app.services.session_types import AgentSession
from app.services.search_service import search_service
from app.services.skill_service import skill_service
from app.services.store import store_service
from app.services.session_runtime_service import RuntimeBusyError, session_runtime_service


ToolHandler = Callable[[AgentSession, dict[str, Any]], Awaitable[dict[str, Any]]]


def _is_absolute_url(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme and parsed.netloc)


def _same_origin(left: str, right: str) -> bool:
    left_parsed = urlparse(left)
    right_parsed = urlparse(right)
    return (left_parsed.scheme, left_parsed.netloc) == (right_parsed.scheme, right_parsed.netloc)


class ToolRegistry:
    """工具注册表，管理工具 schema 和 handler。"""

    def __init__(self) -> None:
        self._schemas: list[dict[str, Any]] = []
        self._handlers: dict[str, ToolHandler] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: ToolHandler,
        required: list[str] | None = None,
    ) -> None:
        """注册一个工具。"""
        schema = {
            "type": "object",
            "properties": parameters.get("properties", {}),
            "additionalProperties": parameters.get("additionalProperties", False),
        }
        if required:
            schema["required"] = required

        self._schemas.append({
            "type": "function",
            "function": {"name": name, "description": description, "parameters": schema},
        })
        self._handlers[name] = handler

    def get_schemas(self) -> list[dict[str, Any]]:
        """返回所有已注册工具的 schema。"""
        return self._schemas.copy()

    def get_handler(self, name: str) -> ToolHandler | None:
        """返回指定工具的 handler。"""
        return self._handlers.get(name)


class ToolService:
    """统一管理内置工具与宿主工具代理。"""

    def __init__(self) -> None:
        self._registry = ToolRegistry()
        self._register_builtin_tools()

    def _register_builtin_tools(self) -> None:
        """注册所有内置工具。"""
        self._registry.register(
            "update_workboard",
            "用结构化操作更新当前会话的任务清单，让 AI 和用户都能在聊天记录之外持续看到计划与进度。优先使用 add_item、update_item、remove_item、reorder_items 等细粒度操作。",
            {
                "properties": {
                    "status": {"type": "string", "enum": ["idle", "active", "completed", "blocked"]},
                    "archive_completed": {"type": "boolean"},
                    "ops": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {
                                    "type": "string",
                                    "enum": ["add_item", "update_item", "remove_item", "reorder_items", "replace_all"],
                                },
                                "id": {"type": "string"},
                                "title": {"type": "string"},
                                "active_form": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed", "blocked", "cancelled"],
                                },
                                "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                                "owner": {"type": "string"},
                                "depends_on": {"type": "array", "items": {"type": "string"}},
                                "blocked_by": {"type": "array", "items": {"type": "string"}},
                                "notes": {"type": "string"},
                                "source": {"type": "string"},
                                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                                "ordered_ids": {"type": "array", "items": {"type": "string"}},
                                "items": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "title": {"type": "string"},
                                            "active_form": {"type": "string"},
                                            "status": {
                                                "type": "string",
                                                "enum": ["pending", "in_progress", "completed", "blocked", "cancelled"],
                                            },
                                            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                                            "owner": {"type": "string"},
                                            "depends_on": {"type": "array", "items": {"type": "string"}},
                                            "blocked_by": {"type": "array", "items": {"type": "string"}},
                                            "notes": {"type": "string"},
                                            "source": {"type": "string"},
                                            "evidence_refs": {"type": "array", "items": {"type": "string"}},
                                        },
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["op"],
                            "additionalProperties": False,
                        },
                    },
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "title": {"type": "string"},
                                "active_form": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed", "blocked", "cancelled"],
                                },
                                "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                                "owner": {"type": "string"},
                                "depends_on": {"type": "array", "items": {"type": "string"}},
                                "blocked_by": {"type": "array", "items": {"type": "string"}},
                                "notes": {"type": "string"},
                                "source": {"type": "string"},
                                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["title", "status"],
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
            self._handle_update_workboard,
            required=[],
        )

        self._registry.register(
            "request_user_input",
            "当你遇到不确定、被阻塞、需要用户明确决策或补充信息时，向用户发起结构化提问。它会在前端生成可持续存在的待回复状态，而不是只在普通聊天文本里提问。",
            {
                "properties": {
                    "id": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["clarification", "confirmation", "decision", "missing_info", "approval"],
                    },
                    "title": {"type": "string"},
                    "blocking": {"type": "boolean"},
                    "source_agent": {"type": "string"},
                    "related_work_items": {"type": "array", "items": {"type": "string"}},
                    "preview_text": {"type": "string"},
                    "questions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "header": {"type": "string"},
                                "question": {"type": "string"},
                                "multi_select": {"type": "boolean"},
                                "allow_other": {"type": "boolean"},
                                "allow_notes": {"type": "boolean"},
                                "options": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {"type": "string"},
                                            "description": {"type": "string"},
                                        },
                                        "required": ["label"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["header", "question"],
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
            self._handle_request_user_input,
            required=["title", "questions"],
        )
        self._registry.register(
            "invoke_skill",
            "加载一个真实技能包，把该技能的 SKILL.md 指令注入当前对话上下文。若任务明显匹配某个技能，必须先调用它。",
            {"properties": {"skill_name": {"type": "string"}}, "additionalProperties": False},
            self._handle_invoke_skill,
            required=["skill_name"],
        )

        self._registry.register(
            "list_skills",
            "列出当前会话可见技能。",
            {"properties": {}, "additionalProperties": False},
            self._handle_list_skills,
        )

        self._registry.register(
            "list",
            "列出沙箱目录内容。默认以沙箱根目录 `/workspace` 为起点，可用于查看 input/work/output/skills/logs 等目录。",
            {
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                },
                "additionalProperties": False,
            },
            self._handle_list,
        )

        self._registry.register(
            "read",
            "读取沙箱中的文本文件。默认以沙箱根目录 `/workspace` 为基准解析路径，可按行偏移读取。",
            {
                "properties": {
                    "file_id": {"type": "string"},
                    "file_path": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "additionalProperties": False,
            },
            self._handle_read,
        )

        self._registry.register(
            "create_text_artifact",
            "在输出目录创建文本产物，便于用户下载。",
            {
                "properties": {"name": {"type": "string"}, "content": {"type": "string"}},
                "additionalProperties": False,
            },
            self._handle_create_text_artifact,
            required=["name", "content"],
        )

        self._registry.register(
            "sandbox_shell",
            "在受限容器沙箱内执行命令，支持 bash 与 powershell。",
            {
                "properties": {
                    "command": {"type": "string"},
                    "shell": {"type": "string", "enum": ["powershell", "bash"]},
                    "timeout_seconds": {"type": "integer", "minimum": 1},
                },
                "additionalProperties": False,
            },
            self._handle_sandbox_shell,
            required=["command"],
        )
        self._registry.register(
            "rebuild_runtime",
            "重建当前会话的持久化沙箱 runtime。当 runtime 卡住、退出异常或容器状态不一致时使用。",
            {
                "properties": {
                    "reason": {"type": "string"},
                },
                "additionalProperties": False,
            },
            self._handle_rebuild_runtime,
        )

        # 注册搜索工具（来自 search_service）
        for schema_info in search_service.get_schemas():
            self._registry.register(
                schema_info["name"],
                schema_info["description"],
                schema_info["parameters"],
                lambda session, args, name=schema_info["name"]: (
                    search_service.execute_glob(session, args)
                    if name == "glob"
                    else search_service.execute_grep(session, args)
                ),
                required=["pattern"],
            )

    async def _handle_invoke_skill(self, session: AgentSession, arguments: dict[str, Any]) -> dict[str, Any]:
        return skill_service.invoke_skill(session, skill_name=str(arguments["skill_name"]))

    async def _handle_update_workboard(self, session: AgentSession, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments.get("ops"), list) and not isinstance(arguments.get("items"), list):
            raise RuntimeError("update_workboard requires either ops or items")
        state = runtime_state_service.update_workboard(session, arguments)
        return {
            "workboard": state.model_dump(mode="json"),
            "public_output": {
                "summary": f"任务清单已更新，共 {len(state.items)} 项",
                "revision": state.revision,
                "status": state.status,
            },
            "runtime_events": [
                {
                    "type": "workboard_updated",
                    "payload": {
                        "snapshot": state.model_dump(mode="json"),
                    },
                }
            ],
        }

    async def _handle_request_user_input(self, session: AgentSession, arguments: dict[str, Any]) -> dict[str, Any]:
        request = runtime_state_service.request_user_input(session, arguments)
        state = runtime_state_service.get_elicitation(session)
        return {
            "elicitation": request.model_dump(mode="json"),
            "public_output": {
                "summary": f"已发起用户提问：{request.title}",
                "request_id": request.id,
                "blocking": request.blocking,
            },
            "runtime_events": [
                {
                    "type": "ask_requested",
                    "payload": {
                        "request": request.model_dump(mode="json"),
                        "snapshot": state.model_dump(mode="json"),
                    },
                }
            ],
            "control": {
                "type": "await_user_input",
                "request_id": request.id,
                "blocking": request.blocking,
                "title": request.title,
            },
        }

    async def _handle_list_skills(self, session: AgentSession, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"items": [item.model_dump(mode="json") for item in skill_service.list_for_session(session)]}

    async def _handle_list(self, session: AgentSession, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "path": arguments.get("path") or "/workspace",
            "items": [
                {
                    "path": item.path,
                    "name": item.name,
                    "type": item.entry_type,
                    "size": item.size,
                    "source": item.source,
                    "file_id": item.file_id,
                }
                for item in file_service.list(
                    session,
                    path=arguments.get("path"),
                    limit=int(arguments.get("limit") or 200),
                )
            ],
        }

    async def _handle_read(self, session: AgentSession, arguments: dict[str, Any]) -> dict[str, Any]:
        result = file_service.read(
            session,
            file_id=arguments.get("file_id"),
            file_path=arguments.get("file_path"),
            offset=int(arguments.get("offset") or 1),
            limit=int(arguments["limit"]) if arguments.get("limit") is not None else None,
        )
        return {
            "file": {
                "file_path": result.file_path,
                "content": result.content,
                "num_lines": result.num_lines,
                "start_line": result.start_line,
                "total_lines": result.total_lines,
                "truncated": result.truncated,
                "size": result.size,
            }
        }

    async def _handle_create_text_artifact(self, session: AgentSession, arguments: dict[str, Any]) -> dict[str, Any]:
        artifact = artifact_service.create_text_artifact(
            session=session,
            name=str(arguments["name"]),
            content=str(arguments["content"]),
        )
        return {"artifact": artifact.model_dump(mode="json")}

    async def _handle_rebuild_runtime(self, session: AgentSession, arguments: dict[str, Any]) -> dict[str, Any]:
        if session.workspace is None:
            raise RuntimeError("会话沙箱尚未初始化。")
        metadata = await session_runtime_service.rebuild_runtime(
            session.workspace,
            reason=str(arguments.get("reason") or "agent_requested_rebuild"),
        )
        return {
            "summary": "沙箱 runtime 已重建",
            "runtime": metadata,
            "runtime_events": [
                {
                    "type": "runtime_recreated" if metadata["status"] == "recreated" else "runtime_created",
                    "payload": metadata,
                }
            ],
        }

    async def _handle_sandbox_shell(
        self,
        session: AgentSession,
        arguments: dict[str, Any],
        *,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        if session.workspace is None:
            raise RuntimeError("会话沙箱尚未初始化。")
        runner_kwargs: dict[str, Any] = {
            "workspace": session.workspace,
            "command": str(arguments["command"]),
            "shell": arguments.get("shell"),
            "timeout_seconds": self._parse_int(arguments.get("timeout_seconds")),
        }
        try:
            signature = inspect.signature(sandbox_runner.run_shell)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            if "session" in signature.parameters:
                runner_kwargs["session"] = session
            if run_id is not None and "run_id" in signature.parameters:
                runner_kwargs["run_id"] = run_id
        try:
            result = await sandbox_runner.run_shell(**runner_kwargs)
        except RuntimeBusyError as exc:
            return {
                "summary": exc.summary,
                "error_code": "runtime_busy",
                "recoverable": True,
                "suggested_actions": list(exc.suggested_actions),
                "runtime": exc.runtime,
                "public_output": {
                    "summary": exc.summary,
                    "error_code": "runtime_busy",
                    "recoverable": True,
                    "suggested_actions": list(exc.suggested_actions),
                    "runtime": exc.runtime,
                },
            }
        artifact_service.sync_output_directory(session)
        response: dict[str, Any] = {
            "shell": result.shell,
            "executor": result.executor,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
            "log_path": result.log_path,
        }
        runtime_metadata = result.runtime_metadata or {}
        if runtime_metadata:
            response["runtime"] = runtime_metadata
        runtime_status = str(runtime_metadata.get("status") or "")
        if runtime_status in {"created", "recreated"}:
            response["runtime_events"] = [
                {
                    "type": "runtime_created" if runtime_status == "created" else "runtime_recreated",
                    "payload": runtime_metadata,
                }
            ]
        return response

    def list_tool_schemas(self, session: AgentSession) -> list[dict[str, Any]]:
        """返回会话可用的工具 schema 列表。"""
        tools = self._registry.get_schemas()
        runtime_config = self._resolve_runtime_config(session)

        # 动态添加网络工具
        if session.allow_network and runtime_config.network.enabled:
            if network_service.supports_web_search(runtime_config):
                tools.append(self._make_schema(
                    "web_search",
                    "联网搜索当前信息。优先走模型服务商原生联网搜索，若不可用则退回到受控搜索提供方。",
                    {
                        "properties": {
                            "query": {"type": "string"},
                            "allowed_domains": {"type": "array", "items": {"type": "string"}},
                            "blocked_domains": {"type": "array", "items": {"type": "string"}},
                            "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                ))
            tools.append(self._make_schema(
                "web_fetch",
                "抓取指定网页内容并返回文本、markdown 或 html。受域名策略、超时和大小限制约束。",
                {
                    "properties": {
                        "url": {"type": "string"},
                        "format": {"type": "string", "enum": ["markdown", "text", "html"]},
                        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
            ))

        # 添加宿主工具
        for descriptor in session.host_tools:
            tools.append(self._make_schema(
                descriptor["name"],
                f"{descriptor['description']}（宿主工具）",
                descriptor.get("input_schema") or {"type": "object", "properties": {}},
            ))

        return tools

    async def execute(
        self,
        session: AgentSession,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """执行指定工具。"""
        # 检查注册表中的内置工具
        handler = self._registry.get_handler(tool_name)
        if handler:
            if tool_name == "sandbox_shell":
                return await self._handle_sandbox_shell(session, arguments, run_id=run_id)
            if tool_name == "rebuild_runtime":
                return await self._handle_rebuild_runtime(session, arguments)
            return await handler(session, arguments)

        # 处理动态工具
        runtime_config = self._resolve_runtime_config(session)

        if tool_name == "web_search":
            return await network_service.web_search(
                session=session,
                runtime_config=runtime_config,
                query=str(arguments["query"]),
                allowed_domains=[str(item) for item in arguments.get("allowed_domains", [])],
                blocked_domains=[str(item) for item in arguments.get("blocked_domains", [])],
                max_results=self._parse_int(arguments.get("max_results")),
            )

        if tool_name == "web_fetch":
            return await network_service.web_fetch(
                runtime_config=runtime_config,
                url=str(arguments["url"]),
                format_type=str(arguments.get("format") or "markdown"),
                timeout_seconds=self._parse_int(arguments.get("timeout_seconds")),
            )

        # 处理宿主工具
        descriptor = next((item for item in session.host_tools if item["name"] == tool_name), None)
        if descriptor:
            return await self._invoke_host_tool(session, descriptor, arguments)

        raise RuntimeError(f"未知工具: {tool_name}")

    async def _invoke_host_tool(
        self,
        session: AgentSession,
        descriptor: dict[str, Any],
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """调用宿主工具，自动注入认证并处理 token 刷新。"""
        base_url = self._get_host_base_url(session)
        endpoint = self._resolve_host_url(
            session,
            descriptor["endpoint"],
            field_name="endpoint",
            require_base_url=True,
        )

        headers = dict(descriptor.get("headers") or {})
        auth = session.host_context.get("auth") if session.host_context else None
        if descriptor.get("requires_auth", True) and descriptor.get("auth_inject", True) and not auth:
            raise RuntimeError(f"宿主工具 {descriptor['name']} 要求认证，但当前会话未提供 host auth。")
        if descriptor.get("auth_inject", True):
            if auth:
                headers.update(self._build_auth_headers(auth))

        request_payload = {
            "session_id": session.session_id,
            "arguments": arguments,
            "context": session.host_context,
        }

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.request(
                method=descriptor.get("method", "POST"),
                url=endpoint,
                headers=headers,
                json=request_payload,
            )

            if response.status_code == 401:
                new_token = await self._refresh_token(session)
                if new_token:
                    auth = session.host_context.get("auth") if session.host_context else None
                    if auth:
                        headers = dict(descriptor.get("headers") or {})
                        headers.update(self._build_auth_headers(auth))
                        response = await client.request(
                            method=descriptor.get("method", "POST"),
                            url=endpoint,
                            headers=headers,
                            json=request_payload,
                        )

            response.raise_for_status()
            data = response.json()

        return data if isinstance(data, dict) else {"result": data}

    def _get_host_base_url(self, session: AgentSession) -> str | None:
        if not session.host_context:
            return None
        base_url = session.host_context.get("extras", {}).get("host_callback_base_url")
        if not base_url:
            return None
        return str(base_url).rstrip("/")

    def _resolve_host_url(
        self,
        session: AgentSession,
        raw_url: str,
        *,
        field_name: str,
        require_base_url: bool,
    ) -> str:
        base_url = self._get_host_base_url(session)
        if raw_url.startswith("/"):
            if not base_url and require_base_url:
                raise RuntimeError(f"宿主 {field_name} 使用相对路径，但未提供 host_callback_base_url。")
            if not base_url:
                return raw_url
            return f"{base_url}{raw_url}"
        if _is_absolute_url(raw_url):
            if base_url and not _same_origin(base_url, raw_url):
                raise RuntimeError(f"宿主 {field_name} 必须与 host_callback_base_url 保持同源。")
            return raw_url
        raise RuntimeError(f"宿主 {field_name} 必须是绝对 URL 或以 / 开头的相对路径。")

    def _build_auth_headers(self, auth: dict[str, Any]) -> dict[str, str]:
        """从 host_context.auth 构建认证 headers。"""
        headers = {}
        token = auth.get("token")
        if token:
            header_name = auth.get("token_header", "Authorization")
            prefix = auth.get("token_prefix", "Bearer")
            headers[header_name] = f"{prefix} {token}"
        custom_headers = auth.get("custom_headers") or {}
        headers.update(custom_headers)
        return headers

    async def _refresh_token(self, session: AgentSession) -> str | None:
        """尝试刷新 token，返回新 token 或 None。"""
        auth = session.host_context.get("auth") if session.host_context else None
        if not auth:
            return None
        refresh_token = auth.get("refresh_token")
        refresh_endpoint = auth.get("refresh_endpoint")
        if not refresh_token or not refresh_endpoint:
            return None
        refresh_endpoint = self._resolve_host_url(
            session,
            str(refresh_endpoint),
            field_name="refresh_endpoint",
            require_base_url=False,
        )
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.request(
                    method="POST",
                    url=refresh_endpoint,
                    json={"refresh_token": refresh_token, "session_id": session.session_id},
                )
                if response.status_code != 200:
                    return None
                data = response.json()
                new_token = data.get("token")
                if new_token:
                    session.host_context["auth"]["token"] = new_token
                    if data.get("expires_at"):
                        session.host_context["auth"]["expires_at"] = data["expires_at"]
                    if data.get("refresh_token"):
                        session.host_context["auth"]["refresh_token"] = data["refresh_token"]
                    session_service.persist(session)
                    return new_token
        except Exception:
            return None
        return None

    def parse_tool_arguments(self, raw_arguments: str) -> dict[str, Any]:
        """解析工具参数 JSON。"""
        if not raw_arguments:
            return {}

        stripped = raw_arguments.strip()
        if stripped in {"{", "}", "{}"}:
            return {}

        try:
            return json.loads(raw_arguments)
        except json.JSONDecodeError:
            return self._repair_json(stripped)

    def _repair_json(self, cleaned: str) -> dict[str, Any]:
        """尝试修复损坏的 JSON。"""
        cleaned = cleaned.strip().strip("`")

        brace_delta = cleaned.count("{") - cleaned.count("}")
        if brace_delta > 0:
            cleaned = cleaned + ("}" * brace_delta)

        normalized = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_-]*)(\s*:)", r'\1"\2"\3', cleaned)
        normalized = normalized.replace("None", "null").replace("True", "true").replace("False", "false")

        try:
            return json.loads(normalized)
        except json.JSONDecodeError:
            parsed = ast.literal_eval(cleaned)
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}

    def _make_schema(self, name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """构建工具 schema。"""
        return {
            "type": "function",
            "function": {"name": name, "description": description, "parameters": parameters},
        }

    def _parse_int(self, value: Any) -> int | None:
        """安全解析整数。"""
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _resolve_runtime_config(self, session: AgentSession) -> RuntimeLlmConfig:
        """解析运行时配置。"""
        conversation = store_service.get_conversation_by_session(session.session_id)
        if conversation is not None:
            return llm_config_service.resolve_for_conversation(conversation)

        return RuntimeLlmConfig(
            scope="global",
            provider_kind="litellm",
            api_format="openai-compatible",
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            extra_headers={},
            extra_body={},
            network=llm_config_service.get_global_summary().network,
            enabled=True,
        )


tool_service = ToolService()
