# backend/app/services/file_service.py
from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.schemas.files import FileRecord
from app.sandbox.manager import sandbox_manager
from app.services.session_service import AgentSession, session_service


class FileService:
    """负责处理会话文件上传、读取与定位。"""

    async def save_upload(self, session: AgentSession, upload_file: UploadFile) -> FileRecord:
        assert session.workspace is not None

        suffix = Path(upload_file.filename or "").suffix
        safe_name = upload_file.filename or f"upload_{uuid.uuid4().hex}{suffix}"
        file_id = f"file_{uuid.uuid4().hex}"
        target_path = session.workspace.input_dir / f"{file_id}_{safe_name}"
        target_path = sandbox_manager.ensure_within_workspace(session.workspace, target_path)

        content = await upload_file.read()
        target_path.write_bytes(content)

        media_type = upload_file.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        record = FileRecord(
            file_id=file_id,
            session_id=session.session_id,
            name=safe_name,
            relative_path=str(target_path.relative_to(session.workspace.root)),
            size=len(content),
            media_type=media_type,
            category="upload",
        )
        session.uploads.append(record.model_dump(mode="json"))
        session_service.persist(session)
        return record

    def list_uploads(self, session: AgentSession) -> list[FileRecord]:
        return [FileRecord(**item) for item in session.uploads]

    def list_platform_files(self, session: AgentSession) -> list[FileRecord]:
        return [FileRecord(**item) for item in session.platform_files]

    def list_visible_files(self, session: AgentSession) -> list[FileRecord]:
        return [
            *self.list_platform_files(session),
            *self.list_uploads(session),
        ]

    def resolve_file_path(self, session: AgentSession, file_id: str) -> Path | None:
        assert session.workspace is not None
        for item in [*session.platform_files, *session.uploads, *session.artifacts]:
            if item["file_id"] == file_id:
                return sandbox_manager.resolve_logical_path(session.workspace, item["relative_path"])
        return None

    def read_text(
        self,
        session: AgentSession,
        *,
        file_id: str | None = None,
        relative_path: str | None = None,
    ) -> str:
        assert session.workspace is not None
        target_path: Path | None = None
        if file_id:
            target_path = self.resolve_file_path(session, file_id)
        elif relative_path:
            target_path = sandbox_manager.resolve_logical_path(session.workspace, relative_path)

        if not target_path or not target_path.exists():
            raise FileNotFoundError("目标文件不存在。")
        data = target_path.read_bytes()
        limited = data[: settings.sandbox_file_read_limit_bytes]
        text = limited.decode("utf-8", errors="replace")
        if len(data) > len(limited):
            text += "\n\n[文件内容已按大小限制截断]"
        return text


file_service = FileService()
