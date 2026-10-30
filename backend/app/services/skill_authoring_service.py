"""Agent 技能草稿、冻结快照与平台市场操作。身份仅来自当前会话。"""
from __future__ import annotations

import hashlib
import io
import re
import tempfile
import uuid
import zipfile
from pathlib import Path

from app.core.config import settings
from app.services.artifact_service import artifact_service
from app.services.capability_service import capability_service
from app.services.extension_store_service import extension_store_service
from app.services.skill_package import unpack_package
from app.services.skill_service import skill_service


def identity(session):
    if session.owner_user_id is not None:
        return {"user_id": session.owner_user_id}
    if session.platform_id is not None and session.external_user_id:
        return {"platform_id": session.platform_id, "external_user_id": session.external_user_id}
    raise PermissionError("需要用户身份")


def author_key(session):
    principal = identity(session)
    return "user:" + str(principal["user_id"]) if "user_id" in principal else f"embed:{session.platform_id}:{session.external_user_id}"


class SkillAuthoringService:
    def _draft(self, session, draft_id):
        if not re.fullmatch(r"[a-f0-9]{32}", draft_id):
            raise ValueError("草稿 ID 无效")
        session_key = hashlib.sha256(session.session_id.encode()).hexdigest()[:16]
        path = session.workspace.work_dir / ".skill-drafts" / session_key / draft_id
        if session.workspace.work_dir.resolve() not in path.resolve().parents:
            raise ValueError("草稿路径越界")
        return path

    def _snapshot(self, session, snapshot_id):
        if not re.fullmatch(r"[a-f0-9]{64}", snapshot_id):
            raise ValueError("快照 ID 无效")
        owner = hashlib.sha256(f"{author_key(session)}:{session.session_id}".encode()).hexdigest()
        return settings.resolved_storage_root / "skill_snapshots" / owner / f"{snapshot_id}.zip"

    def execute(self, session, tool, args):
        identity(session)
        if session.workspace is None:
            raise ValueError("会话工作区未初始化")
        if tool == "skill_init":
            import yaml
            name = args["name"]
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
                raise ValueError("名称须为小写字母、数字和短横线，最长 64 字符")
            draft_id = uuid.uuid4().hex
            root = self._draft(session, draft_id) / name
            root.mkdir(parents=True)
            source_info = {}
            if args.get("source_entry_id"):
                import shutil
                from app.services.skill_loader import skill_loader
                item = extension_store_service.inspect(args["source_entry_id"], session.platform_id, args.get("source_version"))
                if item["kind"] != "skill":
                    raise ValueError("仅支持复制 Skill")
                with tempfile.TemporaryDirectory() as directory:
                    skills = unpack_package(Path(directory), Path(item["artifact_path"]).name, Path(item["artifact_path"]).read_bytes())
                    if len(skills) != 1:
                        raise ValueError("编辑草稿一次仅支持一个技能")
                    shutil.copytree(skills[0], root, dirs_exist_ok=True)
                    args = dict(args, content=skill_loader.load_file(root / "SKILL.md", "draft")["content"])
                source_info = {"source_entry_id": item["entry_id"], "base_version": item["version"], "expected_revision": item["revision"]}
            metadata = yaml.safe_dump({"name": name, "description": args["description"]}, allow_unicode=True)
            (root / "SKILL.md").write_text(f"---\n{metadata}---\n\n{args.get('content', '')}\n", encoding="utf-8")
            return {"draft_id": draft_id, "path": (root / "SKILL.md").relative_to(session.workspace.root).as_posix(), **source_info}
        if tool == "skill_validate":
            root = self._draft(session, args["draft_id"])
            if not root.is_dir():
                raise FileNotFoundError("草稿不存在")
            files = sorted(root.rglob("*"))
            if len(files) > 1100:
                raise ValueError("草稿文件过多")
            buffer = io.BytesIO()
            size = 0
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for file in files:
                    if file.is_symlink() or (hasattr(file, "is_junction") and file.is_junction()) or root.resolve() not in file.resolve().parents:
                        raise ValueError("草稿不能包含链接或越界资源")
                    if file.is_file():
                        size += file.stat().st_size
                        if size > 50 * 1024 * 1024:
                            raise ValueError("草稿超过 50 MiB")
                        archive.write(file, file.relative_to(root).as_posix())
            raw = buffer.getvalue()
            with tempfile.TemporaryDirectory() as directory:
                unpack_package(Path(directory), "skills.zip", raw)
            snapshot_id = hashlib.sha256(raw).hexdigest()
            path = self._snapshot(session, snapshot_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            return {"snapshot_id": snapshot_id, "format_valid": True, "behavior_tested": False, "size": len(raw)}
        if tool in {"skill_market_search", "skill_market_inspect", "skill_publish"} and session.platform_id is None:
            raise PermissionError("需要当前宿主平台")
        if tool == "skill_market_search":
            query = args.get("query", "").casefold()
            items = [item for item in extension_store_service.list_entries(session.platform_id) if item["kind"] == "skill" and query in (item["name"] + " " + item["description"]).casefold()]
            return {"items": items[max(0, args.get("offset", 0)):][:min(20, max(1, args.get("limit", 10)))], "total": len(items)}
        if tool == "skill_market_inspect":
            item = extension_store_service.inspect(args["entry_id"], session.platform_id, args.get("version"))
            path = Path(item.pop("artifact_path"))
            raw = path.read_bytes()
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                unpack_package(root, path.name, raw)
                files = [file for file in sorted(root.rglob("*")) if file.is_file()]
                item["files"] = [file.relative_to(root).as_posix() for file in files][:100]
                requested = args.get("file_path")
                if requested:
                    file = (root / requested).resolve()
                    if root.resolve() not in file.parents:
                        raise ValueError("资源路径越界")
                    item["content"] = file.read_bytes()[:16000].decode("utf-8", errors="replace")
            return item
        if tool == "skill_install" and args.get("entry_id"):
            if not session.platform_id:
                raise PermissionError("需要宿主平台")
            if args.get("snapshot_id"):
                raise ValueError("entry_id 和 snapshot_id 不能同时提供")
            item = extension_store_service.inspect(args["entry_id"], session.platform_id, args.get("version"))
            if item["kind"] != "skill":
                raise ValueError("该工具仅安装 Skill，不安装 MCP")
            return extension_store_service.install(args["entry_id"], platform_id=session.platform_id, version=args.get("version"), scope=args.get("scope", "session"), session=session, identity=identity(session))
        if tool == "skill_uninstall":
            if args.get("scope", "session") == "user":
                removed = capability_service.delete_skill(args["name"], **identity(session))
            else:
                removed = skill_service.delete_uploaded_skill(session, args["name"])
            return {"removed": removed}
        if tool in {"skill_set_maintenance", "skill_set_status"}:
            return extension_store_service.manage(args["entry_id"], session.platform_id, author_key(session), args["expected_revision"], maintenance_policy=args.get("maintenance_policy"), status=args.get("status"))
        if tool == "skill_export" and args.get("entry_id"):
            item = extension_store_service.inspect(args["entry_id"], session.platform_id, args.get("version"))
            if item["kind"] != "skill":
                raise ValueError("仅支持导出 Skill")
            path = Path(item["artifact_path"])
            raw = path.read_bytes()
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                unpack_package(root, path.name, raw)
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                    for file in root.rglob("*"):
                        if file.is_file():
                            archive.write(file, file.relative_to(root).as_posix())
                raw = buffer.getvalue()
        else:
            raw = self._snapshot(session, args["snapshot_id"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != args["snapshot_id"]:
                raise ValueError("快照内容校验失败")
        if tool == "skill_install":
            if args.get("scope", "session") == "user":
                capability_service.install_skill(raw_bytes=raw, filename="skills.zip", **identity(session))
            else:
                skill_service.install_skill_upload(session, raw_bytes=raw, filename="skills.zip", isolated=True)
            return {"installed": True, "scope": args.get("scope", "session"), "snapshot_id": args["snapshot_id"]}
        if tool == "skill_export":
            path = session.workspace.work_dir / f"skill-{hashlib.sha256(raw).hexdigest()[:12]}.zip"
            path.write_bytes(raw)
            artifact_service.sync_work_directory(session)
            return {"artifact": next(item for item in session.artifacts if item["relative_path"] == f"work/{path.name}")}
        if tool == "skill_publish":
            return extension_store_service.publish(kind="skill", name=args["name"], description=args["description"], version=args.get("version", "1.0.0"), submitter_user_id=session.owner_user_id, raw_bytes=raw, filename="skills.zip", platform_id=session.platform_id, author_key=author_key(session), maintenance_policy=args.get("maintenance_policy"), entry_id=args.get("entry_id"), expected_revision=args.get("expected_revision"), base_version=args.get("base_version"), idempotency_key=args.get("idempotency_key"))
        raise ValueError("未知技能操作")


skill_authoring_service = SkillAuthoringService()
