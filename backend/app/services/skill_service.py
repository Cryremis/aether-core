# backend/app/services/skill_service.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.schemas.agent import SkillCard
from app.services.session_service import AgentSession, session_service
from app.services.skill_loader import skill_loader


class SkillService:
    """管理内置技能、宿主技能与用户上传技能。"""

    def __init__(self) -> None:
        self._built_in_skills: list[dict[str, Any]] = []

    def ensure_built_in_layout(self) -> None:
        settings.built_in_skills_dir.mkdir(parents=True, exist_ok=True)
        self.reload_built_in_skills()

    def reload_built_in_skills(self) -> None:
        self._built_in_skills = skill_loader.load_directory(settings.built_in_skills_dir, source="built_in")

    def list_for_session(self, session: AgentSession) -> list[SkillCard]:
        return [self._to_card(item) for item in self._all_skill_definitions(session)]

    def build_system_prompt(self, session: AgentSession) -> str:
        context = session.host_context or {}
        skill_sections: list[str] = []
        for item in self._all_skill_definitions(session):
            if item.get("content"):
                skill_sections.append(
                    f"## 技能: {item['name']}\n来源: {item['source']}\n说明: {item['description']}\n\n{item['content']}"
                )

        return (
            "你是 AetherCore 的生产级工作台 Agent。\n"
            "你运行在服务器侧受限沙箱中，必须优先通过工具读取文件、执行脚本、生成产物，并保持过程可追踪。\n"
            "要求：\n"
            "1. 始终使用中文回答。\n"
            "2. 处理文件前先调用工具确认文件列表或读取内容，不要臆测。\n"
            "3. 如需运行命令，优先使用 sandbox_shell，并把最终可下载结果写入输出目录。\n"
            "4. 当宿主工具可直接提供更可靠的数据时，优先调用宿主工具。\n"
            "5. 最终回答必须简洁，说明你做了什么、产出了什么文件、还有什么风险。\n\n"
            f"## 宿主信息\n宿主名称: {session.host_name or 'standalone-workbench'}\n"
            f"宿主类型: {session.host_type}\n"
            f"宿主上下文: {context}\n\n"
            f"## 沙箱路径约定\n输入目录: {session.workspace.input_dir if session.workspace else ''}\n"
            f"技能目录: {session.workspace.skills_dir if session.workspace else ''}\n"
            f"工作目录: {session.workspace.work_dir if session.workspace else ''}\n"
            f"输出目录: {session.workspace.output_dir if session.workspace else ''}\n\n"
            "## 可用技能\n"
            + ("\n\n".join(skill_sections) if skill_sections else "暂无额外技能。")
        )

    def install_skill_from_text(
        self,
        session: AgentSession,
        name: str,
        description: str,
        content: str,
        allowed_tools: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> SkillCard:
        if session.workspace is None:
            raise RuntimeError("会话工作区未初始化。")

        slug = self._slugify(name)
        skill_dir = session.workspace.skills_dir / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"

        frontmatter_lines = [
            "---",
            f"name: {name}",
            f"description: {description}",
            "allowed_tools:",
        ]
        for tool_name in allowed_tools or []:
            frontmatter_lines.append(f"  - {tool_name}")
        frontmatter_lines.append("tags:")
        for tag in tags or ["upload"]:
            frontmatter_lines.append(f"  - {tag}")
        frontmatter_lines.append("---")
        payload = "\n".join(frontmatter_lines) + f"\n\n{content.strip()}\n"
        skill_path.write_text(payload, encoding="utf-8")

        loaded = skill_loader.load_file(skill_path, source="upload")
        if loaded is None:
            raise RuntimeError("技能文件解析失败。")
        session_service.save_uploaded_skill(session, loaded)
        return self._to_card(loaded)

    def _all_skill_definitions(self, session: AgentSession) -> list[dict[str, Any]]:
        return [*self._built_in_skills, *session.host_skills, *session.uploaded_skills]

    def _to_card(self, item: dict[str, Any]) -> SkillCard:
        return SkillCard(
            name=item["name"],
            description=item["description"],
            source=item["source"],
            allowed_tools=item.get("allowed_tools", []),
            tags=item.get("tags", []),
        )

    def _slugify(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
        return slug or "custom-skill"


skill_service = SkillService()
