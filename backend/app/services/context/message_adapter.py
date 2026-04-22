# backend/app/services/context/message_adapter.py
"""上下文消息适配器。"""

from __future__ import annotations

import copy
import json
from typing import Any

from app.services.context.runtime_types import (
    CONTEXT_MESSAGE_SCHEMA_VERSION,
    new_message_id,
    utc_now_iso,
)
from app.services.session_types import AgentSession


RUNTIME_META_KEYS = {
    "message_id",
    "timestamp",
    "turn_index",
    "kind",
    "compression_meta",
    "visible_in_transcript",
    "ephemeral",
}


class ContextMessageAdapter:
    """为上下文管理规范化 AetherCore 消息结构。"""

    def make_user_message(self, content: str, *, turn_index: int) -> dict[str, Any]:
        return self.ensure_runtime_metadata(
            {"role": "user", "content": content},
            turn_index=turn_index,
            kind="user",
        )

    def make_assistant_message(
        self,
        *,
        content: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        blocks: list[dict[str, Any]] | None = None,
        turn_index: int,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant"}
        if content:
            message["content"] = content
        if tool_calls:
            message["tool_calls"] = tool_calls
        if blocks:
            message["blocks"] = blocks
        return self.ensure_runtime_metadata(message, turn_index=turn_index, kind="assistant")

    def make_tool_message(
        self,
        *,
        tool_call_id: str,
        content: str,
        tool_name: str,
        turn_index: int,
    ) -> dict[str, Any]:
        return self.ensure_runtime_metadata(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "content": content,
            },
            turn_index=turn_index,
            kind="tool_result",
        )

    def make_summary_message(
        self,
        *,
        content: str,
        turn_index: int,
        compression_meta: dict[str, Any],
    ) -> dict[str, Any]:
        message = self.ensure_runtime_metadata(
            {
                "role": "user",
                "content": content,
                "is_compact_summary": True,
                "visible_in_transcript": False,
                "compression_meta": compression_meta,
            },
            turn_index=turn_index,
            kind="compact_summary",
        )
        return message

    def make_boundary_message(
        self,
        *,
        turn_index: int,
        compression_meta: dict[str, Any],
    ) -> dict[str, Any]:
        boundary_id = new_message_id("boundary")
        content = (
            f"<compact_boundary id=\"{boundary_id}\" strategy=\"{compression_meta.get('strategy', 'unknown')}\">\n"
            f"  <timestamp>{utc_now_iso()}</timestamp>\n"
            f"  <messages_summarized>{compression_meta.get('messages_summarized', 0)}</messages_summarized>\n"
            f"  <tokens_before>{compression_meta.get('tokens_before', 0)}</tokens_before>\n"
            f"  <tokens_after>{compression_meta.get('tokens_after', 0)}</tokens_after>\n"
            "</compact_boundary>"
        )
        message = self.ensure_runtime_metadata(
            {
                "role": "system",
                "content": content,
                "is_boundary_marker": True,
                "visible_in_transcript": False,
                "compression_meta": {**compression_meta, "boundary_id": boundary_id},
            },
            turn_index=turn_index,
            kind="compact_boundary",
        )
        return message

    def ensure_runtime_metadata(
        self,
        message: dict[str, Any],
        *,
        turn_index: int | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        normalized = copy.deepcopy(message)
        normalized.setdefault("message_id", new_message_id())
        normalized.setdefault("timestamp", utc_now_iso())
        if turn_index is not None:
            normalized.setdefault("turn_index", turn_index)
        else:
            normalized.setdefault("turn_index", 0)
        normalized.setdefault("kind", kind or self._infer_kind(normalized))
        normalized.setdefault("visible_in_transcript", normalized["kind"] not in {"compact_boundary"})
        return normalized

    def normalize_session(self, session: AgentSession) -> bool:
        changed = False
        normalized_messages: list[dict[str, Any]] = []
        for index, message in enumerate(session.messages):
            normalized = self.ensure_runtime_metadata(message, turn_index=message.get("turn_index", index + 1))
            if normalized != message:
                changed = True
            normalized_messages.append(normalized)
        if session.messages != normalized_messages:
            session.messages = normalized_messages
            changed = True
        if session.message_schema_version != CONTEXT_MESSAGE_SCHEMA_VERSION:
            session.message_schema_version = CONTEXT_MESSAGE_SCHEMA_VERSION
            changed = True
        return changed

    def strip_runtime_metadata(self, message: dict[str, Any]) -> dict[str, Any]:
        api_message = {key: copy.deepcopy(value) for key, value in message.items() if key not in RUNTIME_META_KEYS}
        api_message.pop("blocks", None)
        api_message.pop("visible_in_transcript", None)
        api_message.pop("is_boundary_marker", None)
        api_message.pop("is_compact_summary", None)
        api_message.pop("tool_name", None)
        if api_message.get("role") == "assistant" and not api_message.get("content") and not api_message.get("tool_calls"):
            api_message["content"] = ""
        return api_message

    def to_api_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.strip_runtime_metadata(message) for message in messages if not message.get("ephemeral")]

    def estimate_text_for_summary(self, message: dict[str, Any], max_chars: int = 800) -> str:
        role = message.get("role", "unknown")
        if role == "assistant" and message.get("tool_calls"):
            names = [
                (tool_call.get("function") or {}).get("name", "tool")
                for tool_call in message.get("tool_calls", [])
                if isinstance(tool_call, dict)
            ]
            return f"assistant requested tools: {', '.join(names)}"
        if role == "tool":
            content = str(message.get("content", ""))
            tool_name = message.get("tool_name") or message.get("tool_call_id") or "tool"
            return f"tool result ({tool_name}): {content[:max_chars]}"
        content = message.get("content", "")
        if isinstance(content, str):
            return f"{role}: {content[:max_chars]}"
        return f"{role}: {json.dumps(content, ensure_ascii=False)[:max_chars]}"

    def _infer_kind(self, message: dict[str, Any]) -> str:
        if message.get("is_boundary_marker"):
            return "compact_boundary"
        if message.get("is_compact_summary"):
            return "compact_summary"
        role = message.get("role")
        if role == "tool":
            return "tool_result"
        return str(role or "message")


context_message_adapter = ContextMessageAdapter()
