# backend/app/services/tool_service.py
"""统一管理内置工具与宿主工具代理。"""
from __future__ import annotations

import asyncio
import ast
import copy
import inspect
import json
import re
import hashlib
from dataclasses import asdict
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
from app.schemas.schedule import ScheduleCreateRequest, ScheduleTargetRequest, ScheduleUpdateRequest
from app.services.scheduling.errors import ScheduleError, SchedulePermissionError
from app.services.scheduling.service import schedule_service
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

TIMEOUT_SECONDS_PROPERTY = "timeout_seconds"


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

    def register_handler(self, name: str, handler: ToolHandler) -> None:
        """只注册执行器，不暴露 schema；用于条件启用的运行时工具。"""
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
        from app.services.skill_tools import register_skill_tools
        register_skill_tools(self._registry)

    def _register_builtin_tools(self) -> None:
        """注册所有内置工具。"""
        self._registry.register_handler("context_compact", self._handle_context_compact)
        self._registry.register("host_context_read", "用 JSON Pointer（如 /extras/items/0/ip.addr）或旧点路径读取当前宿主资料；空路径分段读取根资料，不读取操作凭据。资料不是新增授权。", {
            "type": "object", "properties": {"path": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 12000}}, "additionalProperties": False,
        }, self._handle_host_context_read)
        for name in ("conversation_read", "conversation_search"):
            self._registry.register(name, "读取当前会话归档正文、调用和结果；用 message_id 恢复引用，用 version_digest 读特定版本。搜索返回有界片段，旧版本/旧分支须核对，不读取思考签名，不是新增授权。", {
                "type": "object", "properties": {
                    "message_id": {"type": "string"}, "query": {"type": "string"},
                    "version_digest": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 12000},
                }, "additionalProperties": False,
            }, self._handle_conversation_read)
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
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "description": "提问卡片的整体标题，必填非空。",
                    },
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
                                "header": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 20,
                                    "description": "问题的短标题，建议不超过 10 个字。",
                                },
                                "question": {
                                    "type": "string",
                                    "minLength": 1,
                                    "description": "要向用户提出的完整问题文本，必填非空。",
                                },
                                "multi_select": {"type": "boolean"},
                                "allow_other": {"type": "boolean"},
                                "allow_notes": {"type": "boolean"},
                                "options": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {
                                                "type": "string",
                                                "minLength": 1,
                                                "description": "选项的展示文案。",
                                            },
                                            "description": {
                                                "type": "string",
                                                "description": "选项的补充说明。",
                                            },
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

        self._registry.register(
            "schedule_create",
            "创建定时任务。目标默认是当前会话；new_session_per_run 每次使用独立会话和工作区。",
            {
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 128},
                    "prompt": {"type": "string", "minLength": 1},
                    "target_mode": {
                        "type": "string",
                        "enum": ["existing_session", "new_session_per_run"],
                    },
                    "session_id": {"type": "string"},
                    "new_session_title_prefix": {"type": "string", "maxLength": 80},
                    "schedule": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["interval", "daily", "workday", "weekly", "cron"],
                            },
                            "every_seconds": {"type": "integer", "minimum": 60},
                            "time": {"type": "string"},
                            "weekdays": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 7}},
                            "expression": {"type": "string"},
                        },
                        "required": ["type"],
                        "additionalProperties": False,
                    },
                    "timezone": {"type": "string"},
                    "starts_at": {"type": "string", "format": "date-time"},
                    "ends_at": {"type": "string", "format": "date-time"},
                    "timeout_seconds": {"type": "integer", "minimum": 30, "maximum": 7200},
                    "max_runs": {"type": "integer", "minimum": 1},
                },
                "required": ["title", "prompt", "schedule"],
                "additionalProperties": False,
            },
            self._handle_schedule_create,
        )
        self._registry.register(
            "schedule_list",
            "列出当前用户或平台身份可见的定时任务。",
            {
                "properties": {
                    "status": {"type": "string"},
                    "search": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
            self._handle_schedule_list,
        )
        self._registry.register(
            "schedule_update",
            "更新当前身份有权限管理的定时任务，必须携带 expected_revision 防止覆盖并发修改。",
            {
                "properties": {
                    "task_id": {"type": "string"},
                    "expected_revision": {"type": "integer", "minimum": 1},
                    "title": {"type": "string"},
                    "prompt": {"type": "string"},
                    "schedule": {"type": "object"},
                    "timezone": {"type": "string"},
                    "starts_at": {"type": "string", "format": "date-time"},
                    "ends_at": {"type": "string", "format": "date-time"},
                },
                "required": ["task_id", "expected_revision"],
                "additionalProperties": False,
            },
            self._handle_schedule_update,
        )
        self._registry.register(
            "schedule_pause",
            "暂停一个定时任务。",
            {
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
            self._handle_schedule_pause,
        )
        self._registry.register(
            "schedule_resume",
            "恢复一个已暂停的定时任务。",
            {
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
            self._handle_schedule_resume,
        )
        self._registry.register(
            "schedule_restore",
            "恢复一个已归档、已完成或已过期的定时任务；达到次数上限或结束时间已过时需先调整配置。",
            {
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
            self._handle_schedule_restore,
        )
        self._registry.register(
            "schedule_delete",
            "归档一个定时任务并停止后续触发，保留执行历史。",
            {
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
            self._handle_schedule_delete,
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

    async def _handle_schedule_create(
        self,
        session: AgentSession,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return await self._handle_schedule_tool(
            session, {**arguments, "action": "schedule_create"}
        )

    async def _handle_schedule_list(
        self,
        session: AgentSession,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return await self._handle_schedule_tool(
            session, {**arguments, "action": "schedule_list"}
        )

    async def _handle_schedule_update(
        self,
        session: AgentSession,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return await self._handle_schedule_tool(
            session, {**arguments, "action": "schedule_update"}
        )

    async def _handle_schedule_pause(
        self,
        session: AgentSession,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return await self._handle_schedule_tool(
            session, {**arguments, "action": "schedule_pause"}
        )

    async def _handle_schedule_resume(
        self,
        session: AgentSession,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return await self._handle_schedule_tool(
            session, {**arguments, "action": "schedule_resume"}
        )

    async def _handle_schedule_restore(
        self,
        session: AgentSession,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return await self._handle_schedule_tool(
            session, {**arguments, "action": "schedule_restore"}
        )

    async def _handle_schedule_delete(
        self,
        session: AgentSession,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return await self._handle_schedule_tool(
            session, {**arguments, "action": "schedule_delete"}
        )

    async def _handle_schedule_tool(
        self,
        session: AgentSession,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Agent 定时任务工具入口；身份来自当前会话，不接受调用方伪造归属。"""
        identity = schedule_service.identity_from_session(session)
        action = arguments.get("action")
        try:
            if action == "schedule_create":
                target_mode = str(arguments.get("target_mode") or "existing_session")
                target_session_id = arguments.get("session_id") or (
                    session.session_id if target_mode == "existing_session" else None
                )
                request = ScheduleCreateRequest(
                    title=str(arguments["title"]),
                    prompt=str(arguments["prompt"]),
                    target=ScheduleTargetRequest(
                        mode=target_mode,
                        session_id=target_session_id,
                        new_session_title_prefix=arguments.get("new_session_title_prefix"),
                    ),
                    schedule=arguments["schedule"],
                    timezone=str(arguments.get("timezone") or "UTC"),
                    starts_at=arguments.get("starts_at"),
                    ends_at=arguments.get("ends_at"),
                    execution={
                        "timeout_seconds": int(arguments.get("timeout_seconds", 900)),
                        "max_runs": arguments.get("max_runs"),
                    },
                )
                task = schedule_service.create(
                    request,
                    identity=identity,
                    created_via="agent",
                    created_session_id=session.session_id,
                )
                return ToolExecutionResult.success(
                    "schedule.created",
                    "定时任务已创建，等待用户确认" if task.status.value == "pending_approval" else "定时任务已创建",
                    task.model_dump(mode="json"),
                )

            if action == "schedule_list":
                items, total = schedule_service.list(
                    identity=identity,
                    status=arguments.get("status"),
                    search=arguments.get("search"),
                    limit=int(arguments.get("limit", 20)),
                )
                return ToolExecutionResult.success(
                    "schedule.listed",
                    f"找到 {total} 个定时任务",
                    {"total": total, "items": [item.model_dump(mode="json") for item in items]},
                )

            task_id = str(arguments["task_id"])
            if action == "schedule_update":
                task = schedule_service.update(
                    task_id,
                    ScheduleUpdateRequest.model_validate(arguments),
                    identity=identity,
                )
            elif action == "schedule_pause":
                task = schedule_service.pause(task_id, identity=identity)
            elif action == "schedule_resume":
                task = schedule_service.resume(task_id, identity=identity)
            elif action == "schedule_restore":
                task = schedule_service.restore(task_id, identity=identity)
            elif action == "schedule_delete":
                task = schedule_service.archive(task_id, identity=identity)
            else:
                return ToolExecutionResult.failure(
                    "schedule.error",
                    "未知定时任务操作",
                    code="SCHEDULE_ACTION_NOT_FOUND",
                    message=f"不支持的定时任务操作: {action}",
                )
            return ToolExecutionResult.success(
                "schedule.updated",
                "定时任务已更新",
                task.model_dump(mode="json"),
            )
        except SchedulePermissionError as exc:
            return ToolExecutionResult.failure(
                "schedule.error",
                "无权操作目标定时任务",
                code="SCHEDULE_PERMISSION_DENIED",
                message=str(exc),
            )
        except ScheduleError as exc:
            return ToolExecutionResult.failure(
                "schedule.error",
                "定时任务配置无效",
                code="SCHEDULE_INVALID",
                message=str(exc),
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
        validation_error = self._validate_request_user_input(arguments)
        if validation_error is not None:
            example = {
                "title": "请确认执行方式",
                "questions": [
                    {
                        "header": "执行方式",
                        "question": "请选择本次执行方式？",
                        "options": [{"label": "继续"}, {"label": "停止"}],
                    }
                ],
            }
            return ToolExecutionResult.failure(
                "elicitation.request_failed",
                "用户提问参数无效",
                code="INVALID_TOOL_ARGUMENTS",
                message=validation_error,
                retryable=True,
                next_action=(
                    f"{validation_error}。请按 schema 重新调用 request_user_input，"
                    f"合法最小示例：{json.dumps(example, ensure_ascii=False, separators=(',', ':'))}"
                ),
            )

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

    def _validate_request_user_input(self, arguments: dict[str, Any]) -> str | None:
        allowed_fields = {
            "id",
            "kind",
            "title",
            "blocking",
            "source_agent",
            "related_work_items",
            "preview_text",
            "questions",
        }
        unknown_fields = sorted(set(arguments) - allowed_fields)
        if unknown_fields:
            return f"不支持的顶层字段：{', '.join(unknown_fields)}"

        title = arguments.get("title")
        if not isinstance(title, str) or not title.strip():
            return "title 必须为非空字符串"

        questions = arguments.get("questions")
        if not isinstance(questions, list) or not questions:
            return "questions 必须为非空数组"

        allowed_question_fields = {
            "id",
            "header",
            "question",
            "multi_select",
            "allow_other",
            "allow_notes",
            "options",
        }
        for index, question in enumerate(questions, start=1):
            prefix = f"questions[{index - 1}]"
            if not isinstance(question, dict):
                return f"{prefix} 必须为对象"

            unknown_fields = sorted(set(question) - allowed_question_fields)
            if unknown_fields:
                return f"{prefix} 包含不支持的字段：{', '.join(unknown_fields)}"

            header = question.get("header")
            if not isinstance(header, str) or not header.strip():
                return f"{prefix}.header 必须为非空字符串"

            question_text = question.get("question")
            if not isinstance(question_text, str) or not question_text.strip():
                return f"{prefix}.question 必须为非空字符串"

            if "id" in question and not isinstance(question["id"], str):
                return f"{prefix}.id 必须为字符串"

            for field in ("multi_select", "allow_other", "allow_notes"):
                if field in question and not isinstance(question[field], bool):
                    return f"{prefix}.{field} 必须为布尔值"

            if "options" not in question:
                return f"{prefix}.options 必须为对象数组，而不是字符串"

            options = question.get("options")
            if not isinstance(options, list):
                return f"{prefix}.options 必须为对象数组，而不是字符串"

            for option_index, option in enumerate(options, start=1):
                option_prefix = f"{prefix}.options[{option_index - 1}]"
                if not isinstance(option, dict):
                    return f"{option_prefix} 必须为对象"
                label = option.get("label")
                if not isinstance(label, str) or not label.strip():
                    return f"{option_prefix}.label 必须为非空字符串"
                if "description" in option and not isinstance(option["description"], str):
                    return f"{option_prefix}.description 必须为字符串"
                if set(option) - {"label", "description"}:
                    return f"{option_prefix} 只能包含 label 和 description 字段"

        for field, expected_type in (
            ("id", str),
            ("source_agent", str),
            ("preview_text", str),
        ):
            if field in arguments and not isinstance(arguments[field], expected_type):
                return f"{field} 必须为字符串"

        if "kind" in arguments and arguments["kind"] not in {
            "clarification",
            "confirmation",
            "decision",
            "missing_info",
            "approval",
        }:
            return "kind 必须为 clarification、confirmation、decision、missing_info 或 approval"

        if "blocking" in arguments and not isinstance(arguments["blocking"], bool):
            return "blocking 必须为布尔值"

        related_work_items = arguments.get("related_work_items", [])
        if not isinstance(related_work_items, list) or any(
            not isinstance(item, str) for item in related_work_items
        ):
            return "related_work_items 必须为字符串数组"

        return None

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
        tools = [self._with_timeout_parameter(schema) for schema in self._registry.get_schemas()]
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

        return [self._with_timeout_parameter(schema) for schema in tools if permitted(schema)]

    def get_context_compact_schema(self) -> dict[str, Any]:
        """返回 Agent 主动压缩工具 schema；仅在上下文警戒区附加到请求。"""
        return self._with_timeout_parameter(self._make_schema(
            "context_compact",
            "压缩当前会话上下文。上下文接近上限时可选；若当前任务能在剩余预算内完成，也可以继续执行而不压缩。",
            {
                "type": "object",
                "properties": {
                    "strategy": {
                        "type": "string",
                        "enum": ["balanced", "aggressive"],
                        "description": "balanced 保留更多近期上下文；aggressive 更快释放预算",
                    },
                    "reason": {
                        "type": "string",
                        "maxLength": 500,
                        "description": "简短说明压缩原因，便于审计",
                    },
                },
                "additionalProperties": False,
            },
        ))

    async def _handle_conversation_read(self, session: AgentSession, arguments: dict[str, Any]) -> ToolExecutionResult:
        from app.services.context.history_store import history_store
        result = history_store.read(session.session_id, message_id=str(arguments.get("message_id") or ""),
                                    query=str(arguments.get("query") or ""),
                                    version_digest=str(arguments.get("version_digest") or ""),
                                    offset=max(0, int(arguments.get("offset", 0))),
                                    limit=max(1, min(12000, int(arguments.get("limit", 6000)))))
        return ToolExecutionResult.success("history.read", "会话原文检索完成", result)

    async def _handle_host_context_read(self, session: AgentSession, arguments: dict[str, Any]) -> ToolExecutionResult:
        from app.services.context.host_context import public_host_context, read_host_path
        value = read_host_path(public_host_context(session.host_context or {}), str(arguments.get("path") or ""))
        text = json.dumps(value, ensure_ascii=False)
        offset = max(0, int(arguments.get("offset", 0)))
        limit = max(1, min(12000, int(arguments.get("limit", 6000))))
        return ToolExecutionResult.success("host.context", "宿主资料读取完成", {"content": text[offset:offset + limit], "offset": offset, "total_chars": len(text), "has_more": offset + limit < len(text)})

    async def _handle_context_compact(
        self,
        session: AgentSession,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """执行 Agent 主动压缩：归档原文后生成有界摘要或原生检查点。"""
        from app.services.context.context_pipeline import context_pipeline
        from app.services.context.runtime_types import ContextOverflowError
        from app.services.prompt_service import prompt_service
        from app.services.llm_client import llm_client

        strategy = str(arguments.get("strategy") or "balanced")
        if strategy not in {"balanced", "aggressive"}:
            return ToolExecutionResult.failure(
                "context.compact_failed",
                "无效的上下文压缩策略",
                code="CONTEXT_STRATEGY_INVALID",
                message=f"不支持的压缩策略: {strategy}",
            )

        active = session.get_run(session.current_run_id()) if session.current_run_id() else None
        if active is not None and active.tool_name == "context_compact":
            # 当前 assistant 的所有 tool_result 提交后由主循环压缩，不能切断原生依赖组。
            return ToolExecutionResult.success("context.compact_requested", "已请求在当前工具组结束后压缩上下文",
                {"requested": True, "strategy": strategy}, control={"type": "compact_context", "strategy": strategy})

        conversation = store_service.get_conversation_by_session(session.session_id)
        raw_messages = prompt_service.build_system_messages(
            session,
            conversation=conversation,
            runtime_sections=[] if self._resolve_runtime_config(session).api_format != "openai-compatible" else runtime_state_service.build_runtime_context_sections(session),
        )
        raw_messages = [*raw_messages, *session.messages]
        try:
            from app.services.skill_tools import visible_schemas
            request_tools = [*visible_schemas(session, self.list_tool_schemas(session)), self.get_context_compact_schema()]
            prepared = await context_pipeline.compact_session_async(
                session=session,
                llm_runtime=self._resolve_runtime_config(session),
                messages=raw_messages,
                turn_index=max([int(message.get("turn_index", 0)) for message in session.messages] or [0]),
                tools=request_tools,
                max_output_tokens=llm_client.output_limit(self._resolve_runtime_config(session)),
                cause="agent",
                aggressive=strategy == "aggressive",
            )
        except (ContextOverflowError, RuntimeError, ValueError, httpx.HTTPError) as exc:
            return ToolExecutionResult.failure(
                "context.compact_failed",
                "上下文压缩后仍超出限制",
                code="CONTEXT_COMPACT_FAILED",
                message=str(exc),
                data={"estimated_tokens": getattr(exc, "token_estimate", 0)},
            )

        plan = prepared.plan
        assert plan is not None
        return ToolExecutionResult.success(
            "context.compacted",
            "上下文已压缩",
            {
                "compacted": True,
                "cause": "agent",
                "strategy": plan.strategy.value,
                "tokens_before": plan.estimated_tokens_before,
                "tokens_after": plan.estimated_tokens_after,
                "tokens_saved": plan.estimated_tokens_saved,
                "summary_message_id": next(
                    (
                        str(message.get("message_id"))
                        for message in session.messages
                        if message.get("is_compact_summary")
                    ),
                    None,
                ),
            },
        )


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

    async def execute(self, session: AgentSession, tool_name: str, arguments: dict[str, Any], *,
                      run_id: str | None = None, tool_call_id: str | None = None,
                      output_event_callback=None, catalog_snapshot=None) -> ToolExecutionResult:
        from app.services.context.history_store import history_store
        from app.services.tool_result_service import ToolExecutionError
        if session.allowed_tools is not None and tool_name not in session.allowed_tools:
            return ToolExecutionResult.failure("tool.forbidden", "工具不在当前授权范围", code="TOOL_NOT_ALLOWED", message=tool_name)
        if catalog_snapshot is not None:
            schema = next((item["function"].get("parameters", {}) for item in catalog_snapshot.schemas if item["function"]["name"] == tool_name), None)
            if schema is None and tool_name == "context_compact":
                schema = self.get_context_compact_schema()["function"]["parameters"]
            if schema is None and tool_name in catalog_snapshot.host_descriptors_by_name:
                descriptor = catalog_snapshot.host_descriptors_by_name[tool_name]
                schema = self._with_timeout_parameter({"type": "function", "function": {
                    "name": tool_name, "parameters": descriptor.get("input_schema") or {"type": "object"},
                }})["function"]["parameters"]
            if schema is None:
                return ToolExecutionResult.failure("tool.forbidden", "工具未在当前目录中暴露", code="TOOL_NOT_EXPOSED", message=tool_name)
            from jsonschema import Draft202012Validator
            error = next(Draft202012Validator(schema).iter_errors(arguments), None)
            if error:
                return ToolExecutionResult.failure("tool.invalid_arguments", "工具参数与 schema 不匹配", code="TOOL_ARGUMENTS_INVALID", message=error.message)
        if run_id and tool_call_id:
            digest = hashlib.sha256(json.dumps([tool_name, arguments], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            state, saved = history_store.claim_tool(session.session_id, run_id, tool_call_id, digest)
            if saved:
                if saved.get("error"):
                    saved["error"] = ToolExecutionError(**saved["error"])
                return ToolExecutionResult(**saved)
            if state != "claimed":
                return ToolExecutionResult.failure("tool.unknown", "工具执行状态待核对，未自动重复执行", code="TOOL_EFFECT_UNKNOWN",
                    message="该操作已开始但没有完整回执，请先核对实际状态，避免重复副作用")
        result = await self._execute_with_timeout(session, tool_name, arguments, run_id=run_id,
            tool_call_id=tool_call_id, output_event_callback=output_event_callback, catalog_snapshot=catalog_snapshot)
        if run_id and tool_call_id:
            history_store.finish_tool(session.session_id, run_id, tool_call_id, asdict(result))
        return result

    async def _execute_with_timeout(
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
        timeout_seconds, timeout_error = self._extract_timeout_seconds(arguments)
        if timeout_error is not None:
            return ToolExecutionResult.failure(
                "tool.execution",
                "工具超时参数无效",
                code="TOOL_TIMEOUT_INVALID",
                message=timeout_error,
                retryable=True,
                next_action="请传入 1 到 "
                f"{settings.tool_timeout_max_seconds} 之间的整数 timeout_seconds 后重试。",
            )

        try:
            if timeout_seconds is None:
                return await self._execute_tool(
                    session,
                    tool_name,
                    arguments,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    output_event_callback=output_event_callback,
                    catalog_snapshot=catalog_snapshot,
                    timeout_seconds=timeout_seconds,
                )
            async with asyncio.timeout(timeout_seconds):
                return await self._execute_tool(
                    session,
                    tool_name,
                    arguments,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    output_event_callback=output_event_callback,
                    catalog_snapshot=catalog_snapshot,
                    timeout_seconds=timeout_seconds,
                )
        except TimeoutError:
            return self._timeout_failure(tool_name, timeout_seconds)
        except httpx.TimeoutException:
            if timeout_seconds is None:
                raise
            return self._timeout_failure(tool_name, timeout_seconds)

    async def _execute_tool(
        self,
        session: AgentSession,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        run_id: str | None,
        tool_call_id: str | None,
        output_event_callback: Callable[[ToolOutputEvent], Awaitable[None]] | None,
        catalog_snapshot: ToolCatalogSnapshot | None,
        timeout_seconds: int | None,
    ) -> ToolExecutionResult:
        """在统一超时预算内分发工具调用。"""
        from app.services.skill_tools import DEFINITIONS
        if tool_name in DEFINITIONS:
            visible = {schema["function"]["name"] for schema in catalog_snapshot.schemas} if catalog_snapshot else set(session.loaded_skill_tools)
            if tool_name not in visible or (session.allowed_tools is not None and tool_name not in session.allowed_tools):
                return ToolExecutionResult.failure("tool.execution", "请先发现该工具", code="TOOL_NOT_LOADED", message="先调用 discover_tools，下一轮再使用该工具")
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
            business_arguments = {
                key: value
                for key, value in arguments.items()
                if key != TIMEOUT_SECONDS_PROPERTY
            }
            if descriptor.get("kind") == "mcp":
                from app.services.mcp_runtime_service import mcp_runtime_service
                config = mcp_runtime_service.resolve_config(session, str(descriptor["mcp_config_id"]))
                payload = await mcp_runtime_service.invoke(
                    session,
                    config,
                    str(descriptor["mcp_tool"]),
                    business_arguments,
                )
                return ToolExecutionResult.success(
                    "mcp_tool.completed",
                    f"MCP 工具 {tool_name} 执行完成",
                    payload,
                )
            return await self._invoke_host_tool(
                session,
                descriptor,
                business_arguments,
                timeout_seconds=timeout_seconds,
            )

        raise RuntimeError(f"未知工具: {tool_name}")

    def _timeout_failure(self, tool_name: str, timeout_seconds: int) -> ToolExecutionResult:
        """构造可恢复的工具超时结果。"""
        return ToolExecutionResult.failure(
            "tool.execution",
            "工具执行超时",
            code="TOOL_EXECUTION_TIMEOUT",
            message=f"工具 {tool_name} 执行超过 {timeout_seconds} 秒",
            retryable=True,
            next_action="超时不代表业务操作失败；请先查询业务状态，确认幂等后再决定是否重试。",
            data={"timeout_seconds": timeout_seconds},
        )

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
        *,
        timeout_seconds: int | None = None,
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

        async with httpx.AsyncClient(
            timeout=timeout_seconds
            if timeout_seconds is not None
            else settings.host_tool_timeout_seconds,
            verify=settings.http_client_ssl_verify,
        ) as client:
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

    def _with_timeout_parameter(self, schema: dict[str, Any]) -> dict[str, Any]:
        """为所有模型可见工具补充统一的引擎级超时参数。"""
        normalized = copy.deepcopy(schema)
        function = normalized.setdefault("function", {})
        if not isinstance(function, dict):
            return normalized
        parameters = function.setdefault("parameters", {})
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
            function["parameters"] = parameters
        properties = parameters.setdefault("properties", {})
        if not isinstance(properties, dict):
            return normalized
        if TIMEOUT_SECONDS_PROPERTY not in properties:
            properties[TIMEOUT_SECONDS_PROPERTY] = {
                "type": "integer",
                "minimum": 1,
                "maximum": settings.tool_timeout_max_seconds,
                "description": "本次工具调用的引擎级超时时间；超时后工具调用失败，但不会自动停止整个 Agent run。",
            }
        return normalized

    def _extract_timeout_seconds(self, arguments: dict[str, Any]) -> tuple[int | None, str | None]:
        """解析并校验统一的工具超时预算。"""
        raw_value = arguments.get(TIMEOUT_SECONDS_PROPERTY)
        if raw_value is None:
            return None, None
        if isinstance(raw_value, bool):
            return None, "timeout_seconds 必须是整数。"
        if isinstance(raw_value, float) and not raw_value.is_integer():
            return None, "timeout_seconds 必须是整数。"
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return None, "timeout_seconds 必须是整数。"
        if value < 1:
            return None, "timeout_seconds 必须大于 0。"
        if value > settings.tool_timeout_max_seconds:
            return None, (
                f"timeout_seconds 不能超过 {settings.tool_timeout_max_seconds} 秒。"
            )
        return value, None

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
