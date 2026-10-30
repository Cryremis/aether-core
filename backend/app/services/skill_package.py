"""技能上传、草稿及市场共用的包校验与安装入口。"""
from __future__ import annotations

import io
import shutil
import stat
import tempfile
import threading
import zipfile
from pathlib import Path, PurePosixPath

from app.services.skill_loader import skill_loader, SkillValidationError

_lock = threading.RLock()


def unpack_package(root: Path, filename: str, raw: bytes) -> list[Path]:
    if not raw or len(raw) > 20 * 1024 * 1024:
        raise SkillValidationError("技能文件必须介于 1 字节和 20 MiB 之间")
    if filename.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = [m for m in archive.infolist() if not m.is_dir()]
                if not members or len(members) > 1000 or sum(m.file_size for m in members) > 50 * 1024 * 1024:
                    raise SkillValidationError("技能包必须有文件，且不超过 1000 个文件或 50 MiB")
                for member in members:
                    path = PurePosixPath(member.filename.replace("\\", "/"))
                    if path.is_absolute() or ".." in path.parts or ":" in str(path) or stat.S_ISLNK(member.external_attr >> 16):
                        raise SkillValidationError("非法技能包路径或符号链接")
                    target = root.joinpath(*path.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(member))
        except zipfile.BadZipFile as exc:
            raise SkillValidationError("无效的 zip 技能包") from exc
    else:
        loaded = skill_loader.load_bytes(raw, "upload", path_label=filename)
        if not loaded:
            raise SkillValidationError("技能文件为空")
        import re
        name = re.sub(r"[^a-zA-Z0-9_-]+", "-", loaded["name"]).strip("-") or "uploaded-skill"
        (root / name).mkdir()
        (root / name / "SKILL.md").write_bytes(raw)
    discovered = skill_loader.discover_uploaded_skills(root, source="upload")
    names = [item.definition["name"] for item in discovered]
    if len(names) != len(set(names)):
        raise SkillValidationError("技能包包含重名技能")
    return [item.directory for item in discovered]


def install_package(target: Path, filename: str, raw: bytes) -> None:
    with _lock, tempfile.TemporaryDirectory(prefix="aether-skill-") as directory:
        staging = Path(directory) / "staging"
        staging.mkdir()
        skills = unpack_package(staging, filename, raw)
        target.mkdir(parents=True, exist_ok=True)
        backups = Path(directory) / "backups"
        backups.mkdir()
        installed: list[Path] = []
        moved: list[tuple[Path, Path]] = []
        try:
            for index, source in enumerate(skills):
                destination = target / source.name
                if destination.exists():
                    backup = backups / str(index)
                    shutil.move(str(destination), str(backup))
                    moved.append((backup, destination))
                installed.append(destination)
                shutil.copytree(source, destination)
        except Exception:
            for destination in installed:
                shutil.rmtree(destination, ignore_errors=True)
            for backup, destination in moved:
                shutil.move(str(backup), str(destination))
            raise
