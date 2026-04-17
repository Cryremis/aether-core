# backend/app/services/tool_service.py
from __future__ import annotations

import ast
import json
import re
from typing import Any

import httpx

from app.sandbox.runner import sandbox_runner
from app.services.artifact_service import artifact_service
from app.services.file_service import file_service
from app.services.session_service import AgentSession
from app.services.skill_service import skill_service


class ToolService:
    """统一管理内置工具与宿主工具代理。"""

    def list_tool_schemas(self, session: AgentSession) -> list[dict[str, Any]]:
        tools = [
            self._schema(
                "invoke_skill",
                "加载一个真实技能包，把该技能的 SKILL.md 指令注入当前对话上下文。若任务明显匹配某个技能，必须先调用它。",
                {
                    "type": "object",
                    "properties": {
                        "skill_name": {"type": "string"},
                    },
                    "required": ["skill_name"],
                    "additionalProperties": False,
                },
            ),
            self._schema(
                "list_skills",
                "列出当前会话可见技能。",
                {"type": "object", "properties": {}, "additionalProperties": False},
            ),
            self._schema(
                "list_files",
                "列出当前会话可见文件与产物。",
                {"type": "object", "properties": {}, "additionalProperties": False},
            ),
            self._schema(
                "read_workspace_file",
                "读取沙箱中的文本文件。优先使用 file_id，或使用相对路径。",
                {
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string"},
                        "relative_path": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            ),
            self._schema(
                "create_text_artifact",
                "在输出目录创建文本产物，便于用户下载。",
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["name", "content"],
                    "additionalProperties": False,
                },
            ),
            self._schema(
                "sandbox_shell",
                "在受限容器沙箱内执行命令，支持 bash 与 powershell。",
                {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "shell": {"type": "string", "enum": ["powershell", "bash"]},
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
            ),
        ]
        for descriptor in session.host_tools:
            tools.append(
                self._schema(
                    descriptor["name"],
                    f"{descriptor['description']}（宿主工具）",
                    descriptor.get("input_schema") or {"type": "object", "properties": {}},
                )
            )
        return tools

    async def execute(self, session: AgentSession, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "invoke_skill":
            return skill_service.invoke_skill(session, skill_name=str(arguments["skill_name"]))
        if tool_name == "list_skills":
            return {"items": [item.model_dump(mode="json") for item in skill_service.list_for_session(session)]}
        if tool_name == "list_files":
            return {
                "items": [
                    item.model_dump(mode="json")
                    for item in file_service.list_visible_files(session) + artifact_service.list_artifacts(session)
                ]
            }
        if tool_name == "read_workspace_file":
            return {
                "content": file_service.read_text(
                    session,
                    file_id=arguments.get("file_id"),
                    relative_path=arguments.get("relative_path"),
                )
            }
        if tool_name == "create_text_artifact":
            artifact = artifact_service.create_text_artifact(
                session=session,
                name=str(arguments["name"]),
                content=str(arguments["content"]),
            )
            return {"artifact": artifact.model_dump(mode="json")}
        if tool_name == "sandbox_shell":
            if session.workspace is None:
                raise RuntimeError("会话沙箱尚未初始化。")
            result = await sandbox_runner.run_shell(
                workspace=session.workspace,
                command=str(arguments["command"]),
                shell=arguments.get("shell"),
            )
            artifact_service.sync_output_directory(session)
            return {
                "command": result.command,
                "shell": result.shell,
                "executor": result.executor,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_ms": result.duration_ms,
                "log_path": result.log_path,
            }
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
        base_url = (
            session.host_context.get("extras", {}).get("host_callback_base_url")
            if session.host_context
            else None
        )
        endpoint = descriptor["endpoint"]
        if endpoint.startswith("/"):
            if not base_url:
                raise RuntimeError("宿主工具使用相对 endpoint，但未提供 host_callback_base_url。")
            endpoint = f"{str(base_url).rstrip('/')}{endpoint}"

        request_payload = {
            "session_id": session.session_id,
            "arguments": arguments,
            "context": session.host_context,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.request(
                method=descriptor.get("method", "POST"),
                url=endpoint,
                headers=descriptor.get("headers") or {},
                json=request_payload,
            )
            response.raise_for_status()
            data = response.json()

        if isinstance(data, dict):
            return data
        return {"result": data}

    def parse_tool_arguments(self, raw_arguments: str) -> dict[str, Any]:
        if not raw_arguments:
            return {}
        stripped = raw_arguments.strip()
        if stripped in {"{", "}", "{}"}:
            return {}
        try:
            return json.loads(raw_arguments)
        except json.JSONDecodeError:
            cleaned = raw_arguments.strip().strip("`")
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
                if isinstance(parsed, str):
                    return {"value": parsed}
                return {"value": parsed}

    def _schema(self, name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }


tool_service = ToolService()
