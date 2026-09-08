from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.skill_loader import skill_loader
from app.services.store import store_service
from app.services.secret_store_service import secret_store_service


class CapabilityValidationError(ValueError):
    pass


class CapabilityService:
    """统一管理用户级能力和 MCP 配置；平台/会话能力仍由各自存储提供。"""

    def _principal_key(self, *, user_id: int | None = None, platform_id: int | None = None, external_user_id: str | None = None) -> str:
        if user_id is not None:
            return f"user_{user_id}"
        if platform_id is not None and external_user_id:
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", external_user_id)[:96]
            return f"embed_{platform_id}_{safe}"
        raise CapabilityValidationError("需要用户身份")

    def _root(self, principal_key: str) -> Path:
        root = settings.resolved_storage_root / "users" / principal_key
        (root / "skills").mkdir(parents=True, exist_ok=True)
        return root

    def list_user_skills(self, *, user_id: int | None = None, platform_id: int | None = None, external_user_id: str | None = None) -> list[dict[str, Any]]:
        root = self._root(self._principal_key(user_id=user_id, platform_id=platform_id, external_user_id=external_user_id)) / "skills"
        return [dict(item, source="user", scope="user") for item in skill_loader.load_directory(root, source="user")]

    def install_skill(self, *, raw_bytes: bytes, filename: str, user_id: int | None = None, platform_id: int | None = None, external_user_id: str | None = None) -> list[dict[str, Any]]:
        if not raw_bytes:
            raise CapabilityValidationError("技能文件为空")
        if len(raw_bytes) > 20 * 1024 * 1024:
            raise CapabilityValidationError("技能上传不能超过 20 MiB")
        root = self._root(self._principal_key(user_id=user_id, platform_id=platform_id, external_user_id=external_user_id)) / "skills"
        from app.services.skill_service import skill_service
        # 复用现有严格 zip/SKILL.md 校验逻辑，但写入用户目录。
        if Path(filename).suffix.lower() == ".zip":
            skill_service._install_skill_zip(root, raw_bytes)
        else:
            skill_service._install_single_skill_file(root, filename, raw_bytes)
        return self.list_user_skills(user_id=user_id, platform_id=platform_id, external_user_id=external_user_id)

    def delete_skill(self, name: str, *, user_id: int | None = None, platform_id: int | None = None, external_user_id: str | None = None) -> bool:
        root = self._root(self._principal_key(user_id=user_id, platform_id=platform_id, external_user_id=external_user_id)) / "skills"
        normalized = self._slugify(name)
        loaded_by_dir = {
            Path(item["path"]).parent.resolve(): self._slugify(str(item.get("name") or ""))
            for item in skill_loader.load_directory(root, source="user")
        }
        for directory in root.iterdir() if root.exists() else []:
            if directory.is_dir() and (self._slugify(directory.name) == normalized or loaded_by_dir.get(directory.resolve()) == normalized):
                shutil.rmtree(directory)
                return True
        return False

    def _mcp_path(self, principal_key: str) -> Path:
        return self._root(principal_key) / "mcp.json"

    def _platform_mcp_path(self, platform_key: str) -> Path:
        root = settings.platform_baselines_root / platform_key
        root.mkdir(parents=True, exist_ok=True)
        return root / "mcp.json"

    def list_platform_mcp(self, platform_key: str) -> list[dict[str, Any]]:
        path = self._platform_mcp_path(platform_key)
        return [self._public_mcp(item) for item in self._load_mcp_file(path)]

    def upsert_platform_mcp(self, platform_key: str, config: dict[str, Any]) -> dict[str, Any]:
        path = self._platform_mcp_path(platform_key)
        current = self._load_mcp_file(path)
        existing = next((entry for entry in current if entry["name"] == config.get("name")), None)
        item = dict(self._validate_mcp(config, existing=existing), scope="platform")
        items = [entry for entry in current if entry["name"] != item["name"]]
        items.append(item)
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return self._public_mcp(item)

    def delete_platform_mcp(self, platform_key: str, name: str) -> bool:
        items = self._load_mcp_file(self._platform_mcp_path(platform_key))
        next_items = [item for item in items if item["name"] != name]
        if len(items) == len(next_items):
            return False
        removed = next(item for item in items if item["name"] == name)
        self._platform_mcp_path(platform_key).write_text(json.dumps(next_items, ensure_ascii=False, indent=2), encoding="utf-8")
        secret_store_service.delete(removed.get("secret_ref"))
        return True

    def list_effective_mcp(self, session, *, include_secrets: bool = False) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if session.platform_id:
            platform = store_service.get_platform_by_id(session.platform_id)
            if platform:
                items.extend(self._load_mcp_file(self._platform_mcp_path(platform["platform_key"])))
        try:
            items.extend(self._list_mcp_raw(user_id=session.owner_user_id, platform_id=session.platform_id, external_user_id=session.external_user_id))
        except CapabilityValidationError:
            pass
        items.extend(session.session_mcp)
        disabled = set(session.disabled_capability_ids)
        resolved: dict[str, dict[str, Any]] = {}
        for item in items:
            key = str(item.get("id") or "")
            alias = str(item.get("name") or "")
            if key in disabled or f"mcp:{alias}" in disabled or not item.get("enabled", True):
                continue
            resolved[alias] = item
        values = list(resolved.values())
        return [self.hydrate_mcp(item) if include_secrets else self._public_mcp(item) for item in values]

    def list_mcp(self, *, user_id: int | None = None, platform_id: int | None = None, external_user_id: str | None = None) -> list[dict[str, Any]]:
        return [self._public_mcp(item) for item in self._list_mcp_raw(user_id=user_id, platform_id=platform_id, external_user_id=external_user_id)]

    def _list_mcp_raw(self, *, user_id: int | None = None, platform_id: int | None = None, external_user_id: str | None = None) -> list[dict[str, Any]]:
        path = self._mcp_path(self._principal_key(user_id=user_id, platform_id=platform_id, external_user_id=external_user_id))
        return self._load_mcp_file(path)

    def upsert_mcp(self, config: dict[str, Any], *, user_id: int | None = None, platform_id: int | None = None, external_user_id: str | None = None) -> dict[str, Any]:
        principal = self._principal_key(user_id=user_id, platform_id=platform_id, external_user_id=external_user_id)
        current = self._list_mcp_raw(user_id=user_id, platform_id=platform_id, external_user_id=external_user_id)
        existing = next((entry for entry in current if entry["name"] == config.get("name")), None)
        item = self._validate_mcp(config, existing=existing)
        items = [entry for entry in current if entry["name"] != item["name"]]
        items.append(item)
        self._mcp_path(principal).write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return self._public_mcp(item)

    def delete_mcp(self, name: str, *, user_id: int | None = None, platform_id: int | None = None, external_user_id: str | None = None) -> bool:
        principal = self._principal_key(user_id=user_id, platform_id=platform_id, external_user_id=external_user_id)
        items = self._list_mcp_raw(user_id=user_id, platform_id=platform_id, external_user_id=external_user_id)
        next_items = [item for item in items if item["name"] != name]
        if len(next_items) == len(items):
            return False
        removed = next(item for item in items if item["name"] == name)
        self._mcp_path(principal).write_text(json.dumps(next_items, ensure_ascii=False, indent=2), encoding="utf-8")
        secret_store_service.delete(removed.get("secret_ref"))
        return True

    def _validate_mcp(self, raw: dict[str, Any], *, existing: dict[str, Any] | None = None, persist_secrets: bool = True) -> dict[str, Any]:
        name = str(raw.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
            raise CapabilityValidationError("MCP 名称只能包含字母、数字、下划线、点和短横线")
        transport = str(raw.get("transport") or "").lower()
        if transport not in {"stdio", "streamable_http"}:
            raise CapabilityValidationError("MCP transport 必须是 stdio 或 streamable_http")
        if transport == "stdio":
            command = raw.get("command")
            if not isinstance(command, list) or not command or not all(isinstance(value, str) and value.strip() for value in command):
                raise CapabilityValidationError("stdio MCP 必须提供非空 command 数组")
        else:
            url = str(raw.get("url") or "")
            if not re.match(r"^https://", url, re.I):
                raise CapabilityValidationError("远程 MCP 仅允许 HTTPS URL")
        auth = str(raw.get("auth") or "none").lower()
        if auth not in {"none", "oauth"} or (auth == "oauth" and transport != "streamable_http"):
            raise CapabilityValidationError("OAuth 仅支持远程 Streamable HTTP MCP")
        env = raw.get("env") or {}
        headers = raw.get("headers") or {}
        if not isinstance(env, dict) or not isinstance(headers, dict):
            raise CapabilityValidationError("MCP env 和 headers 必须是对象")
        if len(env) > 64 or len(headers) > 64 or any(not isinstance(k, str) or not isinstance(v, str) for k, v in {**env, **headers}.items()):
            raise CapabilityValidationError("MCP 凭据格式无效或数量过多")
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) for key in env):
            raise CapabilityValidationError("MCP 环境变量名称无效")
        if any(not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) for key in headers):
            raise CapabilityValidationError("MCP 请求头名称无效")
        secret_ref = str((existing or {}).get("secret_ref") or raw.get("secret_ref") or "") or None
        if persist_secrets and (env or headers or auth == "oauth"):
            secrets = secret_store_service.get(secret_ref)
            if env:
                secrets["env"] = env
            if headers:
                secrets["headers"] = headers
            secret_ref = secret_store_service.put(secrets, secret_ref)
        return {
            "id": str((existing or {}).get("id") or raw.get("id") or f"cap_{uuid.uuid4().hex}"),
            "name": name,
            "description": str(raw.get("description") or ""),
            "transport": transport,
            "command": raw.get("command", []),
            "url": raw.get("url"),
            "args": raw.get("args", []),
            "auth": auth,
            "oauth_scopes": str(raw.get("oauth_scopes") or ""),
            "secret_ref": secret_ref,
            "has_secrets": bool(secret_ref),
            "enabled": bool(raw.get("enabled", True)),
            "startup_timeout_sec": int(raw.get("startup_timeout_sec", 10)),
            "tool_timeout_sec": int(raw.get("tool_timeout_sec", 60)),
            "status": "configured",
            "scope": "user",
        }

    def hydrate_mcp(self, item: dict[str, Any]) -> dict[str, Any]:
        secrets = secret_store_service.get(item.get("secret_ref"))
        return dict(item, env=secrets.get("env", {}), headers=secrets.get("headers", {}))

    def _public_mcp(self, item: dict[str, Any]) -> dict[str, Any]:
        secrets = secret_store_service.get(item.get("secret_ref")) if item.get("secret_ref") else {}
        public = {key: value for key, value in item.items() if key != "secret_ref"}
        public["has_secrets"] = bool(secrets.get("env") or secrets.get("headers"))
        public["oauth_connected"] = bool(secrets.get("oauth_tokens"))
        return public

    def _load_mcp_file(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise CapabilityValidationError("MCP 配置文件格式无效")
        migrated = False
        items: list[dict[str, Any]] = []
        for raw in value:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            env = item.pop("env", {})
            headers = item.pop("headers", {})
            if env or headers:
                item["secret_ref"] = secret_store_service.put({"env": env, "headers": headers}, item.get("secret_ref"))
                item["has_secrets"] = True
                migrated = True
            item.setdefault("auth", "none")
            item.setdefault("oauth_scopes", "")
            items.append(item)
        if migrated:
            path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return items

    @staticmethod
    def _slugify(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


capability_service = CapabilityService()
