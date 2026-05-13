# backend/app/services/artifact_service.py
from __future__ import annotations

import mimetypes
import uuid
import base64
from datetime import datetime, timezone
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

        target_path = self._dedupe_path(session.workspace.work_dir / self._safe_filename(name))
        target_path = sandbox_manager.ensure_within_workspace(session.workspace, target_path)
        target_path.write_text(content, encoding="utf-8")
        relative_path = str(target_path.relative_to(session.workspace.root)).replace("\\", "/")
        return self._register_path(session, file_id=self._encode_work_file_id(relative_path), name=target_path.name, target_path=target_path)

    def sync_work_directory(self, session: AgentSession) -> list[FileRecord]:
        assert session.workspace is not None
        known_paths = {item["relative_path"] for item in session.artifacts}
        created: list[FileRecord] = []
        root = session.workspace.work_dir
        if root.exists():
            for file_path in sorted(root.rglob("*")):
                if not file_path.is_file():
                    continue
                relative_path = str(file_path.relative_to(session.workspace.root)).replace("\\", "/")
                if relative_path in known_paths:
                    continue
                created.append(self._register_path(session, file_id=self._encode_work_file_id(relative_path), name=file_path.name, target_path=file_path))
                known_paths.add(relative_path)
        session.artifacts = [
            item
            for item in session.artifacts
            if sandbox_manager.resolve_logical_path(session.workspace, str(item.get("relative_path") or "")).exists()
        ]
        session_service.persist(session)
        return created

    def list_artifacts(self, session: AgentSession) -> list[FileRecord]:
        self.sync_work_directory(session)
        return [FileRecord(**item) for item in session.artifacts]

    def list_work_artifacts(self, session: AgentSession) -> list[FileRecord]:
        assert session.workspace is not None
        if not session.workspace.work_dir.exists():
            return []
        return [
            self._record_from_path(session, file_path)
            for file_path in sorted(
                session.workspace.work_dir.rglob("*"),
                key=lambda item: str(item.relative_to(session.workspace.work_dir)).lower(),
            )
            if file_path.is_file()
        ]

    def _register_path(
        self,
        session: AgentSession,
        *,
        file_id: str,
        name: str,
        target_path: Path,
    ) -> FileRecord:
        media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        stat = target_path.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        relative_path = str(target_path.relative_to(session.workspace.root)).replace("\\", "/")
        record = FileRecord(
            file_id=file_id,
            session_id=session.session_id,
            name=name,
            relative_path=relative_path,
            size=stat.st_size,
            media_type=media_type,
            category="work" if relative_path.startswith("work/") else "artifact",
            created_at=modified_at,
            modified_at=modified_at,
        )
        session.artifacts = [
            item
            for item in session.artifacts
            if item.get("relative_path") != record.relative_path and item.get("file_id") != record.file_id
        ]
        session.artifacts.append(record.model_dump(mode="json"))
        session_service.persist(session)
        return record

    def _safe_filename(self, filename: str) -> str:
        name = Path(filename).name.strip()
        return name or f"artifact-{uuid.uuid4().hex}.txt"

    def _dedupe_path(self, target_path: Path) -> Path:
        if not target_path.exists():
            return target_path
        stem = target_path.stem
        suffix = target_path.suffix
        parent = target_path.parent
        index = 2
        while True:
            candidate = parent / f"{stem} ({index}){suffix}"
            if not candidate.exists():
                return candidate
            index += 1

    def _record_from_path(self, session: AgentSession, file_path: Path) -> FileRecord:
        stat = file_path.stat()
        relative_path = str(file_path.relative_to(session.workspace.root)).replace("\\", "/")
        modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        return FileRecord(
            file_id=self._encode_work_file_id(relative_path),
            session_id=session.session_id,
            name=file_path.name,
            relative_path=relative_path,
            size=stat.st_size,
            media_type=mimetypes.guess_type(file_path.name)[0] or "application/octet-stream",
            category="work",
            created_at=modified_at,
            modified_at=modified_at,
        )

    def _encode_work_file_id(self, relative_path: str) -> str:
        encoded = base64.urlsafe_b64encode(relative_path.encode("utf-8")).decode("ascii").rstrip("=")
        return f"work:{encoded}"


artifact_service = ArtifactService()
