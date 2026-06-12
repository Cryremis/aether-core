# backend/app/services/file_service.py
from __future__ import annotations

import mimetypes
import uuid
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.schemas.files import FileRecord
from app.sandbox.manager import sandbox_manager
from app.services.artifact_service import artifact_service
from app.services.session_service import session_service
from app.services.session_workspace_sync_service import session_workspace_sync_service
from app.services.session_types import AgentSession
from app.services.workspace_path_service import workspace_path_service


@dataclass(frozen=True)
class ReadResult:
    file_path: str
    content: str
    num_lines: int
    start_line: int
    total_lines: int
    truncated: bool
    size: int


@dataclass(frozen=True)
class ListEntry:
    path: str
    name: str
    entry_type: str
    size: int | None
    source: str
    file_id: str | None = None


class FileService:
    """负责处理会话文件上传、读取与定位。"""

    async def save_upload(self, session: AgentSession, upload_file: UploadFile) -> FileRecord:
        assert session.workspace is not None

        suffix = Path(upload_file.filename or "").suffix
        safe_name = self._safe_filename(upload_file.filename or f"upload{suffix or '.bin'}")
        target_path = self._dedupe_path(session.workspace.work_dir / safe_name)
        target_path = sandbox_manager.ensure_within_workspace(session.workspace, target_path)

        content = await upload_file.read()
        target_path.write_bytes(content)

        media_type = upload_file.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        record = self._record_from_path(
            session_id=session.session_id,
            root=session.workspace.root,
            file_path=target_path,
            media_type=media_type,
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

    def list_work_files(self, session: AgentSession) -> list[FileRecord]:
        assert session.workspace is not None
        records_by_relative: dict[str, FileRecord] = {}
        tombstones = set(session_workspace_sync_service.load_state(session.workspace).tombstones)

        if session.workspace.work_dir.exists():
            for file_path in sorted(
                session.workspace.work_dir.rglob("*"),
                key=lambda item: str(item.relative_to(session.workspace.work_dir)).lower(),
            ):
                if not file_path.is_file():
                    continue
                record = self._record_from_path(
                    session_id=session.session_id,
                    root=session.workspace.root,
                    file_path=file_path,
                )
                if record.relative_path in tombstones:
                    continue
                records_by_relative[record.relative_path] = record

        for item in self.list_platform_files(session):
            relative_path = str(item.relative_path or "").replace("\\", "/")
            if not relative_path.startswith("work/"):
                continue
            if self._is_deleted_relative_path(relative_path, tombstones):
                continue
            records_by_relative.setdefault(relative_path, item)

        return [records_by_relative[key] for key in sorted(records_by_relative.keys(), key=str.lower)]

    def list_sidebar_files(self, session: AgentSession) -> list[FileRecord]:
        return self.list_work_files(session)

    def resolve_file_path(self, session: AgentSession, file_id: str) -> Path | None:
        assert session.workspace is not None
        if file_id.startswith("work:"):
            relative_path = self._decode_work_file_id(file_id)
            target = sandbox_manager.resolve_logical_path(session.workspace, relative_path)
            work_root = session.workspace.work_dir.resolve(strict=False)
            try:
                target.resolve(strict=False).relative_to(work_root)
            except ValueError:
                return None
            return target
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
        if file_id and relative_path:
            raise ValueError("file_id 与 relative_path 不能同时提供。")

        target_path: Path | None = None
        if file_id:
            target_path = self.resolve_file_path(session, file_id)
        elif relative_path:
            normalized_relative = self._normalize_relative_path(relative_path)
            if session.workspace is not None and session_workspace_sync_service.is_deleted(session.workspace, normalized_relative):
                raise FileNotFoundError("目标文件不存在。")
            target_path = workspace_path_service.resolve_path(session, relative_path).host_path

        if not target_path or not target_path.exists():
            if relative_path and session.workspace is not None and session_workspace_sync_service.is_deleted(session.workspace, self._normalize_relative_path(relative_path)):
                raise FileNotFoundError("目标文件不存在。")
            raise FileNotFoundError("目标文件不存在。")
        if target_path.is_dir():
            raise IsADirectoryError("目标路径是目录，请改用 list 工具。")

        return target_path.read_text(encoding="utf-8", errors="replace")

    def read(
        self,
        session: AgentSession,
        *,
        file_id: str | None = None,
        file_path: str | None = None,
        offset: int = 1,
        limit: int | None = None,
    ) -> ReadResult:
        assert session.workspace is not None
        if file_id and file_path:
            raise ValueError("file_id 与 file_path 不能同时提供。")

        target_path: Path | None = None
        logical_path: str | None = None
        if file_id:
            target_path = self.resolve_file_path(session, file_id)
            if target_path is not None:
                logical_path = workspace_path_service.relative_to_logical_path(
                    self._relative_path_for_target(session, target_path)
                )
        elif file_path:
            normalized_relative = workspace_path_service.logical_to_relative_path(file_path)
            if session.workspace is not None and session_workspace_sync_service.is_deleted(session.workspace, self._normalize_relative_path(normalized_relative)):
                raise FileNotFoundError("目标文件不存在。")
            resolved = workspace_path_service.resolve_path(session, file_path)
            target_path = resolved.host_path
            logical_path = resolved.logical_path

        if not target_path or not target_path.exists():
            logical_candidate = None
            if file_path:
                logical_candidate = workspace_path_service.logical_to_relative_path(file_path)
            elif logical_path:
                logical_candidate = workspace_path_service.logical_to_relative_path(logical_path)
            if logical_candidate and session.workspace is not None and session_workspace_sync_service.is_deleted(session.workspace, self._normalize_relative_path(logical_candidate)):
                raise FileNotFoundError("目标文件不存在。")
            raise FileNotFoundError("目标文件不存在。")
        if target_path.is_dir():
            raise IsADirectoryError("目标路径是目录，请改用 list 工具。")

        data = target_path.read_bytes()
        decoded = data.decode("utf-8", errors="replace")
        lines = decoded.splitlines()
        total_lines = len(lines)
        start_line = max(1, int(offset or 1))
        start_index = start_line - 1

        line_slice = lines[start_index:] if limit is None else lines[start_index:start_index + max(0, int(limit))]
        numbered_text = self._add_line_numbers(line_slice, start_line=start_line)
        text = numbered_text
        limited = text.encode("utf-8")[: settings.sandbox_file_read_limit_bytes]
        output = limited.decode("utf-8", errors="replace")
        truncated = len(text.encode("utf-8")) > len(limited)
        if truncated:
            output += "\n\n[文件内容已按大小限制截断]"

        return ReadResult(
            file_path=logical_path or workspace_path_service.relative_to_logical_path(
                self._relative_path_for_target(session, target_path)
            ),
            content=output,
            num_lines=len(line_slice),
            start_line=start_line,
            total_lines=total_lines,
            truncated=truncated,
            size=len(data),
        )

    def list(
        self,
        session: AgentSession,
        *,
        path: str | None = None,
        limit: int = 200,
    ) -> list[ListEntry]:
        assert session.workspace is not None
        resolved = workspace_path_service.resolve_path(session, path)
        if not resolved.exists:
            raise FileNotFoundError("目标路径不存在。")
        if not resolved.is_dir:
            raise NotADirectoryError("目标路径不是目录，请改用 read 工具。")

        visible_by_path = {
            item.relative_path: item
            for item in [
                *self.list_visible_files(session),
                *artifact_service.list_artifacts(session),
            ]
        }

        relative_dir = workspace_path_service.logical_to_relative_path(resolved.logical_path)
        workspace_dir = sandbox_manager.ensure_within_workspace(session.workspace, session.workspace.root / relative_dir)
        baseline_dir = self._baseline_path_for_relative(session, relative_dir)
        tombstones = set(session_workspace_sync_service.load_state(session.workspace).tombstones)
        merged_children = self._merge_directory_children(
            relative_dir=relative_dir,
            workspace_dir=workspace_dir,
            baseline_dir=baseline_dir,
            tombstones=tombstones,
        )

        entries: list[ListEntry] = []
        for child in merged_children[: max(0, limit)]:
            relative_path = self._relative_path_for_target(session, child)
            logical_path = workspace_path_service.relative_to_logical_path(relative_path)
            metadata = visible_by_path.get(relative_path)
            source = metadata.category if metadata else ("directory" if child.is_dir() else "workspace")
            entries.append(
                ListEntry(
                    path=logical_path,
                    name=child.name,
                    entry_type="dir" if child.is_dir() else "file",
                    size=None if child.is_dir() else child.stat().st_size,
                    source=source,
                    file_id=metadata.file_id if metadata else None,
                )
            )
        return entries

    def _add_line_numbers(self, lines: list[str], *, start_line: int) -> str:
        if not lines:
            return ""
        return "\n".join(f"{index}\t{line}" for index, line in enumerate(lines, start=start_line))

    def _safe_filename(self, filename: str) -> str:
        name = Path(filename).name.strip()
        return name or f"upload-{uuid.uuid4().hex}.bin"

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

    def _record_from_path(
        self,
        *,
        session_id: str,
        root: Path,
        file_path: Path,
        media_type: str | None = None,
    ) -> FileRecord:
        stat = file_path.stat()
        relative_path = str(file_path.relative_to(root)).replace("\\", "/")
        modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        return FileRecord(
            file_id=self._encode_work_file_id(relative_path),
            session_id=session_id,
            name=file_path.name,
            relative_path=relative_path,
            size=stat.st_size,
            media_type=media_type or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream",
            category="work",
            created_at=modified_at,
            modified_at=modified_at,
        )

    def _encode_work_file_id(self, relative_path: str) -> str:
        encoded = base64.urlsafe_b64encode(relative_path.encode("utf-8")).decode("ascii").rstrip("=")
        return f"work:{encoded}"

    def _decode_work_file_id(self, file_id: str) -> str:
        value = file_id[len("work:") :]
        try:
            padded = value + "=" * (-len(value) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            if decoded.startswith("work/"):
                return decoded
        except Exception:
            pass
        return value

    def is_workspace_owned_path(self, session: AgentSession, target_path: Path) -> bool:
        if session.workspace is None:
            return False
        try:
            target_path.resolve(strict=False).relative_to(session.workspace.root.resolve(strict=False))
        except ValueError:
            return False
        return True

    def _relative_path_for_target(self, session: AgentSession, target_path: Path) -> str:
        assert session.workspace is not None
        resolved_target = target_path.resolve(strict=False)
        workspace_root = session.workspace.root.resolve(strict=False)
        try:
            return str(resolved_target.relative_to(workspace_root)).replace("\\", "/")
        except ValueError:
            pass

        baseline_root = session.workspace.baseline_root
        if baseline_root is not None:
            resolved_baseline = baseline_root.resolve(strict=False)
            try:
                return str(resolved_target.relative_to(resolved_baseline)).replace("\\", "/")
            except ValueError:
                pass

        raise ValueError("目标路径不属于当前会话工作区或平台基线。")

    def _baseline_path_for_relative(self, session: AgentSession, relative_path: str) -> Path | None:
        assert session.workspace is not None
        if session.workspace.baseline_root is None:
            return None
        candidate = (session.workspace.baseline_root / relative_path).resolve(strict=False)
        baseline_root = session.workspace.baseline_root.resolve(strict=False)
        try:
            candidate.relative_to(baseline_root)
        except ValueError:
            return None
        return candidate

    def _merge_directory_children(
        self,
        *,
        relative_dir: str,
        workspace_dir: Path,
        baseline_dir: Path | None,
        tombstones: set[str],
    ) -> list[Path]:
        merged: dict[str, Path] = {}
        if workspace_dir.exists() and workspace_dir.is_dir():
            for child in workspace_dir.iterdir():
                child_relative = self._join_relative(relative_dir, child.name)
                if self._is_deleted_relative_path(child_relative, tombstones):
                    continue
                merged[child.name] = child
        if baseline_dir is not None and baseline_dir.exists() and baseline_dir.is_dir():
            for child in baseline_dir.iterdir():
                child_relative = self._join_relative(relative_dir, child.name)
                if self._is_deleted_relative_path(child_relative, tombstones):
                    continue
                merged.setdefault(child.name, child)
        return sorted(merged.values(), key=lambda item: (not item.is_dir(), item.name.lower()))

    def _normalize_relative_path(self, value: str) -> str:
        return str(value or "").replace("\\", "/").strip("/")

    def _join_relative(self, parent: str, name: str) -> str:
        parent_normalized = self._normalize_relative_path(parent)
        if not parent_normalized:
            return self._normalize_relative_path(name)
        return f"{parent_normalized}/{name}"

    def _is_deleted_relative_path(self, relative_path: str, tombstones: set[str]) -> bool:
        normalized = self._normalize_relative_path(relative_path)
        return any(
            normalized == tombstone or normalized.startswith(f"{tombstone}/")
            for tombstone in tombstones
        )


file_service = FileService()
