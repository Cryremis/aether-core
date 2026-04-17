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
    PlatformBaselineFile,
    PlatformBaselineSkill,
    PlatformBaselineSummary,
)
from app.services.session_service import AgentSession, session_service
from app.services.skill_loader import skill_loader


class PlatformBaselineService:
    """管理平台基线环境。"""

    def ensure_platform_root(self, platform_key: str) -> Path:
        root = settings.platform_baselines_root / platform_key.strip().lower()
        for section in ("input", "work", "skills"):
            (root / section).mkdir(parents=True, exist_ok=True)
        return root

    def list_summary(self, platform_key: str) -> PlatformBaselineSummary:
        return PlatformBaselineSummary(
            platform_key=platform_key,
            files=self.list_files(platform_key),
            skills=self.list_skills(platform_key),
        )

    async def upload_file(
        self,
        platform_key: str,
        *,
        upload_file: UploadFile,
        section: Literal["input", "work"] = "input",
    ) -> PlatformBaselineFile:
        root = self.ensure_platform_root(platform_key)
        safe_name = Path(upload_file.filename or f"file_{uuid.uuid4().hex}").name
        if not safe_name:
            raise RuntimeError("无效的文件名。")

        target_path = self._ensure_within_root(root, root / section / safe_name)
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
        if not target_path.exists() or not target_path.is_file():
            raise FileNotFoundError("目标基线文件不存在。")
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
        for section in ("input", "work"):
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

        self._sync_directory(root / "input", session.workspace.input_dir)
        self._sync_directory(root / "work", session.workspace.work_dir)
        platform_skill_root = session.workspace.skills_dir / "platform"
        platform_skill_root.mkdir(parents=True, exist_ok=True)
        self._sync_directory(root / "skills", platform_skill_root)

        platform_files: list[dict[str, object]] = []
        for item in self.list_files(platform_key):
            section_root = session.workspace.input_dir if item.section == "input" else session.workspace.work_dir
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

        loaded_skills = skill_loader.load_directory(platform_skill_root, source="platform")
        for item in loaded_skills:
            item["base_dir"] = str(Path(str(item["path"])).parent)
        session_service.replace_platform_assets(session, files=platform_files, skills=loaded_skills)

    def _sync_directory(self, source: Path, target: Path) -> None:
        if not source.exists():
            return
        for item in source.rglob("*"):
            if not item.is_file():
                continue
            relative_path = item.relative_to(source)
            destination = target / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)

    def _ensure_within_root(self, root: Path, target: Path) -> Path:
        resolved_root = root.resolve(strict=False)
        resolved_target = target.resolve(strict=False)
        if resolved_root not in resolved_target.parents and resolved_target != resolved_root:
            raise ValueError("目标路径超出平台基线环境范围。")
        return resolved_target

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
