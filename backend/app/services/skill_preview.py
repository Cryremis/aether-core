"""按需读取技能包的文件清单与有限长度的文本。"""
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from app.services.skill_package import package_members


PREVIEW_BYTES = 128 * 1024


def _content(path: str, raw: bytes) -> dict:
    truncated = len(raw) > PREVIEW_BYTES
    try:
        if b"\0" in raw:
            raise ValueError("binary")
        text = raw[:PREVIEW_BYTES].decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        text = raw[:exc.start].decode("utf-8-sig") if truncated and exc.reason == "unexpected end of data" else None
    except ValueError:
        text = None
    return {"path": path, "text": text, "truncated": truncated}


def _preview(files: dict[str, int], read, file_path: str | None) -> dict:
    if file_path is not None:
        if file_path not in files:
            raise FileNotFoundError("技能包中没有该文件")
        return {"file": _content(file_path, read(file_path))}
    entries = [path for path in sorted(files) if PurePosixPath(path).name == "SKILL.md"]
    if not entries:
        raise FileNotFoundError("技能包缺少 SKILL.md")
    entry = "SKILL.md" if "SKILL.md" in files else entries[0]
    return {"files": [{"path": path, "size": size} for path, size in sorted(files.items())],
            "file": _content(entry, read(entry))}


def preview_artifact(artifact: Path, file_path: str | None) -> dict:
    if artifact.suffix.lower() != ".zip":
        def read(_path):
            with artifact.open("rb") as stream:
                return stream.read(PREVIEW_BYTES + 1)
        return _preview({"SKILL.md": artifact.stat().st_size}, read, file_path)
    with ZipFile(artifact) as archive:
        members = {path: member for path, member in package_members(archive).items()
                   if not {"__MACOSX", ".DS_Store"}.intersection(PurePosixPath(path).parts)}

        def read(path):
            with archive.open(members[path]) as stream:
                return stream.read(PREVIEW_BYTES + 1)

        return _preview({path: member.file_size for path, member in members.items()}, read, file_path)


def preview_installed(skill: dict, file_path: str | None) -> dict:
    if not skill.get("path"):
        raw = skill.get("content", "").encode("utf-8")
        return _preview({"SKILL.md": len(raw)}, lambda _: raw[:PREVIEW_BYTES + 1], file_path)
    skill_file = Path(skill["path"])
    root = skill_file.parent.resolve()
    if skill_file.resolve() != skill_file.parent.parent.resolve() / skill_file.parent.name / skill_file.name:
        raise FileNotFoundError("技能入口不可预览")

    def read(path):
        target = root / path
        if root not in target.resolve().parents or target.is_symlink():
            raise FileNotFoundError("技能包中没有该文件")
        with target.open("rb") as stream:
            return stream.read(PREVIEW_BYTES + 1)

    if file_path is not None:
        relative = PurePosixPath(file_path)
        if relative.is_absolute() or ".." in relative.parts or ":" in file_path or "\\" in file_path:
            raise FileNotFoundError("技能包中没有该文件")
        target = root / relative
        if not target.is_file():
            raise FileNotFoundError("技能包中没有该文件")
        return {"file": _content(file_path, read(file_path))}
    files = {}
    for target in root.rglob("*"):
        if target.is_file() and not target.is_symlink() and root in target.resolve().parents:
            files[target.relative_to(root).as_posix()] = target.stat().st_size
            if len(files) > 1000:
                raise ValueError("技能文件数量超过预览上限")
    return _preview(files, read, None)
