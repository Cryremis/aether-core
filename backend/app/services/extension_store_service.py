from __future__ import annotations

import json
import shutil
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

    def list_entries(self) -> list[dict[str, Any]]:
        with store_service._connect() as conn:
            rows = conn.execute(
                "SELECT e.*, COALESCE(u.full_name, u.username, 'Unknown') AS submitter_name "
                "FROM extension_entries e LEFT JOIN users u ON u.user_id=e.submitter_user_id "
                "ORDER BY e.updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def publish(self, *, kind: str, name: str, description: str, version: str, submitter_user_id: int, raw_bytes: bytes, filename: str, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
        if kind not in {"skill", "mcp"}:
            raise ValueError("拓展类型必须是 skill 或 mcp")
        if not raw_bytes or len(raw_bytes) > 20 * 1024 * 1024:
            raise ValueError("拓展文件必须介于 1 字节和 20 MiB 之间")
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
            raise ValueError("拓展名称只能包含字母、数字、下划线、点和短横线")
        version = version.strip() or "1.0.0"
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version):
            raise ValueError("版本号必须符合 SemVer，例如 1.0.0")
        raw_bytes, validated_manifest = self._validate_artifact(kind, raw_bytes, filename)
        manifest = {**validated_manifest, **(manifest or {})}
        entry_id = f"ext_{uuid.uuid5(uuid.NAMESPACE_URL, f'{kind}:{name}').hex}"
        root = settings.resolved_storage_root / "extensions" / entry_id / version
        if root.exists():
            raise ValueError("该版本已存在")
        root.mkdir(parents=True)
        artifact = root / Path(filename).name
        artifact.write_bytes(raw_bytes)
        now = utcnow_iso()
        with store_service._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO extension_entries (entry_id, kind, name, description, submitter_user_id, visibility, current_version, usage_count, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'public', ?, COALESCE((SELECT usage_count FROM extension_entries WHERE entry_id = ?), 0), COALESCE((SELECT created_at FROM extension_entries WHERE entry_id = ?), ?), ?)",
                (entry_id, kind, name, description, submitter_user_id, version, entry_id, entry_id, now, now),
            )
            conn.execute(
                "INSERT INTO extension_versions (entry_id, version, artifact_path, manifest_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (entry_id, version, str(artifact), json.dumps(manifest or {}, ensure_ascii=False), now),
            )
        return next(item for item in self.list_entries() if item["entry_id"] == entry_id)

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

    def install(self, entry_id: str, *, scope: str, session=None, platform_key: str | None = None, identity: dict[str, Any] | None = None) -> dict[str, Any]:
        with store_service._connect() as conn:
            row = conn.execute("SELECT e.*, v.artifact_path, v.manifest_json FROM extension_entries e JOIN extension_versions v ON v.entry_id=e.entry_id AND v.version=e.current_version WHERE e.entry_id=?", (entry_id,)).fetchone()
        if not row:
            raise FileNotFoundError("商店拓展不存在")
        item = dict(row)
        artifact = Path(item["artifact_path"])
        if item["kind"] == "skill":
            raw = artifact.read_bytes()
            if scope == "user":
                capability_service.install_skill(raw_bytes=raw, filename=artifact.name, **(identity or {}))
            elif scope == "session" and session is not None:
                from app.services.skill_service import skill_service
                skill_service.install_skill_upload(session, filename=artifact.name, raw_bytes=raw)
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
        return {"entry_id": entry_id, "kind": item["kind"], "scope": scope}


extension_store_service = ExtensionStoreService()
