from __future__ import annotations

import hashlib
import json
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.capability_service import capability_service
from app.services.store import store_service, utcnow_iso


class ExtensionStoreService:
    """拓展商店元数据、不可变版本和安装统计。"""

    def list_entries(self, platform_id: int | None = None) -> list[dict[str, Any]]:
        with store_service._connect() as conn:
            rows = conn.execute(
                "SELECT e.*, COALESCE(u.full_name, u.username, e.author_key, 'Unknown') AS submitter_name "
                "FROM extension_entries e LEFT JOIN users u ON u.user_id=e.submitter_user_id "
                "WHERE e.platform_id IS ? AND e.status='active' ORDER BY e.updated_at DESC",
                (platform_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def publish(self, *, kind: str, name: str, description: str, version: str, submitter_user_id: int | None, raw_bytes: bytes, filename: str, manifest: dict[str, Any] | None = None, platform_id: int | None = None, author_key: str | None = None, maintenance_policy: str | None = None, entry_id: str | None = None, expected_revision: int | None = None, base_version: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        author_key = author_key or f"user:{submitter_user_id}"
        if entry_id is not None and not re.fullmatch(r"ext_[a-f0-9]{32}", entry_id):
            raise ValueError("拓展 ID 无效")
        if maintenance_policy is not None and maintenance_policy not in {"creator_only", "platform_members"}:
            raise ValueError("维护方式无效")
        if kind not in {"skill", "mcp"}:
            raise ValueError("拓展类型必须是 skill 或 mcp")
        if not raw_bytes or len(raw_bytes) > 20 * 1024 * 1024:
            raise ValueError("拓展文件必须介于 1 字节和 20 MiB 之间")
        name = name.strip()
        if len(description) > 2000:
            raise ValueError("介绍不能超过 2000 字符")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
            raise ValueError("拓展名称只能包含字母、数字、下划线、点和短横线")
        version = version.strip() or "1.0.0"
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version):
            raise ValueError("版本号必须符合 SemVer，例如 1.0.0")
        raw_bytes, validated_manifest = self._validate_artifact(kind, raw_bytes, filename)
        manifest = {**validated_manifest, **(manifest or {})}
        entry_id = entry_id or f"ext_{uuid.uuid5(uuid.NAMESPACE_URL, f'{platform_id}:{author_key}:{kind}:{name}').hex}"
        root = settings.resolved_storage_root / "extensions" / entry_id / version
        now = utcnow_iso()
        with store_service._connect() as conn:
            # 在写事务中重新检查维护权限和基础版本，避免共同维护时丢失更新。
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM extension_entries WHERE entry_id=?", (entry_id,)).fetchone()
            maintenance_policy = maintenance_policy or (existing["maintenance_policy"] if existing else "creator_only")
            if existing:
                if existing["platform_id"] != platform_id or existing["kind"] != kind:
                    raise PermissionError("无权访问该拓展")
                if existing["author_key"] != author_key and existing["maintenance_policy"] != "platform_members":
                    raise PermissionError("仅创作者可维护该技能")
                if existing["status"] != "active":
                    raise ValueError("条目已下架，请先恢复")
                if author_key != existing["author_key"]:
                    maintenance_policy = existing["maintenance_policy"]
                previous = conn.execute("SELECT manifest_json FROM extension_versions WHERE entry_id=? AND version=?", (entry_id, version)).fetchone()
                if previous and idempotency_key:
                    saved = json.loads(previous["manifest_json"])
                    fingerprint = hashlib.sha256(raw_bytes + json.dumps([name, description, maintenance_policy, author_key, base_version, expected_revision], ensure_ascii=False).encode()).hexdigest()
                    if saved.get("idempotency_key") == idempotency_key and saved.get("request_fingerprint") == fingerprint:
                        return dict(existing, published_version=version, idempotent=True)
                if expected_revision != existing["revision"] or base_version != existing["current_version"]:
                    raise ValueError("VERSION_CONFLICT: 请读取最新版本后合并修改")
            if conn.execute("SELECT 1 FROM extension_versions WHERE entry_id=? AND version=?", (entry_id, version)).fetchone():
                raise ValueError("该版本已存在")
            root.mkdir(parents=True, exist_ok=True)
            artifact = root / Path(filename).name
            artifact.write_bytes(raw_bytes)
            if existing:
                conn.execute("UPDATE extension_entries SET description=?,current_version=?,updated_at=?,revision=revision+1,maintenance_policy=? WHERE entry_id=?", (description, version, now, maintenance_policy, entry_id))
            else:
                conn.execute("INSERT INTO extension_entries (entry_id,kind,name,description,submitter_user_id,visibility,current_version,usage_count,created_at,updated_at,platform_id,author_key,maintenance_policy) VALUES (?,?,?,?,?,'platform',?,0,?,?,?,?,?)", (entry_id,kind,name,description,submitter_user_id or 0,version,now,now,platform_id,author_key,maintenance_policy))
            manifest.update({"publisher": author_key, "base_version": base_version, "idempotency_key": idempotency_key, "request_fingerprint": hashlib.sha256(raw_bytes + json.dumps([name, description, maintenance_policy, author_key, base_version, expected_revision], ensure_ascii=False).encode()).hexdigest()})
            conn.execute(
                "INSERT INTO extension_versions (entry_id, version, artifact_path, manifest_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (entry_id, version, str(artifact), json.dumps(manifest or {}, ensure_ascii=False), now),
            )
            conn.execute("INSERT INTO extension_audit_events (entry_id,actor,action,revision,detail_json,created_at) VALUES (?,?,'publish',?,?,?)", (entry_id, author_key, existing["revision"] + 1 if existing else 1, json.dumps({"version": version, "base_version": base_version, "maintenance_policy": maintenance_policy}), now))
        return next(item for item in self.list_entries(platform_id) if item["entry_id"] == entry_id)

    def inspect(self, entry_id: str, platform_id: int | None, version: str | None = None) -> dict[str, Any]:
        with store_service._connect() as conn:
            entry = conn.execute("SELECT * FROM extension_entries WHERE entry_id=? AND platform_id IS ? AND status='active'", (entry_id, platform_id)).fetchone()
            if entry is None:
                raise FileNotFoundError("拓展不存在或无权访问")
            versions = [dict(row) for row in conn.execute("SELECT version,manifest_json,created_at FROM extension_versions WHERE entry_id=? ORDER BY created_at DESC LIMIT 100", (entry_id,))]
            artifact = conn.execute("SELECT artifact_path FROM extension_versions WHERE entry_id=? AND version=?", (entry_id, version or entry["current_version"])).fetchone()
        if artifact is None:
            raise FileNotFoundError("版本不存在")
        result = dict(entry)
        result.update(versions=versions, artifact_path=artifact["artifact_path"], version=version or entry["current_version"])
        return result

    def manage(self, entry_id: str, platform_id: int, actor: str, expected_revision: int, *, maintenance_policy: str | None = None, status: str | None = None) -> dict[str, Any]:
        if maintenance_policy is not None and maintenance_policy not in {"creator_only", "platform_members"}:
            raise ValueError("维护方式无效")
        if status is not None and status not in {"active", "unlisted"}:
            raise ValueError("状态无效")
        with store_service._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM extension_entries WHERE entry_id=? AND platform_id=?", (entry_id, platform_id)).fetchone()
            if row is None:
                raise FileNotFoundError("拓展不存在")
            admin = False
            if actor.startswith("user:"):
                user_id = int(actor.split(":", 1)[1])
                user = conn.execute("SELECT role FROM users WHERE user_id=? AND is_active=1", (user_id,)).fetchone()
                admin = bool(user and (user["role"] == "system_admin" or conn.execute("SELECT 1 FROM platform_admins WHERE platform_id=? AND user_id=?", (platform_id, user_id)).fetchone()))
            if row["author_key"] != actor and not admin:
                raise PermissionError("仅创作者或平台管理员可管理")
            if row["revision"] != expected_revision:
                raise ValueError("REVISION_CONFLICT: 请刷新条目后重试")
            conn.execute("UPDATE extension_entries SET maintenance_policy=?, status=?, revision=revision+1, updated_at=? WHERE entry_id=?", (maintenance_policy or row["maintenance_policy"], status or row["status"], utcnow_iso(), entry_id))
            conn.execute("INSERT INTO extension_audit_events (entry_id,actor,action,revision,detail_json,created_at) VALUES (?,?,'manage',?,?,?)", (entry_id, actor, row["revision"] + 1, json.dumps({"old_policy": row["maintenance_policy"], "policy": maintenance_policy or row["maintenance_policy"], "old_status": row["status"], "status": status or row["status"]}), utcnow_iso()))
            return dict(conn.execute("SELECT * FROM extension_entries WHERE entry_id=?", (entry_id,)).fetchone())

    def _validate_artifact(self, kind: str, raw_bytes: bytes, filename: str) -> tuple[bytes, dict[str, Any]]:
        if kind == "skill":
            from app.services.skill_service import skill_service

            with tempfile.TemporaryDirectory(prefix="aethercore-extension-") as directory:
                target = Path(directory)
                try:
                    if Path(filename).suffix.lower() == ".zip":
                        skill_service._install_skill_zip(target, raw_bytes)
                    else:
                        skill_service._install_single_skill_file(target, filename, raw_bytes)
                except (RuntimeError, ValueError) as exc:
                    raise ValueError(f"Skill 制品无效: {exc}") from exc
            return raw_bytes, {"format": "skill-package-v1"}

        try:
            config = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("MCP 制品必须是 UTF-8 JSON") from exc
        if isinstance(config, dict) and "mcpServers" in config:
            servers = config["mcpServers"]
            if not isinstance(servers, dict) or len(servers) != 1:
                raise ValueError("MCP 制品必须且只能包含一个 mcpServers 配置")
            server_name, server = next(iter(servers.items()))
            if not isinstance(server, dict):
                raise ValueError("MCP server 配置必须是对象")
            config = {"name": server_name, **server}
            config["transport"] = "streamable_http" if config.get("url") else "stdio"
            if isinstance(config.get("command"), str):
                config["command"] = [config["command"]]
        if not isinstance(config, dict):
            raise ValueError("MCP 制品必须是配置对象")
        if config.get("env") or config.get("headers") or config.get("secret_ref"):
            raise ValueError("商店 MCP 制品不能包含环境变量、请求头或其他凭据")
        normalized = capability_service._validate_mcp(config, persist_secrets=False)
        public_manifest = {
            key: value
            for key, value in normalized.items()
            if key not in {"id", "secret_ref", "has_secrets", "status", "scope"}
        }
        return json.dumps(public_manifest, ensure_ascii=False, indent=2).encode("utf-8"), {
            "format": "mcp-config-v1",
            "transport": normalized["transport"],
        }

    def install(self, entry_id: str, *, scope: str, session=None, platform_key: str | None = None, identity: dict[str, Any] | None = None, platform_id: int | None = None, version: str | None = None) -> dict[str, Any]:
        item = self.inspect(entry_id, platform_id, version)
        artifact = Path(item["artifact_path"])
        if item["kind"] == "skill":
            raw = artifact.read_bytes()
            if scope == "user":
                capability_service.install_skill(raw_bytes=raw, filename=artifact.name, **(identity or {}))
            elif scope == "session" and session is not None:
                from app.services.skill_service import skill_service
                skill_service.install_skill_upload(session, filename=artifact.name, raw_bytes=raw, isolated=True)
            elif scope == "platform" and platform_key:
                target = settings.platform_baselines_root / platform_key / "skills"
                target.mkdir(parents=True, exist_ok=True)
                from app.services.skill_service import skill_service
                if artifact.suffix.lower() == ".zip": skill_service._install_skill_zip(target, raw)
                else: skill_service._install_single_skill_file(target, artifact.name, raw)
            else:
                raise ValueError("安装作用域参数不完整")
        else:
            config = json.loads(artifact.read_text(encoding="utf-8"))
            if "mcpServers" in config:
                server_name, server = next(iter(config["mcpServers"].items()))
                config = {"name": server_name, **server}
                config["transport"] = "streamable_http" if config.get("url") else "stdio"
                if isinstance(config.get("command"), str): config["command"] = [config["command"]]
            if scope == "user": capability_service.upsert_mcp(config, **(identity or {}))
            elif scope == "session" and session is not None:
                normalized = dict(capability_service._validate_mcp(config), scope="session")
                session.session_mcp = [entry for entry in session.session_mcp if entry["name"] != normalized["name"]] + [normalized]
            elif scope == "platform" and platform_key: capability_service.upsert_platform_mcp(platform_key, config)
            else: raise ValueError("安装作用域参数不完整")
        with store_service._connect() as conn:
            conn.execute("UPDATE extension_entries SET usage_count=usage_count+1, updated_at=? WHERE entry_id=?", (utcnow_iso(), entry_id))
        return {"entry_id": entry_id, "kind": item["kind"], "scope": scope, "version": item["version"]}


extension_store_service = ExtensionStoreService()
