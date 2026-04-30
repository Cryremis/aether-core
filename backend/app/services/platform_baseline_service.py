# backend/app/services/platform_baseline_service.py
from __future__ import annotations

import io
import mimetypes
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Literal

from fastapi import UploadFile

from app.core.config import settings
from app.schemas.files import FileRecord
from app.schemas.platform import (
    PlatformBaselineDirectoryRequest,
    PlatformBaselineEntry,
    PlatformBaselineFile,
    PlatformBaselineFileContent,
    PlatformBaselineMoveRequest,
    PlatformBaselineSkill,
    PlatformBaselineSummary,
    PlatformBaselineWriteRequest,
)
from app.services.session_service import session_service
from app.services.session_types import AgentSession
from app.services.skill_loader import skill_loader


class PlatformBaselineService:
    """管理平台基线环境。"""

    _ROOT_DIRECTORIES = ("input", "skills", "work", "output", "logs")

    def ensure_platform_root(self, platform_key: str) -> Path:
        root = settings.platform_baselines_root / platform_key.strip().lower()
        for section in self._ROOT_DIRECTORIES:
            (root / section).mkdir(parents=True, exist_ok=True)
        return root

    def list_summary(self, platform_key: str) -> PlatformBaselineSummary:
        return PlatformBaselineSummary(
            platform_key=platform_key,
            files=self.list_files(platform_key),
            entries=self.list_entries(platform_key),
            skills=self.list_skills(platform_key),
        )

    async def upload_file(
        self,
        platform_key: str,
        *,
        upload_file: UploadFile,
        target_relative_dir: str = "work",
    ) -> PlatformBaselineFile:
        root = self.ensure_platform_root(platform_key)
        safe_name = Path(upload_file.filename or f"file_{uuid.uuid4().hex}").name
        if not safe_name:
            raise RuntimeError("无效的文件名。")

        normalized_dir = target_relative_dir.replace("\\", "/").strip("/") or "work"
        section = self._extract_section(normalized_dir)
        target_path = self._ensure_within_root(root, root / normalized_dir / safe_name)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        content = await upload_file.read()
        target_path.write_bytes(content)

        media_type = upload_file.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        return PlatformBaselineFile(
            name=safe_name,
            relative_path=str(target_path.relative_to(root)).replace("\\", "/"),
            section=section,
            size=len(content),
            media_type=media_type,
        )

    def delete_file(self, platform_key: str, *, relative_path: str) -> None:
        root = self.ensure_platform_root(platform_key)
        target_path = self._ensure_within_root(root, root / relative_path)
        if relative_path.replace("\\", "/").strip("/") in set(self._ROOT_DIRECTORIES):
            raise RuntimeError("不允许删除平台基线根目录。")
        if not target_path.exists():
            raise FileNotFoundError("目标基线文件不存在。")
        if target_path.is_dir():
            shutil.rmtree(target_path)
            return
        target_path.unlink()

    def resolve_file(self, platform_key: str, *, relative_path: str) -> Path:
        root = self.ensure_platform_root(platform_key)
        target_path = self._ensure_within_root(root, root / relative_path)
        if not target_path.exists() or not target_path.is_file():
            raise FileNotFoundError("目标基线文件不存在。")
        return target_path

    def list_files(self, platform_key: str) -> list[PlatformBaselineFile]:
        root = self.ensure_platform_root(platform_key)
        files: list[PlatformBaselineFile] = []
        for section in self._ROOT_DIRECTORIES:
            section_root = root / section
            for path in sorted(item for item in section_root.rglob("*") if item.is_file()):
                media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                files.append(
                    PlatformBaselineFile(
                        name=path.name,
                        relative_path=str(path.relative_to(root)).replace("\\", "/"),
                        section=section,  # type: ignore[arg-type]
                        size=path.stat().st_size,
                        media_type=media_type,
                    )
                )
        return files

    def list_entries(self, platform_key: str) -> list[PlatformBaselineEntry]:
        root = self.ensure_platform_root(platform_key)
        entries: list[PlatformBaselineEntry] = []
        for section in self._ROOT_DIRECTORIES:
            section_root = root / section
            entries.append(
                PlatformBaselineEntry(
                    name=section,
                    relative_path=section,
                    section=section,  # type: ignore[arg-type]
                    kind="directory",
                )
            )
            for path in sorted(section_root.rglob("*")):
                relative_path = str(path.relative_to(root)).replace("\\", "/")
                if path.is_dir():
                    entries.append(
                        PlatformBaselineEntry(
                            name=path.name,
                            relative_path=relative_path,
                            section=section,  # type: ignore[arg-type]
                            kind="directory",
                        )
                    )
                    continue
                media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                entries.append(
                    PlatformBaselineEntry(
                        name=path.name,
                        relative_path=relative_path,
                        section=section,  # type: ignore[arg-type]
                        kind="file",
                        size=path.stat().st_size,
                        media_type=media_type,
                    )
                )
        return entries

    def read_text(self, platform_key: str, *, relative_path: str) -> PlatformBaselineFileContent:
        root = self.ensure_platform_root(platform_key)
        target_path = self._ensure_within_root(root, root / relative_path)
        if not target_path.exists() or not target_path.is_file():
            raise FileNotFoundError("目标基线文件不存在。")
        media_type = mimetypes.guess_type(target_path.name)[0] or "application/octet-stream"
        data = target_path.read_bytes()
        limited = data[: settings.sandbox_file_read_limit_bytes]
        return PlatformBaselineFileContent(
            relative_path=str(target_path.relative_to(root)).replace("\\", "/"),
            media_type=media_type,
            content=limited.decode("utf-8", errors="replace"),
            truncated=len(data) > len(limited),
        )

    def write_text(self, platform_key: str, request: PlatformBaselineWriteRequest) -> PlatformBaselineEntry:
        root = self.ensure_platform_root(platform_key)
        target_path = self._ensure_within_root(root, root / request.relative_path)
        section = self._extract_section(request.relative_path)
        if target_path.exists() and target_path.is_dir():
            raise FileExistsError("目标路径是目录，不能直接写入文本文件。")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(request.content, encoding="utf-8")
        return PlatformBaselineEntry(
            name=target_path.name,
            relative_path=str(target_path.relative_to(root)).replace("\\", "/"),
            section=section,
            kind="file",
            size=target_path.stat().st_size,
            media_type=mimetypes.guess_type(target_path.name)[0] or "text/plain",
        )

    def create_directory(self, platform_key: str, request: PlatformBaselineDirectoryRequest) -> PlatformBaselineEntry:
        root = self.ensure_platform_root(platform_key)
        target_path = self._ensure_within_root(root, root / request.relative_path)
        section = self._extract_section(request.relative_path)
        if target_path.exists() and not target_path.is_dir():
            raise FileExistsError("同名文件已存在，不能创建目录。")
        target_path.mkdir(parents=True, exist_ok=True)
        return PlatformBaselineEntry(
            name=target_path.name,
            relative_path=str(target_path.relative_to(root)).replace("\\", "/"),
            section=section,
            kind="directory",
        )

    def move_path(self, platform_key: str, request: PlatformBaselineMoveRequest) -> PlatformBaselineEntry:
        root = self.ensure_platform_root(platform_key)
        source_path = self._ensure_within_root(root, root / request.source_relative_path)
        target_path = self._ensure_within_root(root, root / request.target_relative_path)
        if request.source_relative_path.replace("\\", "/").strip("/") in set(self._ROOT_DIRECTORIES):
            raise RuntimeError("不允许重命名平台基线根目录。")
        if not source_path.exists():
            raise FileNotFoundError("源路径不存在。")
        if target_path.exists():
            raise FileExistsError("目标路径已存在。")
        source_section = self._extract_section(request.source_relative_path)
        target_section = self._extract_section(request.target_relative_path)
        if source_section != target_section:
            raise RuntimeError("暂不支持跨 section 移动，请保持在同一区域内操作。")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_path), str(target_path))
        if target_path.is_dir():
            return PlatformBaselineEntry(
                name=target_path.name,
                relative_path=str(target_path.relative_to(root)).replace("\\", "/"),
                section=target_section,
                kind="directory",
            )
        return PlatformBaselineEntry(
            name=target_path.name,
            relative_path=str(target_path.relative_to(root)).replace("\\", "/"),
            section=target_section,
            kind="file",
            size=target_path.stat().st_size,
            media_type=mimetypes.guess_type(target_path.name)[0] or "application/octet-stream",
        )

    async def upload_skill(self, platform_key: str, *, upload_file: UploadFile) -> list[PlatformBaselineSkill]:
        root = self.ensure_platform_root(platform_key)
        target_root = root / "skills"
        filename = upload_file.filename or "uploaded-skill.md"
        raw_bytes = await upload_file.read()
        if not raw_bytes:
            raise RuntimeError("上传的技能文件为空。")

        suffix = Path(filename).suffix.lower()
        if suffix == ".zip":
            self._install_skill_zip(target_root, raw_bytes)
        else:
            self._install_single_skill_file(target_root, filename, raw_bytes)
        return self.list_skills(platform_key)

    def list_skills(self, platform_key: str) -> list[PlatformBaselineSkill]:
        root = self.ensure_platform_root(platform_key)
        items = skill_loader.load_directory(root / "skills", source="platform")
        skills: list[PlatformBaselineSkill] = []
        for item in items:
            path = Path(str(item["path"]))
            skills.append(
                PlatformBaselineSkill(
                    name=str(item["name"]),
                    description=str(item["description"]),
                    allowed_tools=list(item.get("allowed_tools", [])),
                    tags=list(item.get("tags", [])),
                    relative_path=str(path.relative_to(root)).replace("\\", "/"),
                )
            )
        return skills

    def delete_skill(self, platform_key: str, *, skill_name: str) -> None:
        root = self.ensure_platform_root(platform_key)
        skills_root = root / "skills"
        normalized = self._slugify(skill_name)
        candidates = [skills_root / normalized]
        for skill_dir in skills_root.iterdir() if skills_root.exists() else []:
            if skill_dir.is_dir() and self._slugify(skill_dir.name) == normalized:
                candidates.append(skill_dir)

        for candidate in candidates:
            if (candidate / "SKILL.md").exists():
                shutil.rmtree(candidate)
                return
        raise FileNotFoundError("目标技能不存在。")

    def materialize_to_session(self, platform_key: str, session: AgentSession) -> None:
        if session.workspace is None:
            raise RuntimeError("会话沙箱未初始化。")

        root = self.ensure_platform_root(platform_key)
        session_service.bind_baseline_root(session, root)
        self._sync_baseline_to_workspace(root, session)

        platform_files: list[dict[str, object]] = []
        for item in self.list_files(platform_key):
            section_root = {
                "input": session.workspace.input_dir,
                "skills": session.workspace.skills_dir,
                "work": session.workspace.work_dir,
                "output": session.workspace.output_dir,
                "logs": session.workspace.logs_dir,
            }[item.section]
            session_relative_path = Path(item.relative_path).relative_to(item.section)
            session_path = section_root / session_relative_path
            platform_files.append(
                FileRecord(
                    file_id=f"platform_{uuid.uuid4().hex}",
                    session_id=session.session_id,
                    name=item.name,
                    relative_path=str(session_path.relative_to(session.workspace.root)).replace("\\", "/"),
                    size=item.size,
                    media_type=item.media_type,
                    category="platform",
                ).model_dump(mode="json")
            )

        loaded_skills = skill_loader.load_directory(root / "skills", source="platform")
        for item in loaded_skills:
            item["base_dir"] = str(Path(str(item["path"])).parent)
        session_service.replace_platform_assets(session, files=platform_files, skills=loaded_skills)

    def _sync_baseline_to_workspace(self, baseline_root: Path, session: AgentSession) -> None:
        if session.workspace is None:
            return
        section_roots = {
            "input": session.workspace.input_dir,
            "skills": session.workspace.skills_dir,
            "work": session.workspace.work_dir,
            "output": session.workspace.output_dir,
            "logs": session.workspace.logs_dir,
        }
        for section, target_root in section_roots.items():
            source_root = baseline_root / section
            if not source_root.exists():
                continue
            for source_path in sorted(source_root.rglob("*")):
                relative = source_path.relative_to(source_root)
                target_path = target_root / relative
                if source_path.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    continue
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target_path)

    def _ensure_within_root(self, root: Path, target: Path) -> Path:
        resolved_root = root.resolve(strict=False)
        resolved_target = target.resolve(strict=False)
        if resolved_root not in resolved_target.parents and resolved_target != resolved_root:
            raise ValueError("目标路径超出平台基线环境范围。")
        return resolved_target

    def _extract_section(self, relative_path: str) -> Literal["input", "skills", "work", "output", "logs"]:
        normalized = relative_path.replace("\\", "/").strip("/")
        section = normalized.split("/", 1)[0]
        if section not in set(self._ROOT_DIRECTORIES):
            raise RuntimeError("平台基线路径必须位于 input、skills、work、output、logs 之一。")
        return section  # type: ignore[return-value]

    def _install_single_skill_file(self, target_root: Path, filename: str, raw_bytes: bytes) -> None:
        source_name = Path(filename).name
        if source_name.lower() == "skill.md":
            slug = self._slugify(Path(filename).parent.name or "uploaded-skill")
        else:
            slug = self._slugify(Path(filename).stem)

        skill_dir = target_root / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_bytes(raw_bytes)

    def _install_skill_zip(self, target_root: Path, raw_bytes: bytes) -> None:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if not members:
                raise RuntimeError("技能压缩包中没有可提取文件。")

            for member in members:
                member_path = Path(member.filename)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise RuntimeError(f"非法技能压缩包路径: {member.filename}")
                target_path = self._ensure_within_root(target_root, target_root / member_path)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src:
                    target_path.write_bytes(src.read())

        if not skill_loader.load_directory(target_root, source="platform"):
            raise RuntimeError("技能压缩包中未发现任何 skill-name/SKILL.md 结构。")

    def _slugify(self, value: str) -> str:
        return (
            "".join(char.lower() if char.isalnum() or char in {"-", "_"} else "-" for char in value.strip())
            .strip("-")
            or "custom-skill"
        )


platform_baseline_service = PlatformBaselineService()
