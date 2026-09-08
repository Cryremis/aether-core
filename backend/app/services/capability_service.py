from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.skill_loader import skill_loader


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
        for directory in root.iterdir() if root.exists() else []:
            if directory.is_dir() and self._slugify(directory.name) == normalized:
                shutil.rmtree(directory)
                return True
        return False

    def _mcp_path(self, principal_key: str) -> Path:
        return self._root(principal_key) / "mcp.json"

    def list_mcp(self, *, user_id: int | None = None, platform_id: int | None = None, external_user_id: str | None = None) -> list[dict[str, Any]]:
        path = self._mcp_path(self._principal_key(user_id=user_id, platform_id=platform_id, external_user_id=external_user_id))
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def upsert_mcp(self, config: dict[str, Any], *, user_id: int | None = None, platform_id: int | None = None, external_user_id: str | None = None) -> dict[str, Any]:
        item = self._validate_mcp(config)
        principal = self._principal_key(user_id=user_id, platform_id=platform_id, external_user_id=external_user_id)
        items = [entry for entry in self.list_mcp(user_id=user_id, platform_id=platform_id, external_user_id=external_user_id) if entry["name"] != item["name"]]
        items.append(item)
        self._mcp_path(principal).write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return item

    def delete_mcp(self, name: str, *, user_id: int | None = None, platform_id: int | None = None, external_user_id: str | None = None) -> bool:
        principal = self._principal_key(user_id=user_id, platform_id=platform_id, external_user_id=external_user_id)
        items = self.list_mcp(user_id=user_id, platform_id=platform_id, external_user_id=external_user_id)
        next_items = [item for item in items if item["name"] != name]
        if len(next_items) == len(items):
            return False
        self._mcp_path(principal).write_text(json.dumps(next_items, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    def _validate_mcp(self, raw: dict[str, Any]) -> dict[str, Any]:
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
        return {
            "id": str(raw.get("id") or f"cap_{uuid.uuid4().hex}"),
            "name": name,
            "description": str(raw.get("description") or ""),
            "transport": transport,
            "command": raw.get("command", []),
            "url": raw.get("url"),
            "args": raw.get("args", []),
            "env": raw.get("env", {}),
            "headers": raw.get("headers", {}),
            "enabled": bool(raw.get("enabled", True)),
            "startup_timeout_sec": int(raw.get("startup_timeout_sec", 10)),
            "tool_timeout_sec": int(raw.get("tool_timeout_sec", 60)),
            "status": "configured",
            "scope": "user",
        }

    @staticmethod
    def _slugify(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


capability_service = CapabilityService()
