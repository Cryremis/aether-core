from __future__ import annotations

import copy
import re
from collections.abc import Iterable
from typing import Any


class ToolDisplayService:
    """统一每次工具调用的展示说明；展示参数不进入业务执行接口。"""

    note_key = "_aether_call_note"

    def annotate_schemas(self, schemas: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        result = copy.deepcopy(list(schemas))
        for item in result:
            function = item.get("function", {})
            schema = function.get("parameters", {})
            if self.supports_note(schema):
                schema.setdefault("properties", {})[self.note_key] = {
                    "type": "string",
                    "description": "可选：用用户的语言简短说明本次调用要做什么，作为用户可见标题。不要写凭据、参数原文或执行结果；不需要时省略。",
                }
                if function.get("strict"):
                    # 严格协议要求所有属性列入 required；null 表示不提供说明。
                    schema["properties"][self.note_key]["type"] = ["string", "null"]
                    schema["required"] = [*schema.get("required", []), self.note_key]
        return result

    def supports_note(self, schema: Any) -> bool:
        # 不覆盖宿主已有字段，也不向复杂联合 schema 强行插入属性。
        return (isinstance(schema, dict) and schema.get("type") == "object"
                and not any(key in schema for key in ("$ref", "allOf", "anyOf", "oneOf"))
                and self.note_key not in schema.get("properties", {}))

    def split_arguments(self, tool_name: str, arguments: dict[str, Any],
                        schemas: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], str]:
        schema = next((item.get("function", {}).get("parameters", {}) for item in schemas
                       if item.get("function", {}).get("name") == tool_name), {})
        clean = dict(arguments)
        note = self.normalize_note(clean.pop(self.note_key, None)) if self.supports_note(schema) else ""
        return clean, note

    def normalize_note(self, value: Any) -> str:
        if not isinstance(value, str):
            return ""
        # 单行、有限长度，避免控制字符破坏进度消息布局。
        return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", value).split())[:120]

    def resolve(self, tool_name: str, tool_input: dict[str, Any] | None = None,
                *, note: str | None = None) -> dict[str, str]:
        normalized_name = str(tool_name or "").strip() or "tool"
        payload = tool_input if isinstance(tool_input, dict) else {}
        title = self.normalize_note(payload.get(self.note_key) if note is None else note)
        shell = normalized_name.lower() in {"sandbox_shell", "sandboxshell", "sandbox-shell"}
        return {"title": title or normalized_name,
                "meta": str(payload.get("shell") or "powershell") if shell else "tool"}


tool_display_service = ToolDisplayService()
