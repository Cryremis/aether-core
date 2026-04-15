# backend/app/services/skill_loader.py
from __future__ import annotations

from pathlib import Path
from typing import Any


class SkillLoader:
    """从磁盘装载技能定义。"""

    def load_directory(self, root: Path, source: str) -> list[dict[str, Any]]:
        if not root.exists():
            return []

        skills: list[dict[str, Any]] = []
        for skill_file in sorted(root.glob("*/SKILL.md")):
            loaded = self.load_file(skill_file, source=source)
            if loaded:
                skills.append(loaded)
        return skills

    def load_file(self, path: Path, source: str) -> dict[str, Any] | None:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return None

        lines = text.splitlines()
        metadata: dict[str, Any] = {}
        body = text
        if len(lines) >= 3 and lines[1].strip() == "---":
            frontmatter_lines: list[str] = []
            end_index = 2
            for index in range(2, len(lines)):
                line = lines[index]
                if line.strip() == "---":
                    end_index = index
                    break
                frontmatter_lines.append(line)
            metadata = self._parse_frontmatter(frontmatter_lines)
            body = "\n".join(lines[end_index + 1 :]).strip()

        name = str(metadata.get("name") or path.parent.name)
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


skill_loader = SkillLoader()
