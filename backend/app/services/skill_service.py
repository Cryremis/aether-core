# backend/app/services/skill_service.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.schemas.agent import SkillCard
from app.services.session_service import session_service
from app.services.session_types import AgentSession
from app.services.skill_loader import skill_loader
from app.services.context.host_context import bounded_host_context


class SkillService:
    """管理内置技能、宿主技能与用户上传技能。"""

    def __init__(self) -> None:
        self._built_in_skills: list[dict[str, Any]] = []
        self._built_in_snapshot: tuple[tuple[str, int, int], ...] = ()

    def ensure_built_in_layout(self) -> None:
        self._built_in_root().mkdir(parents=True, exist_ok=True)
        self.reload_built_in_skills()

    def reload_built_in_skills(self) -> None:
        self._refresh_built_in_skills(force=True)

    def list_for_session(self, session: AgentSession) -> list[SkillCard]:
        return [self._to_card(item) for item in self._all_skill_definitions(session)]

    def build_system_prompt(self, session: AgentSession) -> str:
        context = bounded_host_context(session.host_context or {})
        skill_listing = self._format_skill_listing(session)
        sandbox_paths = self._sandbox_path_map()
        return (
            "你是 AetherCore 的生产级工作台 Agent。\n"
            "你运行在服务端受限沙箱中，必须优先通过工具读取文件、执行脚本、生成产物，并保持过程可追踪。\n"
            "要求：\n"
            "1. 默认使用中文，用户明确要求其他语言时遵循其要求。IP、URL、路径、命令与标识符必须保持原样。\n"
            "2. 处理文件前优先使用 list、read、glob、grep 等工具确认路径和内容，不要臆测。\n"
            "3. 如需运行命令，优先使用 sandbox_shell，并把最终可下载结果写入工作目录。\n"
            "4. 当宿主工具可直接提供更可靠的数据时，优先调用宿主工具。\n"
            "5. 最终回答必须简洁，说明你做了什么、产出了什么文件、还有什么风险。\n"
            "6. 技能不是普通提示词片段。若用户任务明显匹配某个技能，必须先调用 invoke_skill 加载该技能，再继续执行任务。\n"
            "7. 不要只提到某个技能而不调用 invoke_skill；技能加载后，严格遵循该技能中的工作流与约束。\n\n"
            f"## 宿主信息\n宿主名称: {session.host_name or 'AetherCore'}\n"
            f"宿主上下文: {context}\n\n"
            "## 沙箱路径约定\n"
            f"沙箱根目录: {sandbox_paths['root']}\n"
            f"技能目录: {sandbox_paths['skills']}\n"
            f"工作目录: {sandbox_paths['work']}\n"
            f"日志目录: {sandbox_paths['logs']}\n"
            "所有工具中的相对路径，都应当优先按沙箱根目录解析；用户上传文件和你生成的文件统一位于 `work/` 下。\n\n"
            "## 可用技能\n"
            f"{skill_listing}"
        )

    def build_core_system_prompt(self) -> str:
        return (
            "你是 AetherCore 的生产级工作台 Agent。\n"
            "你运行在服务端受限沙箱中，必须优先通过工具读取文件、执行脚本、生成产物，并保持过程可追踪。\n"
            "要求：\n"
            "1. 默认使用中文，用户明确要求其他语言时遵循其要求。IP、URL、路径、命令与标识符必须保持原样。\n"
            "2. 处理文件前优先使用 list、read、glob、grep 等工具确认路径和内容，不要臆测。\n"
            "3. 如需运行命令，优先使用 sandbox_shell，并把最终可下载结果写入工作目录。\n"
            "4. 当宿主工具可直接提供更可靠的数据时，优先调用宿主工具。\n"
            "5. 最终回答必须简洁，说明你做了什么、产出了什么文件、还有什么风险。\n"
            "6. 技能不是普通提示词片段。若用户任务明显匹配某个技能，必须先调用 invoke_skill 加载该技能，再继续执行任务。\n"
            "7. 不要只提到某个技能而不调用 invoke_skill；技能加载后，严格遵循该技能中的工作流与约束。"
        )

    def build_environment_prompt(self, session: AgentSession) -> str:
        context = bounded_host_context(session.host_context or {})
        skill_listing = self._format_skill_listing(session)
        sandbox_paths = self._sandbox_path_map()
        return (
            f"## 宿主信息\n宿主名称: {session.host_name or 'AetherCore'}\n"
            f"宿主上下文: {context}\n\n"
            "## 沙箱路径约定\n"
            f"沙箱根目录: {sandbox_paths['root']}\n"
            f"技能目录: {sandbox_paths['skills']}\n"
            f"工作目录: {sandbox_paths['work']}\n"
            f"日志目录: {sandbox_paths['logs']}\n"
            "所有工具中的相对路径，都应当优先按沙箱根目录解析；用户上传文件和你生成的文件统一位于 `work/` 下。\n\n"
            "## 可用技能\n"
            f"{skill_listing}"
        )

    def _sandbox_path_map(self) -> dict[str, str]:
        return {
            "root": settings.sandbox_docker_workspace_mount,
            "skills": settings.sandbox_docker_skills_dir,
            "work": settings.sandbox_docker_work_dir,
            "logs": settings.sandbox_docker_logs_dir,
        }

    def install_skill_upload(
        self,
        session: AgentSession,
        *,
        filename: str,
        raw_bytes: bytes,
        isolated: bool = False,
    ) -> list[SkillCard]:
        if session.workspace is None:
            raise RuntimeError("会话工作区未初始化。")
        if not raw_bytes:
            raise RuntimeError("上传的技能文件为空。")

        suffix = Path(filename).suffix.lower()
        target_root = self.session_skill_root(session) if isolated else session.workspace.skills_dir
        if suffix == ".zip":
            self._install_skill_zip(target_root, raw_bytes)
        else:
            self._install_single_skill_file(target_root, filename, raw_bytes)

        uploaded_skills = self._load_skills_from_disk(target_root, source="upload")
        session_service.replace_uploaded_skills(session, uploaded_skills)
        return [self._to_card(item) for item in uploaded_skills]

    def session_skill_root(self, session: AgentSession) -> Path:
        import hashlib
        key = hashlib.sha256(session.session_id.encode()).hexdigest()[:32]
        return session.workspace.skills_dir / ".sessions" / key

    def delete_uploaded_skill(self, session: AgentSession, skill_name: str) -> bool:
        if session.workspace is None:
            return False
        normalized = self._slugify(skill_name)
        isolated_root = self.session_skill_root(session)
        for item in self._load_skills_from_disk(isolated_root, source="upload"):
            if self._slugify(item["name"]) == normalized:
                import shutil
                shutil.rmtree(Path(item["path"]).parent)
                return True
        loaded_by_dir = {
            Path(item["path"]).parent.resolve(): self._slugify(str(item.get("name") or ""))
            for item in self._load_skills_from_disk(session.workspace.skills_dir, source="upload")
        }
        for directory in session.workspace.skills_dir.iterdir() if session.workspace.skills_dir.exists() else []:
            if directory.is_dir() and (self._slugify(directory.name) == normalized or loaded_by_dir.get(directory.resolve()) == normalized):
                import shutil
                shutil.rmtree(directory)
                uploaded = self._load_skills_from_disk(session.workspace.skills_dir, source="upload")
                session_service.replace_uploaded_skills(session, uploaded)
                return True
        return False

    def invoke_skill(self, session: AgentSession, skill_name: str) -> dict[str, Any]:
        skill = self.resolve_skill(session, skill_name)
        if skill is None:
            available = ", ".join(item["name"] for item in self._all_skill_definitions(session))
            raise RuntimeError(f"技能 {skill_name} 不存在。当前可用技能: {available}")

        # 用户/内置技能原件位于 API 主机目录，调用时把完整资源放入当前沙箱。
        if session.workspace and skill.get("path"):
            source = Path(skill["path"]).parent.resolve()
            if session.workspace.root.resolve() not in source.parents:
                import hashlib
                import shutil
                resources = sorted(source.rglob("*"))
                if any(path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) for path in resources):
                    raise RuntimeError("技能资源不能包含符号链接")
                signature = repr([(str(path.relative_to(source)), path.stat().st_mtime_ns, path.stat().st_size) for path in resources if path.is_file()])
                digest = hashlib.sha256((str(source) + signature).encode()).hexdigest()[:32]
                destination = session.workspace.skills_dir / ".available" / digest
                from app.sandbox.manager import sandbox_manager
                sandbox_manager.ensure_within_workspace(session.workspace, destination)
                shutil.copytree(source, destination, dirs_exist_ok=True)
                skill = dict(skill, path=str(destination / "SKILL.md"), base_dir=str(destination))
        final_content = self._render_skill_content(session, skill)
        return {
            "skill": {
                "name": skill["name"],
                "description": skill["description"],
                "source": skill["source"],
                "allowed_tools": skill.get("allowed_tools", []),
                "tags": skill.get("tags", []),
            },
            "injected_messages": [
                {
                    "role": "user",
                    "content": final_content,
                }
            ],
        }

    def resolve_skill(self, session: AgentSession, skill_name: str) -> dict[str, Any] | None:
        normalized = skill_name.strip().lower()
        for item in self._all_skill_definitions(session):
            candidates = {
                str(item.get("name", "")).strip().lower(),
                self._slugify(str(item.get("name", ""))),
            }
            if normalized in candidates:
                return item
        return None

    def _all_skill_definitions(self, session: AgentSession) -> list[dict[str, Any]]:
        self._refresh_built_in_skills()
        self._refresh_platform_skills(session)
        skills = [*self._built_in_skills, *session.host_skills, *session.platform_skills]
        try:
            from app.services.capability_service import capability_service
            skills.extend(capability_service.list_user_skills(
                user_id=getattr(session, "owner_user_id", None),
                platform_id=getattr(session, "platform_id", None),
                external_user_id=getattr(session, "external_user_id", None),
            ))
        except Exception:
            pass
        platform_skill_names = {self._slugify(s.get("name", "")) for s in session.platform_skills}
        if session.workspace is not None:
            workspace_skills = self._load_skills_from_disk(session.workspace.skills_dir, source="upload")
            workspace_skills.extend(self._load_skills_from_disk(self.session_skill_root(session), source="upload"))
            workspace_skills = [s for s in workspace_skills if self._slugify(s.get("name", "")) not in platform_skill_names]
            if workspace_skills != session.uploaded_skills:
                session.uploaded_skills = workspace_skills
            skills.extend(workspace_skills)
        else:
            skills.extend(session.uploaded_skills)
        deduped: dict[str, dict[str, Any]] = {}
        disabled = set(session.disabled_capability_ids)
        for item in skills:
            materialized = self._ensure_materialized(session, item)
            key = self._slugify(str(materialized.get("name", "")))
            if key in disabled or f"skill:{key}" in disabled:
                continue
            existing = deduped.get(key)
            if existing and existing.get("source") == "platform":
                continue
            deduped[key] = materialized
        return list(deduped.values())

    def _refresh_built_in_skills(self, *, force: bool = False) -> None:
        built_in_root = self._built_in_root()
        snapshot = self._directory_snapshot(built_in_root)
        if not force and snapshot == self._built_in_snapshot:
            return
        self._built_in_skills = self._load_skills_from_disk(
            built_in_root,
            source="built_in",
        )
        self._built_in_snapshot = snapshot

    def _refresh_platform_skills(self, session: AgentSession) -> None:
        if not session.baseline_root:
            return
        latest_platform_skills = self._load_skills_from_disk(Path(session.baseline_root) / "skills", source="platform")
        if latest_platform_skills == session.platform_skills:
            return
        session_service.replace_platform_assets(
            session,
            files=session.platform_files,
            skills=latest_platform_skills,
        )

    def _load_skills_from_disk(self, root: Path, source: str) -> list[dict[str, Any]]:
        loaded = skill_loader.load_directory(root, source=source)
        for item in loaded:
            item["base_dir"] = str(Path(item["path"]).parent)
        return loaded

    def _directory_snapshot(self, root: Path) -> tuple[tuple[str, int, int], ...]:
        if not root.exists():
            return ()
        snapshot: list[tuple[str, int, int]] = []
        for skill_file in sorted(root.glob("*/SKILL.md")):
            stat = skill_file.stat()
            snapshot.append((str(skill_file.resolve(strict=False)), stat.st_mtime_ns, stat.st_size))
        return tuple(snapshot)

    def _built_in_root(self) -> Path:
        configured_root = settings.built_in_skills_dir
        if configured_root.is_absolute():
            return configured_root

        if configured_root.parts and configured_root.parts[0] == settings.storage_root.name:
            relative_parts = configured_root.parts[1:]
            if relative_parts:
                return settings.resolved_storage_root.joinpath(*relative_parts)
            return settings.resolved_storage_root

        storage_root = settings.storage_root
        if not storage_root.is_absolute():
            storage_parts = storage_root.parts
            if configured_root.parts[: len(storage_parts)] == storage_parts:
                return settings.resolved_storage_root / configured_root.relative_to(storage_root)

        return settings.backend_root / configured_root

    def _ensure_materialized(self, session: AgentSession, item: dict[str, Any]) -> dict[str, Any]:
        source = item.get("source", "")
        if source in ("platform", "built_in"):
            return item
        if item.get("path") or session.workspace is None or not item.get("content"):
            return item

        slug = self._slugify(str(item["name"]))
        materialized_dir = session.workspace.skills_dir / slug
        materialized_dir.mkdir(parents=True, exist_ok=True)
        skill_path = materialized_dir / "SKILL.md"

        frontmatter_lines = [
            "---",
            f"name: {item['name']}",
            f"description: {item['description']}",
            "allowed_tools:",
        ]
        for tool_name in item.get("allowed_tools", []):
            frontmatter_lines.append(f"  - {tool_name}")
        frontmatter_lines.append("tags:")
        for tag in item.get("tags", []):
            frontmatter_lines.append(f"  - {tag}")
        frontmatter_lines.append("---")
        skill_path.write_text(
            "\n".join(frontmatter_lines) + f"\n\n{str(item['content']).strip()}\n",
            encoding="utf-8",
        )

        item["path"] = str(skill_path)
        item["base_dir"] = str(materialized_dir)
        return item

    def _render_skill_content(self, session: AgentSession, skill: dict[str, Any]) -> str:
        base_dir = str(
            skill.get("base_dir")
            or (Path(skill["path"]).parent if skill.get("path") else session.workspace.skills_dir if session.workspace else "")
        )
        normalized_dir = base_dir.replace("\\", "/")
        if session.workspace and settings.sandbox_executor == "docker":
            try:
                relative = Path(base_dir).resolve().relative_to(session.workspace.root.resolve())
                normalized_dir = settings.sandbox_docker_workspace_mount.rstrip("/") + "/" + relative.as_posix()
            except ValueError:
                pass
        body = str(skill.get("content", "")).strip()
        body = body.replace("${AETHER_SKILL_DIR}", normalized_dir)
        body = body.replace("${AETHER_WORKSPACE_ID}", session.workspace_id)
        return (
            f"<aether_skill name=\"{skill['name']}\" source=\"{skill['source']}\">\n"
            f"Base directory for this skill: {normalized_dir}\n\n"
            f"{body}\n"
            "</aether_skill>"
        )

    def _format_skill_listing(self, session: AgentSession) -> str:
        items = self._all_skill_definitions(session)
        if not items:
            return "暂无额外技能。"
        lines = []
        for item in items:
            lines.append(
                f"- {item['name']}: {item['description']} "
                f"(来源: {item['source']}, 标签: {', '.join(item.get('tags', [])) or '无'})"
            )
        return "\n".join(lines)

    def _install_single_skill_file(self, target_root: Path, filename: str, raw_bytes: bytes) -> None:
        from app.services.skill_package import install_package
        install_package(target_root, filename, raw_bytes)

    def _install_skill_zip(self, target_root: Path, raw_bytes: bytes) -> None:
        from app.services.skill_package import install_package
        install_package(target_root, "skills.zip", raw_bytes)

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
