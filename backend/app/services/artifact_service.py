# backend/app/services/artifact_service.py
from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from app.schemas.files import FileRecord
from app.sandbox.manager import sandbox_manager
from app.services.session_service import session_service
from app.services.session_types import AgentSession


class ArtifactService:
    """管理会话产物文件。"""

    def create_text_artifact(
        self,
        session: AgentSession,
        name: str,
        content: str,
    ) -> FileRecord:
        assert session.workspace is not None

        file_id = f"artifact_{uuid.uuid4().hex}"
        target_path = session.workspace.output_dir / f"{file_id}_{name}"
        target_path = sandbox_manager.ensure_within_workspace(session.workspace, target_path)
        target_path.write_text(content, encoding="utf-8")
        return self._register_path(session, file_id=file_id, name=name, target_path=target_path)

    def sync_output_directory(self, session: AgentSession) -> list[FileRecord]:
        assert session.workspace is not None
        known_paths = {item["relative_path"] for item in session.artifacts}
        created: list[FileRecord] = []
        for file_path in sorted(session.workspace.output_dir.rglob("*")):
            if not file_path.is_file():
                continue
            relative_path = str(file_path.relative_to(session.workspace.root))
            if relative_path in known_paths:
                continue
            file_id = f"artifact_{uuid.uuid4().hex}"
            created.append(self._register_path(session, file_id=file_id, name=file_path.name, target_path=file_path))
        return created

    def list_artifacts(self, session: AgentSession) -> list[FileRecord]:
        self.sync_output_directory(session)
        return [FileRecord(**item) for item in session.artifacts]

    def _register_path(
        self,
        session: AgentSession,
        *,
        file_id: str,
        name: str,
        target_path: Path,
    ) -> FileRecord:
        media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        record = FileRecord(
            file_id=file_id,
            session_id=session.session_id,
            name=name,
            relative_path=str(target_path.relative_to(session.workspace.root)),
            size=target_path.stat().st_size,
            media_type=media_type,
            category="artifact",
        )
        session.artifacts.append(record.model_dump(mode="json"))
        session_service.persist(session)
        return record


artifact_service = ArtifactService()
