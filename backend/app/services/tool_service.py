# backend/app/services/tool_service.py
"""统一管理内置工具与宿主工具代理。"""
from __future__ import annotations

import asyncio
import ast
import copy
import inspect
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping
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
from app.services.tool_execution_service import ToolOutputEvent, tool_execution_service
from app.services.tool_catalog_service import HostToolCatalogSnapshot, tool_catalog_service
from app.services.tool_result_service import ToolExecutionResult
from app.services.search_service import search_service
from app.services.skill_service import skill_service
from app.services.store import store_service
from app.services.workspace_runtime_service import RuntimeBusyError, RuntimeStartError, workspace_runtime_service


ToolHandler = Callable[[AgentSession, dict[str, Any]], Awaitable[ToolExecutionResult]]

SUBAGENT_TOOL_NAMES = {
    "subagent_create",
    "subagent_send_message",
    "subagent_wait",
    "subagent_list",
    "subagent_cancel",
    "subagent_get_result",
}


@dataclass(frozen=True)
class ToolCatalogSnapshot:
    """Immutable model-facing schemas and matching host execution descriptors."""

    revision: int
    fingerprint: str
    schemas: tuple[dict[str, Any], ...]
    host_descriptors_by_name: Mapping[str, dict[str, Any]]


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
            "subagent_create",
            "创建一个只执行一层任务的子 Agent。子 Agent 不再拥有 subagent 工具，用户可以只读查看它的状态和最终回复。",
            {
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 128},
                    "task": {"type": "string", "minLength": 1},
                    "instructions": {"type": "string"},
                    "allowed_tools": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "task"],
                "additionalProperties": False,
            },
            self._handle_subagent_tool,
            required=["name", "task"],
        )
        self._registry.register(
            "subagent_send_message",
            "向已完成的子 Agent 发送追加消息，进行下一轮沟通。句柄使用 subagent_id，不是 latest_run_id。",
            {
                "properties": {
                    "subagent_id": {"type": "string"},
                    "message": {"type": "string", "minLength": 1},
                },
                "required": ["subagent_id", "message"],
                "additionalProperties": False,
            },
            self._handle_subagent_tool,
            required=["subagent_id", "message"],
        )
        self._registry.register(
            "subagent_wait",
            "等待一个或全部子 Agent 完成，并返回它们的最终结果。",
            {
                "properties": {
                    "subagent_id": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "minimum": 1},
                },
                "additionalProperties": False,
            },
            self._handle_subagent_tool,
        )
        self._registry.register(
            "subagent_list",
            "列出当前主 Agent 会话下的子 Agent 与运行状态。",
            {"properties": {}, "additionalProperties": False},
            self._handle_subagent_tool,
        )
        self._registry.register(
            "subagent_cancel",
            "停止一个子 Agent，并等待它进入终态后返回真实结果。",
            {
                "properties": {"subagent_id": {"type": "string"}},
                "required": ["subagent_id"],
                "additionalProperties": False,
            },
            self._handle_subagent_tool,
            required=["subagent_id"],
        )
        self._registry.register(
            "subagent_get_result",
            "读取一个子 Agent 的当前结果或错误；通常优先使用 subagent_wait 等待主动回传。",
            {
                "properties": {"subagent_id": {"type": "string"}},
                "required": ["subagent_id"],
                "additionalProperties": False,
            },
            self._handle_subagent_tool,
            required=["subagent_id"],
        )

        self._registry.register(
            "list_skills",
            "列出当前会话可见技能。",
            {"properties": {}, "additionalProperties": False},
            self._handle_list_skills,
        )

        self._registry.register(
            "list",
            "列出沙箱目录内容。默认以沙箱根目录 `/workspace` 为起点，可用于查看 work/skills/logs 等目录。",
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
            "在工作目录创建文本产物，便于用户预览、编辑和下载。",
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
                self._handle_glob if schema_info["name"] == "glob" else self._handle_grep,
                required=["pattern"],
            )

    async def _handle_glob(self, session: AgentSession, arguments: dict[str, Any]) -> ToolExecutionResult:
        result = await search_service.execute_glob(session, arguments)
        filenames = result.get("filenames", [])
        return ToolExecutionResult.success(
            "search.glob",
            f"匹配到 {result.get('num_files', 0)} 个文件",
            {
                "pattern": arguments.get("pattern", ""),
                "files": filenames,
                "num_files": result.get("num_files", 0),
                "truncated": result.get("truncated", False),
                "duration_ms": result.get("duration_ms", 0),
                "next": "使用更具体的 path 或 pattern 缩小范围。" if result.get("truncated") else "",
            },
        )

    async def _handle_grep(self, session: AgentSession, arguments: dict[str, Any]) -> ToolExecutionResult:
        result = await search_service.execute_grep(session, arguments)
        mode = str(arguments.get("output_mode") or "files_with_matches")
        if mode == "content":
            summary = f"匹配到 {result.get('num_lines', 0)} 行"
        elif mode == "count":
            summary = f"匹配到 {result.get('num_matches', 0)} 次，分布在 {result.get('num_files', 0)} 个文件"
        else:
            summary = f"匹配到 {result.get('num_files', 0)} 个文件"
        return ToolExecutionResult.success(
            "search.grep",
            summary,
            {
                "mode": mode,
                "content": result.get("content", ""),
                "num_lines": result.get("num_lines", 0),
                "num_matches": result.get("num_matches", 0),
                "num_files": result.get("num_files", 0),
                "truncated": result.get("truncated", False),
                "duration_ms": result.get("duration_ms", 0),
            },
        )

    async def _handle_invoke_skill(self, session: AgentSession, arguments: dict[str, Any]) -> ToolExecutionResult:
        payload = skill_service.invoke_skill(session, skill_name=str(arguments["skill_name"]))
        skill = payload["skill"]
        return ToolExecutionResult.success(
            "skill.loaded",
            f"技能 {skill['name']} 已加载",
            {
                "skill_name": skill["name"],
                "description": skill["description"],
                "source": skill["source"],
                "allowed_tools": skill.get("allowed_tools", []),
                "tags": skill.get("tags", []),
            },
            injected_messages=payload["injected_messages"],
        )

    async def _handle_update_workboard(self, session: AgentSession, arguments: dict[str, Any]) -> ToolExecutionResult:
        if not isinstance(arguments.get("ops"), list) and not isinstance(arguments.get("items"), list):
            raise RuntimeError("update_workboard requires either ops or items")
        state = runtime_state_service.update_workboard(session, arguments)
        snapshot = state.model_dump(mode="json")
        open_item_count = sum(item.status not in {"completed", "cancelled"} for item in state.items)
        return ToolExecutionResult.success(
            "workboard.updated",
            f"任务清单已更新，共 {len(state.items)} 项",
            {
                "revision": state.revision,
                "status": state.status,
                "item_count": len(state.items),
                "open_item_count": open_item_count,
            },
            runtime_events=[
                {
                    "type": "workboard_updated",
                    "payload": {
                        "snapshot": snapshot,
                    },
                }
            ],
        )

    async def _handle_request_user_input(self, session: AgentSession, arguments: dict[str, Any]) -> ToolExecutionResult:
        request = runtime_state_service.request_user_input(session, arguments)
        state = runtime_state_service.get_elicitation(session)
        request_payload = request.model_dump(mode="json")
        return ToolExecutionResult.success(
            "elicitation.requested",
            f"已发起用户提问：{request.title}",
            {
                "request_id": request.id,
                "title": request.title,
                "blocking": request.blocking,
            },
            runtime_events=[
                {
                    "type": "ask_requested",
                    "payload": {
                        "request": request_payload,
                        "snapshot": state.model_dump(mode="json"),
                    },
                }
            ],
            control={
                "type": "await_user_input",
                "request_id": request.id,
                "blocking": request.blocking,
                "title": request.title,
            },
        )

    async def _handle_list_skills(self, session: AgentSession, arguments: dict[str, Any]) -> ToolExecutionResult:
        skills = [item.model_dump(mode="json") for item in skill_service.list_for_session(session)]
        return ToolExecutionResult.success(
            "skills.listed",
            f"当前会话可见 {len(skills)} 个技能",
            {"count": len(skills), "skills": skills},
        )

    async def _handle_list(self, session: AgentSession, arguments: dict[str, Any]) -> ToolExecutionResult:
        path = arguments.get("path") or "/workspace"
        items = [
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
                    path=path,
                    limit=int(arguments.get("limit") or 200),
                )
        ]
        return ToolExecutionResult.success(
            "workspace.listed",
            f"{path} 下共有 {len(items)} 个条目",
            {"path": path, "item_count": len(items), "items": items},
        )

    async def _handle_read(self, session: AgentSession, arguments: dict[str, Any]) -> ToolExecutionResult:
        result = file_service.read(
            session,
            file_id=arguments.get("file_id"),
            file_path=arguments.get("file_path"),
            offset=int(arguments.get("offset") or 1),
            limit=int(arguments["limit"]) if arguments.get("limit") is not None else None,
        )
        return ToolExecutionResult.success(
            "file.read",
            f"已读取 {result.file_path}",
            {
                "file_path": result.file_path,
                "content": result.content,
                "start_line": result.start_line,
                "end_line": result.start_line + result.num_lines - 1,
                "total_lines": result.total_lines,
                "truncated": result.truncated,
                "size": result.size,
            },
        )

    async def _handle_create_text_artifact(self, session: AgentSession, arguments: dict[str, Any]) -> ToolExecutionResult:
        artifact = artifact_service.create_text_artifact(
            session=session,
            name=str(arguments["name"]),
            content=str(arguments["content"]),
        )
        payload = artifact.model_dump(mode="json")
        return ToolExecutionResult.success(
            "artifact.created",
            f"文本产物 {artifact.name} 已创建",
            {"artifact": payload},
            artifact=payload,
        )

    async def _handle_rebuild_runtime(self, session: AgentSession, arguments: dict[str, Any]) -> ToolExecutionResult:
        if session.workspace is None:
            raise RuntimeError("会话沙箱尚未初始化。")
        metadata = await workspace_runtime_service.rebuild_runtime(
            session.workspace,
            reason=str(arguments.get("reason") or "agent_requested_rebuild"),
        )
        return ToolExecutionResult.success(
            "runtime.rebuilt",
            "沙箱 runtime 已重建",
            metadata,
            runtime_events=[
                {
                    "type": "runtime_recreated" if metadata["status"] == "recreated" else "runtime_created",
                    "payload": metadata,
                }
            ],
        )

    async def _handle_sandbox_shell(
        self,
        session: AgentSession,
        arguments: dict[str, Any],
        *,
        run_id: str | None = None,
        tool_call_id: str | None = None,
        output_event_callback: Callable[[ToolOutputEvent], Awaitable[None]] | None = None,
    ) -> ToolExecutionResult:
        if session.workspace is None:
            raise RuntimeError("会话沙箱尚未初始化。")
        runner_kwargs: dict[str, Any] = {
            "workspace": session.workspace,
            "command": str(arguments["command"]),
            "shell": arguments.get("shell"),
            "timeout_seconds": self._parse_int(arguments.get("timeout_seconds")),
        }
        streaming_enabled = bool(tool_call_id)
        if streaming_enabled:
            tool_execution_service.begin_execution(
                session,
                tool_call_id=tool_call_id,
                tool_name="sandbox_shell",
            )

        async def output_callback(stream_name: str, text: str) -> None:
            if not streaming_enabled or tool_call_id is None:
                return
            event = await tool_execution_service.append_output(
                session,
                tool_call_id=tool_call_id,
                tool_name="sandbox_shell",
                stream=stream_name,
                text=text,
            )
            if output_event_callback is not None:
                await output_event_callback(event)

        try:
            signature = inspect.signature(sandbox_runner.run_shell)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            if "session" in signature.parameters:
                runner_kwargs["session"] = session
            if run_id is not None and "run_id" in signature.parameters:
                runner_kwargs["run_id"] = run_id
            if streaming_enabled and "output_callback" in signature.parameters:
                runner_kwargs["output_callback"] = output_callback
        try:
            result = await sandbox_runner.run_shell(**runner_kwargs)
        except asyncio.CancelledError:
            if streaming_enabled and tool_call_id is not None:
                tool_execution_service.abort_execution(
                    session,
                    tool_call_id=tool_call_id,
                )
            raise
        except RuntimeBusyError as exc:
            if streaming_enabled and tool_call_id is not None:
                tool_execution_service.fail_execution(
                    session,
                    tool_call_id=tool_call_id,
                    error=exc.summary,
                )
            return ToolExecutionResult.failure(
                "shell.executed",
                "沙箱暂时不可用",
                code="RUNTIME_BUSY",
                message=exc.summary,
                retryable=True,
                next_action="; ".join(exc.suggested_actions) or "稍后重试，或调用 rebuild_runtime。",
                data={"runtime": exc.runtime},
            )
        except RuntimeStartError as exc:
            if streaming_enabled and tool_call_id is not None:
                tool_execution_service.fail_execution(
                    session,
                    tool_call_id=tool_call_id,
                    error=exc.summary,
                )
            return ToolExecutionResult.failure(
                "shell.executed",
                "沙箱 runtime 启动失败",
                code="RUNTIME_START_FAILED",
                message=exc.summary,
                retryable=True,
                next_action="调用 rebuild_runtime 重建沙箱。",
                data={"runtime": exc.runtime},
            )
        artifact_service.sync_work_directory(session)
        data: dict[str, Any] = {
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
            data["runtime"] = runtime_metadata
        runtime_status = str(runtime_metadata.get("status") or "")
        if runtime_status in {"created", "recreated"}:
            runtime_events = [
                {
                    "type": "runtime_created" if runtime_status == "created" else "runtime_recreated",
                    "payload": runtime_metadata,
                }
            ]
        else:
            runtime_events = []
        if streaming_enabled and tool_call_id is not None:
            tool_execution_service.finish_execution(
                session,
                tool_call_id=tool_call_id,
                exit_code=result.exit_code,
                final_output=ToolExecutionResult.success("shell.executed", "命令已执行", data).public_payload(),
                status="completed" if result.exit_code == 0 else "failed",
            )
        return ToolExecutionResult(
            kind="shell.executed",
            summary="命令执行成功" if result.exit_code == 0 else f"命令退出码为 {result.exit_code}",
            status="success" if result.exit_code == 0 else "partial",
            data=data,
            runtime_events=tuple(runtime_events),
        )

    def list_tool_schemas(
        self,
        session: AgentSession,
        *,
        _host_snapshot: HostToolCatalogSnapshot | None = None,
    ) -> list[dict[str, Any]]:
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
        host_descriptors = (
            _host_snapshot.descriptors
            if _host_snapshot is not None
            else tool_catalog_service.snapshot(session).descriptors
        )
        for descriptor in host_descriptors:
            tools.append(self._make_schema(
                descriptor["name"],
                f"{descriptor['description']}（宿主工具）",
                descriptor.get("input_schema") or {"type": "object", "properties": {}},
            ))

        allowed_tools = set(session.allowed_tools) if session.allowed_tools is not None else None

        def permitted(schema: dict[str, Any]) -> bool:
            name = str(schema.get("function", {}).get("name") or "")
            if not session.subagent_tools_enabled and name in SUBAGENT_TOOL_NAMES:
                return False
            return allowed_tools is None or name in allowed_tools

        return [schema for schema in tools if permitted(schema)]


    def create_catalog_snapshot(self, session: AgentSession) -> ToolCatalogSnapshot:
        """Capture one coherent tool catalog for a complete model round."""
        host_snapshot = tool_catalog_service.snapshot(session)
        try:
            signature = inspect.signature(self.list_tool_schemas)
        except (TypeError, ValueError):
            signature = None
        if signature is not None and "_host_snapshot" in signature.parameters:
            schemas = self.list_tool_schemas(session, _host_snapshot=host_snapshot)
        else:
            # Keeps test and extension monkeypatches that implement the legacy signature compatible.
            schemas = self.list_tool_schemas(session)
        effective_mcp_ids: set[str] = set()
        try:
            from app.services.capability_service import capability_service
            effective_mcp_ids = {str(item["id"]) for item in capability_service.list_effective_mcp(session)}
        except Exception:
            pass
        descriptors_by_name = {
            name: copy.deepcopy(item)
            for name, item in host_snapshot.descriptors_by_name.items()
            if item.get("kind") != "mcp" or str(item.get("mcp_config_id")) in effective_mcp_ids
        }
        allowed_names = set(descriptors_by_name)
        schemas = tuple(
            schema for schema in schemas
            if schema.get("function", {}).get("name") not in host_snapshot.descriptors_by_name
            or schema.get("function", {}).get("name") in allowed_names
        )
        return ToolCatalogSnapshot(
            revision=host_snapshot.revision,
            fingerprint=host_snapshot.fingerprint,
            schemas=tuple(copy.deepcopy(schemas)),
            host_descriptors_by_name=MappingProxyType(
                descriptors_by_name
            ),
        )

    async def execute(
        self,
        session: AgentSession,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        run_id: str | None = None,
        tool_call_id: str | None = None,
        output_event_callback: Callable[[ToolOutputEvent], Awaitable[None]] | None = None,
        catalog_snapshot: ToolCatalogSnapshot | None = None,
    ) -> ToolExecutionResult:
        """执行指定工具。"""
        # 检查注册表中的内置工具
        handler = self._registry.get_handler(tool_name)
        if handler:
            if tool_name in SUBAGENT_TOOL_NAMES:
                return await self._handle_subagent_tool(
                    session,
                    arguments,
                    tool_name=tool_name,
                    run_id=run_id,
                )
            if tool_name == "sandbox_shell":
                return await self._handle_sandbox_shell(
                    session,
                    arguments,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    output_event_callback=output_event_callback,
                )
            if tool_name == "rebuild_runtime":
                return await self._handle_rebuild_runtime(session, arguments)
            return await handler(session, arguments)

        # 处理动态工具
        runtime_config = self._resolve_runtime_config(session)

        if tool_name == "web_search":
            payload = await network_service.web_search(
                session=session,
                runtime_config=runtime_config,
                query=str(arguments["query"]),
                allowed_domains=[str(item) for item in arguments.get("allowed_domains", [])],
                blocked_domains=[str(item) for item in arguments.get("blocked_domains", [])],
                max_results=self._parse_int(arguments.get("max_results")),
            )
            results = payload.get("results", [])
            return ToolExecutionResult.success(
                "web.searched",
                f"搜索完成，返回 {len(results)} 条结果",
                {
                    "query": payload.get("query", ""),
                    "provider": payload.get("provider", ""),
                    "result_count": len(results),
                    "answer": payload.get("summary", ""),
                    "results": results,
                },
            )

        if tool_name == "web_fetch":
            payload = await network_service.web_fetch(
                runtime_config=runtime_config,
                url=str(arguments["url"]),
                format_type=str(arguments.get("format") or "markdown"),
                timeout_seconds=self._parse_int(arguments.get("timeout_seconds")),
            )
            return ToolExecutionResult.success(
                "web.fetched",
                f"已抓取 {payload.get('url', '')}",
                payload,
            )

        # 处理宿主工具
        descriptor = (
            catalog_snapshot.host_descriptors_by_name.get(tool_name)
            if catalog_snapshot is not None
            else tool_catalog_service.snapshot(session).descriptors_by_name.get(tool_name)
        )
        if descriptor:
            if descriptor.get("kind") == "mcp":
                from app.services.mcp_runtime_service import mcp_runtime_service
                config = mcp_runtime_service.resolve_config(session, str(descriptor["mcp_config_id"]))
                payload = await mcp_runtime_service.invoke(session, config, str(descriptor["mcp_tool"]), arguments)
                return ToolExecutionResult.success(
                    "mcp_tool.completed",
                    f"MCP 工具 {tool_name} 执行完成",
                    payload,
                )
            return await self._invoke_host_tool(session, descriptor, arguments)

        raise RuntimeError(f"未知工具: {tool_name}")

    async def _handle_subagent_tool(
        self,
        session: AgentSession,
        arguments: dict[str, Any],
        *,
        tool_name: str,
        run_id: str | None = None,
    ) -> ToolExecutionResult:
        from app.services.subagent_service import subagent_service

        payload = await subagent_service.execute_tool(
            session,
            arguments,
            parent_run_id=run_id,
            tool_name=tool_name,
        )
        return self._subagent_result(tool_name, payload)

    def _subagent_result(self, tool_name: str, payload: dict[str, Any]) -> ToolExecutionResult:
        if tool_name == "subagent_create":
            return ToolExecutionResult.success(
                "subagent.created",
                f"子代理 {payload.get('name', '')} 已创建",
                payload,
            )
        if tool_name == "subagent_send_message":
            return ToolExecutionResult.success(
                "subagent.message_sent",
                f"已向子代理 {payload.get('name', '')} 发送跟进消息",
                payload,
            )
        if tool_name == "subagent_wait":
            status = str(payload.get("status") or "unknown")
            return ToolExecutionResult.success(
                "subagent.waited",
                "子代理已全部完成" if status == "completed" else "子代理仍在运行",
                payload,
            )
        if tool_name == "subagent_list":
            rows = payload.get("subagents", [])
            return ToolExecutionResult.success(
                "subagent.listed",
                f"当前会话共有 {len(rows)} 个子代理",
                payload,
            )
        if tool_name == "subagent_cancel":
            return ToolExecutionResult.success(
                "subagent.cancelled",
                "子代理停止流程已结束",
                payload,
            )
        if tool_name == "subagent_get_result":
            return ToolExecutionResult.success(
                "subagent.result",
                f"子代理状态：{payload.get('status', 'unknown')}",
                payload,
            )
        raise RuntimeError(f"未知 subagent 工具: {tool_name}")

    async def _invoke_host_tool(
        self,
        session: AgentSession,
        descriptor: dict[str, Any],
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """调用宿主工具，自动注入认证并处理 token 刷新。"""
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

        async with httpx.AsyncClient(timeout=120, verify=settings.http_client_ssl_verify) as client:
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

            if response.is_error:
                detail = self._host_error_detail(response)
                raise RuntimeError(
                    f"宿主工具 {descriptor['name']} 调用失败（HTTP {response.status_code}）：{detail}"
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    f"宿主工具 {descriptor['name']} 返回了无效 JSON: {response.text[:2000]}"
                ) from exc

        payload = data if isinstance(data, dict) else {"result": data}
        return ToolExecutionResult.success(
            "host_tool.completed",
            f"宿主工具 {descriptor['name']} 执行完成",
            payload,
        )

    @staticmethod
    def _host_error_detail(response: httpx.Response) -> str:
        """Preserve a bounded host explanation so the model can recover intelligently."""
        text = response.text
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            payload = None
        candidates: list[Any] = []
        if isinstance(payload, dict):
            candidates.extend((payload.get("detail"), payload.get("message"), payload.get("summary")))
            error = payload.get("error")
            if isinstance(error, dict):
                candidates.extend((error.get("message"), error.get("detail")))
            else:
                candidates.append(error)
        candidates.append(text)
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()[:2000]
        return "宿主没有提供错误说明"

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
            async with httpx.AsyncClient(timeout=30, verify=settings.http_client_ssl_verify) as client:
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
