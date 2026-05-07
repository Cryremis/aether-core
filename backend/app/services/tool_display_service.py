from __future__ import annotations

from typing import Any


class ToolDisplayService:
    """Canonical tool display naming used by both streaming and persisted transcript."""

    def resolve(self, tool_name: str, tool_input: dict[str, Any] | None = None) -> dict[str, str]:
        normalized_name = str(tool_name or "").strip() or "tool"
        canonical_name = self._canonical_tool_name(normalized_name)
        payload = tool_input if isinstance(tool_input, dict) else {}

        if canonical_name == "sandbox_shell":
            return {
                "title": "运行命令",
                "meta": "",
            }

        return {
            "title": normalized_name,
            "meta": "tool",
        }

    def _canonical_tool_name(self, tool_name: str) -> str:
        lowered = tool_name.lower().strip()
        aliases = {
            "sandboxshell": "sandbox_shell",
            "sandbox-shell": "sandbox_shell",
        }
        return aliases.get(lowered, lowered)


tool_display_service = ToolDisplayService()
