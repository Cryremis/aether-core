# backend/app/services/session_service.py
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.sandbox.manager import sandbox_manager
from app.sandbox.models import SandboxWorkspace


@dataclass
class AgentSession:
    """AetherCore 会话状态。"""

    session_id: str
    host_name: str = ""
    host_type: str = "custom"
    messages: list[dict[str, Any]] = field(default_factory=list)
    host_context: dict[str, Any] = field(default_factory=dict)
    host_tools: list[dict[str, Any]] = field(default_factory=list)
    host_skills: list[dict[str, Any]] = field(default_factory=list)
    uploaded_skills: list[dict[str, Any]] = field(default_factory=list)
    host_apis: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    uploads: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)
    workspace: SandboxWorkspace | None = None

    def touch(self) -> None:
        self.last_access = time.time()


class SessionService:
    """管理 AetherCore 会话与会话工作区。"""

    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}

    def get_or_create(self, session_id: str | None = None) -> AgentSession:
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            session.touch()
            return session

        new_id = session_id or f"sess_{uuid.uuid4().hex}"
        session = AgentSession(session_id=new_id)
        session.workspace = sandbox_manager.ensure_workspace(new_id)
        self._sessions[new_id] = session
        self._write_metadata(session)
        return session

    def attach_host(
        self,
        session: AgentSession,
        host_name: str,
        host_type: str,
        context: dict[str, Any],
        tools: list[dict[str, Any]],
        skills: list[dict[str, Any]],
        apis: list[dict[str, Any]],
    ) -> AgentSession:
        session.host_name = host_name
        session.host_type = host_type
        session.host_context = context
        session.host_tools = tools
        session.host_skills = skills
        session.host_apis = apis
        session.touch()
        self._write_metadata(session)
        return session

    def save_uploaded_skill(self, session: AgentSession, skill: dict[str, Any]) -> None:
        session.uploaded_skills = [item for item in session.uploaded_skills if item["name"] != skill["name"]]
        session.uploaded_skills.append(skill)
        session.touch()
        self._write_metadata(session)

    def _write_metadata(self, session: AgentSession) -> None:
        if session.workspace is None:
            return
        metadata_path = Path(session.workspace.metadata_dir) / "session.json"
        payload = {
            "session_id": session.session_id,
            "host_name": session.host_name,
            "host_type": session.host_type,
            "host_context": session.host_context,
            "host_tools": session.host_tools,
            "host_skills": session.host_skills,
            "uploaded_skills": session.uploaded_skills,
            "host_apis": session.host_apis,
            "created_at": session.created_at,
            "last_access": session.last_access,
        }
        metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


session_service = SessionService()
