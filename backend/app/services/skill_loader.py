# backend/app/services/skill_loader.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SkillValidationError(RuntimeError):
    """技能包格式或内容校验失败。"""


@dataclass(slots=True)
class DiscoveredSkill:
    directory: Path
    relative_dir: Path
    definition: dict[str, Any]


class SkillLoader:
    """从磁盘装载技能定义。"""

    def load_directory(self, root: Path, source: str, *, strict: bool = False) -> list[dict[str, Any]]:
        if not root.exists():
            return []

        skills: list[dict[str, Any]] = []
        for skill_file in sorted(root.glob("*/SKILL.md")):
            try:
                loaded = self.load_file(skill_file, source=source)
            except SkillValidationError:
                if strict:
                    raise
                continue
            if loaded:
                skills.append(loaded)
        return skills

    def load_file(
        self,
        path: Path,
        source: str,
        *,
        fallback_name: str | None = None,
    ) -> dict[str, Any] | None:
        relative_label = str(path.name if path.parent == path.parent.parent else path.parent.name + "/SKILL.md")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SkillValidationError(
                f"技能文件 {relative_label} 不是 UTF-8 文本，请确认它是纯文本 Markdown，而不是压缩包或二进制文件。"
            ) from exc
        return self._load_text(text, path=path, source=source, fallback_name=fallback_name)

    def load_bytes(
        self,
        raw_bytes: bytes,
        source: str,
        *,
        path_label: str,
        fallback_name: str | None = None,
    ) -> dict[str, Any] | None:
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillValidationError(
                f"技能文件 {path_label} 不是 UTF-8 文本，请确认它是纯文本 Markdown，而不是压缩包或二进制文件。"
            ) from exc
        return self._load_text(text, path=Path(path_label), source=source, fallback_name=fallback_name)

    def discover_uploaded_skills(
        self,
        root: Path,
        source: str,
        *,
        fallback_name: str | None = None,
    ) -> list[DiscoveredSkill]:
        files = [path for path in sorted(root.rglob("*")) if path.is_file() and not self._is_ignored_artifact(path)]
        skill_files = [path for path in files if path.name == "SKILL.md"]
        if not skill_files:
            raise SkillValidationError(
                "技能包中未找到任何 SKILL.md。请提供 `skill-name/SKILL.md`，或把完整技能目录打成 zip 再上传。"
            )

        skill_dirs = {path.parent.resolve(strict=False) for path in skill_files}
        discovered: list[DiscoveredSkill] = []

        for file_path in files:
            if self._find_owner_skill_dir(file_path, root=root, skill_dirs=skill_dirs) is None:
                relative_path = self._relative_label(file_path, root)
                raise SkillValidationError(
                    f"技能包中存在未归属到技能目录的文件 `{relative_path}`。所有文件都必须位于包含 `SKILL.md` 的技能目录内。"
                )

        for skill_dir in sorted(skill_dirs):
            skill_path = skill_dir / "SKILL.md"
            label = self._relative_label(skill_path, root)
            loaded = self.load_file(
                skill_path,
                source=source,
                fallback_name=fallback_name if skill_dir == root.resolve(strict=False) else skill_dir.name,
            )
            if loaded is None:
                raise SkillValidationError(f"技能文件 {label} 为空，请补充技能说明后再上传。")
            relative_dir = Path(self._relative_label(skill_dir, root))
            discovered.append(DiscoveredSkill(directory=skill_dir, relative_dir=relative_dir, definition=loaded))
        return discovered

    def _load_text(
        self,
        text: str,
        *,
        path: Path,
        source: str,
        fallback_name: str | None = None,
    ) -> dict[str, Any] | None:
        if not text.strip():
            return None

        lines = text.splitlines()
        metadata: dict[str, Any] = {}
        body = text
        frontmatter_start: int | None = None
        if len(lines) >= 3 and lines[0].strip() == "---":
            frontmatter_start = 0
        elif len(lines) >= 4 and lines[1].strip() == "---":
            frontmatter_start = 1

        if frontmatter_start is not None:
            frontmatter_lines: list[str] = []
            end_index = frontmatter_start + 1
            for index in range(frontmatter_start + 1, len(lines)):
                line = lines[index]
                if line.strip() == "---":
                    end_index = index
                    break
                frontmatter_lines.append(line)
            metadata = self._parse_frontmatter(frontmatter_lines)
            body = "\n".join(lines[end_index + 1 :]).strip()

        name = str(metadata.get("name") or fallback_name or path.parent.name)
        description = str(metadata.get("description") or f"{name} 技能")
        allowed_tools = metadata.get("allowed_tools") or []
        tags = metadata.get("tags") or [source]
        return {
            "name": name,
            "description": description,
            "content": body,
            "allowed_tools": allowed_tools,
            "tags": tags,
            "source": source,
            "path": str(path),
        }

    def _parse_frontmatter(self, lines: list[str]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        current_list_key: str | None = None
        for raw_line in lines:
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("- ") and current_list_key:
                metadata.setdefault(current_list_key, []).append(stripped[2:].strip())
                continue
            current_list_key = None
            if ":" not in stripped:
                continue
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value:
                metadata[key] = value
            else:
                metadata[key] = []
                current_list_key = key
        return metadata

    def _find_owner_skill_dir(self, path: Path, *, root: Path, skill_dirs: set[Path]) -> Path | None:
        resolved_root = root.resolve(strict=False)
        current = path.parent.resolve(strict=False)
        while current == resolved_root or resolved_root in current.parents:
            if current in skill_dirs:
                return current
            if current == resolved_root:
                break
            current = current.parent
        return None

    def _relative_label(self, path: Path, root: Path) -> str:
        try:
            return str(path.resolve(strict=False).relative_to(root.resolve(strict=False))).replace("\\", "/")
        except ValueError:
            return path.name

    def _is_ignored_artifact(self, path: Path) -> bool:
        ignored_names = {"__MACOSX", ".DS_Store"}
        return any(part in ignored_names for part in path.parts)


skill_loader = SkillLoader()
