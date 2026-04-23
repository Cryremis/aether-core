# backend/app/services/session_service.py
"""会话服务。

负责会话生命周期、元数据落盘与工作区绑定。
会话数据模型定义位于 session_types，避免类型与服务实现耦合。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.sandbox.manager import sandbox_manager
from app.services.context.bootstrap import configure_context_runtime
from app.services.session_types import AgentSession


class SessionService:
    """管理 AetherCore 会话与对应工作区。"""

    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}

    def get_or_create(self, session_id: str | None = None) -> AgentSession:
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            session.touch()
            return session

        new_id = session_id or f"sess_{uuid.uuid4().hex}"
        session = self._load_from_disk(new_id) or AgentSession(session_id=new_id)
        session.workspace = sandbox_manager.ensure_workspace(
            new_id,
            Path(session.baseline_root) if session.baseline_root else None,
        )
        self._sessions[new_id] = session
        self._write_metadata(session)
        return session

    def _metadata_path(self, session_id: str) -> Path:
        return settings.sessions_root / session_id / "sandbox" / "metadata" / "session.json"

    def attach_host(
        self,
        session: AgentSession,
        host_name: str,
        context: dict[str, Any],
        tools: list[dict[str, Any]],
        skills: list[dict[str, Any]],
        apis: list[dict[str, Any]],
    ) -> AgentSession:
        session.host_name = host_name
        session.host_context = context
        session.host_tools = tools
        session.host_skills = skills
        session.host_apis = apis
        session.touch()
        self._write_metadata(session)
        return session

    def persist(self, session: AgentSession) -> None:
        session.touch()
        self._write_metadata(session)

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
        session.baseline_root = str(baseline_root.resolve())
        session.workspace = sandbox_manager.ensure_workspace(session.session_id, baseline_root)
        self.persist(session)

    def _load_from_disk(self, session_id: str) -> AgentSession | None:
        metadata_path = self._metadata_path(session_id)
        if not metadata_path.exists():
            return None
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        return AgentSession(
            session_id=session_id,
            conversation_id=payload.get("conversation_id"),
            host_name=payload.get("host_name", ""),
            baseline_root=payload.get("baseline_root", ""),
            messages=payload.get("messages", []),
            host_context=payload.get("host_context", {}),
            platform_files=payload.get("platform_files", []),
            platform_skills=payload.get("platform_skills", []),
            host_tools=payload.get("host_tools", []),
            host_skills=payload.get("host_skills", []),
            uploaded_skills=payload.get("uploaded_skills", []),
            host_apis=payload.get("host_apis", []),
            artifacts=payload.get("artifacts", []),
            uploads=payload.get("uploads", []),
            context_state=payload.get("context_state", {}),
            message_schema_version=int(payload.get("message_schema_version", 1)),
            allow_network=bool(payload.get("allow_network", True)),
            created_at=float(payload.get("created_at", time.time())),
            last_access=float(payload.get("last_access", time.time())),
            workspace=sandbox_manager.ensure_workspace(
                session_id,
                Path(payload["baseline_root"]) if payload.get("baseline_root") else None,
            ),
        )

    def _write_metadata(self, session: AgentSession) -> None:
        if session.workspace is None:
            return
        metadata_path = Path(session.workspace.metadata_dir) / "session.json"
        payload = {
            "session_id": session.session_id,
            "conversation_id": session.conversation_id,
            "host_name": session.host_name,
            "baseline_root": session.baseline_root,
            "messages": session.messages,
            "host_context": session.host_context,
            "platform_files": session.platform_files,
            "platform_skills": session.platform_skills,
            "host_tools": session.host_tools,
            "host_skills": session.host_skills,
            "uploaded_skills": session.uploaded_skills,
            "host_apis": session.host_apis,
            "artifacts": session.artifacts,
            "uploads": session.uploads,
            "context_state": session.context_state,
            "message_schema_version": session.message_schema_version,
            "allow_network": session.allow_network,
            "created_at": session.created_at,
            "last_access": session.last_access,
        }
        metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def delete_session(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
        metadata_path = self._metadata_path(session_id)
        session_dir = settings.sessions_root / session_id
        if session_dir.exists():
            import shutil

            shutil.rmtree(session_dir, ignore_errors=True)
            return True
        return metadata_path.exists()


session_service = SessionService()
configure_context_runtime(session_service)
