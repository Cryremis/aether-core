# backend/app/services/session_service.py
"""会话上下文服务。

AgentSession 只代表上下文；文件、基线与 runtime 归 Workspace 所有。
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import shutil
import time
import threading
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.sandbox.manager import sandbox_manager
from app.services.context.bootstrap import configure_context_runtime
from app.services.session_types import AgentSession
from app.services.store import store_service
from app.services.transcript_service import transcript_service
from app.services.tool_catalog_service import tool_catalog_service
from app.services.workspace_service import workspace_service


class SessionService:
    """管理会话上下文与共享 Workspace 的引用关系。"""

    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._metadata_locks: dict[str, threading.RLock] = {}
        self._metadata_locks_guard = threading.Lock()

    def get_or_create(
        self,
        session_id: str | None = None,
        workspace_id: str | None = None,
        *,
        role: str = "owner",
        parent_session_id: str | None = None,
    ) -> AgentSession:
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            if workspace_id and session.workspace_id != workspace_id:
                raise ValueError("会话已绑定到另一个 Workspace")
            session.touch()
            store_service.touch_workspace_session(session.session_id)
            return session

        new_id = session_id or f"sess_{uuid.uuid4().hex}"
        session = self._load_from_disk(new_id) or AgentSession(session_id=new_id)
        self.reconcile_active_run_view(session)
        resolved_workspace_id = session.workspace_id or workspace_id or self._find_workspace_id(new_id)
        if resolved_workspace_id is None:
            workspace = workspace_service.create_workspace(
                new_id,
                baseline_root=session.baseline_root,
            )
            resolved_workspace_id = str(workspace["workspace_id"])

        if workspace_id and resolved_workspace_id != workspace_id:
            raise ValueError("会话元数据与指定 Workspace 不一致")

        workspace_record = store_service.get_workspace(resolved_workspace_id)
        if workspace_record is None:
            raise LookupError("Workspace 不存在")
        if session.baseline_root:
            store_service.update_workspace(
                resolved_workspace_id,
                baseline_root=session.baseline_root,
            )
        elif workspace_record.get("baseline_root"):
            session.baseline_root = str(workspace_record["baseline_root"])

        existing_member = store_service.get_workspace_member(new_id) or {}
        effective_role = str(existing_member.get("role") or role)
        effective_parent = str(existing_member.get("parent_session_id") or "") or parent_session_id
        store_service.attach_workspace_session(
            workspace_id=resolved_workspace_id,
            session_id=new_id,
            conversation_id=session.conversation_id,
            role=effective_role,
            parent_session_id=effective_parent,
        )
        if session.conversation_id:
            store_service.update_workspace(
                resolved_workspace_id,
                owner_conversation_id=session.conversation_id,
            )

        session.workspace_id = resolved_workspace_id
        session.workspace = workspace_service.resolve_workspace(
            resolved_workspace_id,
            workspace_record.get("owner_session_id") or new_id,
        )
        self._sessions[new_id] = session
        self._write_metadata(session)
        return session

    def _find_workspace_id(self, session_id: str) -> str | None:
        conversation = store_service.get_conversation_by_session(session_id)
        if conversation and conversation.get("workspace_id"):
            return str(conversation["workspace_id"])
        member = store_service.get_workspace_member(session_id)
        if member and member.get("workspace_id"):
            return str(member["workspace_id"])
        return None

    def _metadata_path(self, session_id: str) -> Path:
        return settings.sessions_root / "session-state" / session_id / "session.json"

    def _metadata_lock(self, session_id: str) -> threading.RLock:
        with self._metadata_locks_guard:
            return self._metadata_locks.setdefault(session_id, threading.RLock())

    def attach_host(
        self,
        session: AgentSession,
        host_name: str,
        context: dict[str, Any],
        tools: list[dict[str, Any]],
        skills: list[dict[str, Any]],
        system_prompts: list[dict[str, Any]],
        apis: list[dict[str, Any]],
        tool_source_id: str = "host:bind",
        replace_all_tools: bool = True,
        tool_refresh_policy: str = "static_run",
    ) -> AgentSession:
        session.host_name = host_name
        session.host_context = context
        tool_catalog_service.replace(
            session,
            tools,
            source_id=tool_source_id,
            replace_all=replace_all_tools,
        )
        session.tool_refresh_policy = tool_refresh_policy
        session.host_skills = skills
        session.host_system_prompts = system_prompts
        session.host_apis = apis
        self.persist(session)
        return session

    def persist(self, session: AgentSession) -> None:
        session.touch()
        store_service.touch_workspace_session(session.session_id)
        self._write_metadata(session)

    def clone_host_state(self, source: AgentSession, target: AgentSession) -> None:
        """克隆宿主绑定，不克隆消息历史，也不改变 Workspace 归属。"""
        target.host_name = source.host_name
        target.owner_user_id = source.owner_user_id
        target.platform_id = source.platform_id
        target.external_user_id = source.external_user_id
        target.host_context = copy.deepcopy(source.host_context)
        target.host_tools = copy.deepcopy(source.host_tools)
        target.host_tools_revision = source.host_tools_revision
        target.host_tools_fingerprint = source.host_tools_fingerprint
        target.host_tool_sources = copy.deepcopy(source.host_tool_sources)
        target.host_tool_collections = copy.deepcopy(source.host_tool_collections)
        target.tool_refresh_policy = source.tool_refresh_policy
        target.host_skills = copy.deepcopy(source.host_skills)
        target.host_system_prompts = copy.deepcopy(source.host_system_prompts)
        target.host_apis = copy.deepcopy(source.host_apis)
        self.persist(target)

    def set_allow_network(self, session: AgentSession, allow_network: bool) -> None:
        session.allow_network = allow_network
        self.persist(session)

    def save_uploaded_skill(self, session: AgentSession, skill: dict[str, Any]) -> None:
        session.uploaded_skills = [item for item in session.uploaded_skills if item["name"] != skill["name"]]
        session.uploaded_skills.append(skill)
        self.persist(session)

    def replace_uploaded_skills(self, session: AgentSession, skills: list[dict[str, Any]]) -> None:
        session.uploaded_skills = skills
        self.persist(session)

    def replace_platform_assets(
        self,
        session: AgentSession,
        *,
        files: list[dict[str, Any]],
        skills: list[dict[str, Any]],
    ) -> None:
        session.platform_files = files
        session.platform_skills = skills
        self.persist(session)

    def bind_baseline_root(self, session: AgentSession, baseline_root: Path) -> None:
        if not session.workspace_id:
            raise RuntimeError("会话尚未绑定 Workspace")
        session.baseline_root = str(baseline_root.resolve())
        store_service.update_workspace(
            session.workspace_id,
            baseline_root=session.baseline_root,
        )
        workspace_record = store_service.get_workspace(session.workspace_id) or {}
        session.workspace = workspace_service.resolve_workspace(
            session.workspace_id,
            str(workspace_record.get("owner_session_id") or session.session_id),
        )
        self.persist(session)

    def _load_from_disk(self, session_id: str) -> AgentSession | None:
        metadata_path = self._metadata_path(session_id)
        if not metadata_path.exists():
            return None
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        return AgentSession(
            session_id=session_id,
            workspace_id=str(payload.get("workspace_id") or ""),
            conversation_id=payload.get("conversation_id"),
            owner_user_id=payload.get("owner_user_id"),
            platform_id=payload.get("platform_id"),
            external_user_id=payload.get("external_user_id"),
            host_name=payload.get("host_name", ""),
            baseline_root=payload.get("baseline_root", ""),
            messages=payload.get("messages", []),
            transcript=payload.get(
                "transcript",
                transcript_service.build_persisted_transcript(payload.get("messages", [])),
            ),
            host_context=payload.get("host_context", {}),
            platform_files=payload.get("platform_files", []),
            platform_skills=payload.get("platform_skills", []),
            host_tools=payload.get("host_tools", []),
            host_tools_revision=int(payload.get("host_tools_revision", 0)),
            host_tools_fingerprint=str(payload.get("host_tools_fingerprint") or ""),
            host_tool_sources={
                str(key): str(value)
                for key, value in (payload.get("host_tool_sources") or {}).items()
            },
            host_tool_collections={
                str(source_id): [dict(item) for item in tools if isinstance(item, dict)]
                for source_id, tools in (payload.get("host_tool_collections") or {}).items()
                if isinstance(tools, list)
            },
            tool_refresh_policy=str(payload.get("tool_refresh_policy") or "static_run"),
            host_skills=payload.get("host_skills", []),
            host_system_prompts=payload.get("host_system_prompts", []),
            uploaded_skills=payload.get("uploaded_skills", []),
            session_mcp=payload.get("session_mcp", []),
            host_apis=payload.get("host_apis", []),
            artifacts=payload.get("artifacts", []),
            uploads=payload.get("uploads", []),
            context_state=payload.get("context_state", {}),
            message_schema_version=int(payload.get("message_schema_version", 1)),
            allow_network=bool(payload.get("allow_network", True)),
            disabled_capability_ids=[str(item) for item in payload.get("disabled_capability_ids", [])],
            allowed_tools=[str(item) for item in payload.get("allowed_tools", [])]
            if payload.get("allowed_tools") is not None
            else None,
            subagent_tools_enabled=bool(payload.get("subagent_tools_enabled", True)),
            created_at=float(payload.get("created_at", time.time())),
            last_access=float(payload.get("last_access", time.time())),
            active_run_view=payload.get("active_run_view"),
        )

    def reconcile_active_run_view(self, session: AgentSession) -> None:
        """把磁盘上的运行快照与数据库 run 状态对齐，避免重启后 UI 永远显示执行中。"""
        active_view = session.active_run_view
        if not active_view:
            return
        run_id = str(active_view.get("run_id") or "")
        if not run_id:
            session.active_run_view = None
            return
        run = store_service.get_agent_run(run_id)
        if run is None or str(run.get("status")) in store_service.TERMINAL_AGENT_RUN_STATUSES:
            session.active_run_view = None

    def _write_metadata(self, session: AgentSession) -> None:
        if not session.workspace_id:
            raise RuntimeError("会话尚未绑定 Workspace")
        payload = {
            "session_id": session.session_id,
            "workspace_id": session.workspace_id,
            "conversation_id": session.conversation_id,
            "owner_user_id": session.owner_user_id,
            "platform_id": session.platform_id,
            "external_user_id": session.external_user_id,
            "host_name": session.host_name,
            "baseline_root": session.baseline_root,
            "messages": session.messages,
            "transcript": session.transcript,
            "host_context": session.host_context,
            "platform_files": session.platform_files,
            "platform_skills": session.platform_skills,
            "host_tools": session.host_tools,
            "host_tools_revision": session.host_tools_revision,
            "host_tools_fingerprint": session.host_tools_fingerprint,
            "host_tool_sources": session.host_tool_sources,
            "host_tool_collections": session.host_tool_collections,
            "tool_refresh_policy": session.tool_refresh_policy,
            "host_skills": session.host_skills,
            "host_system_prompts": session.host_system_prompts,
            "uploaded_skills": session.uploaded_skills,
            "session_mcp": session.session_mcp,
            "host_apis": session.host_apis,
            "artifacts": session.artifacts,
            "uploads": session.uploads,
            "context_state": session.context_state,
            "message_schema_version": session.message_schema_version,
            "allow_network": session.allow_network,
            "disabled_capability_ids": session.disabled_capability_ids,
            "allowed_tools": session.allowed_tools,
            "subagent_tools_enabled": session.subagent_tools_enabled,
            "created_at": session.created_at,
            "last_access": session.last_access,
            "active_run_view": session.active_run_view,
        }
        metadata_path = self._metadata_path(session.session_id)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        temporary_path = metadata_path.with_name(
            f"{metadata_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )

        # Windows 下目标文件可能被短暂的杀毒/索引/读取占用；串行化同会话写入，
        # 并用唯一临时文件避免两个写入方抢占同一个 .tmp。
        with self._metadata_lock(session.session_id):
            try:
                temporary_path.write_text(serialized, encoding="utf-8")
                for attempt in range(20):
                    try:
                        os.replace(temporary_path, metadata_path)
                        return
                    except PermissionError:
                        if attempt == 19:
                            raise
                        time.sleep(0.05)
            finally:
                temporary_path.unlink(missing_ok=True)

    def delete_session(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        workspace_id = (session.workspace_id if session else None) or self._find_workspace_id(session_id)
        self._sessions.pop(session_id, None)
        store_service.delete_workspace_session(session_id)

        if workspace_id and store_service.count_workspace_members(workspace_id) == 0:
            self._delete_workspace(workspace_id)

        state_path = self._metadata_path(session_id)
        existed = state_path.exists()
        if state_path.parent.exists():
            self._remove_directory(state_path.parent)
        return existed

    def _delete_workspace(self, workspace_id: str) -> None:
        from app.services.workspace_runtime_service import workspace_runtime_service

        async def cleanup() -> None:
            await workspace_runtime_service.delete_runtime(workspace_id, reason="workspace_deleted")
            store_service.mark_workspace_deleted(workspace_id)
            self._remove_workspace_directory(workspace_id)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(cleanup())
        else:
            loop.create_task(cleanup())

    @staticmethod
    def _remove_workspace_directory(workspace_id: str) -> None:
        target = (settings.sessions_root / "workspaces" / workspace_id).resolve()
        root = (settings.sessions_root / "workspaces").resolve()
        if target != root and root in target.parents:
            shutil.rmtree(target, ignore_errors=True)

    @staticmethod
    def _remove_directory(target: Path) -> None:
        resolved = target.resolve()
        root = settings.sessions_root.resolve()
        if resolved == root or root not in resolved.parents:
            raise RuntimeError("拒绝删除 sessions_root 之外的目录")
        shutil.rmtree(resolved, ignore_errors=True)


session_service = SessionService()
configure_context_runtime(session_service)
