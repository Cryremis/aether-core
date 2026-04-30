from __future__ import annotations

import json
from typing import Any

from app.services.tool_display_service import tool_display_service

class TranscriptService:
    """Build a stable, UI-oriented transcript from raw session messages."""

    def build_chat_transcript(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        transcript: list[dict[str, Any]] = []
        open_tool_blocks: dict[str, tuple[int, int]] = {}
        pending_tool_results: dict[str, dict[str, Any]] = {}

        for index, message in enumerate(messages):
            if not self._is_visible(message):
                continue

            role = str(message.get("role") or "")
            if role == "user":
                transcript.append(
                    {
                        "id": str(message.get("message_id") or f"user-{index}"),
                        "role": "user",
                        "content": str(message.get("content") or ""),
                    }
                )
                continue

            if role == "assistant":
                assistant_item = self._assistant_from_message(message, index=index)
                if assistant_item is None:
                    continue
                self._apply_pending_tool_results(assistant_item, pending_tool_results)
                transcript.append(assistant_item)
                assistant_index = len(transcript) - 1
                self._index_open_tool_blocks(
                    assistant_item=assistant_item,
                    assistant_index=assistant_index,
                    open_tool_blocks=open_tool_blocks,
                )
                continue

            if role == "tool":
                tool_call_id = str(message.get("tool_call_id") or "")
                if tool_call_id and tool_call_id in open_tool_blocks:
                    assistant_index, block_index = open_tool_blocks[tool_call_id]
                    assistant_item = transcript[assistant_index]
                    blocks = assistant_item.get("blocks") if isinstance(assistant_item, dict) else None
                    if isinstance(blocks, list) and 0 <= block_index < len(blocks):
                        block = blocks[block_index]
                        if isinstance(block, dict):
                            block["outputText"] = self._format_tool_output(message.get("content"))
                            status = self._infer_tool_status(message.get("content"))
                            block["status"] = status
                            if status != "running":
                                open_tool_blocks.pop(tool_call_id, None)
                    continue

                if tool_call_id:
                    pending_tool_results[tool_call_id] = {
                        "tool_name": str(message.get("tool_name") or "tool"),
                        "outputText": self._format_tool_output(message.get("content")),
                        "status": self._infer_tool_status(message.get("content")),
                    }
                    continue

                # Orphan tool result: still keep it visible by converting to a standalone assistant tool card.
                orphan_display = tool_display_service.resolve(str(message.get("tool_name") or "tool"), {})
                transcript.append(
                    {
                        "id": str(message.get("message_id") or f"tool-{index}"),
                        "role": "assistant",
                        "blocks": [
                            {
                                "id": tool_call_id or f"tool-{index}",
                                "kind": "tool",
                                "title": orphan_display.get("title", "tool"),
                                "meta": orphan_display.get("meta", "tool"),
                                "argumentsText": "",
                                "outputText": self._format_tool_output(message.get("content")),
                                "status": self._infer_tool_status(message.get("content")),
                            }
                        ],
                        "elapsedMs": None,
                        "streaming": False,
                    }
                )

        # Any still-pending tool results are dropped from UI transcript when no matching assistant tool block exists.
        # This avoids dangling shell cards rendered far away from their reasoning/content context.

        return transcript

    def _assistant_from_message(self, message: dict[str, Any], *, index: int) -> dict[str, Any] | None:
        blocks = message.get("blocks")
        normalized_blocks: list[dict[str, Any]] = []
        elapsed_ms: int | None = None

        if isinstance(blocks, list):
            for raw_block in blocks:
                if not isinstance(raw_block, dict):
                    continue
                kind = str(raw_block.get("kind") or "")
                if kind == "elapsed":
                    elapsed_value = raw_block.get("elapsed_ms")
                    if isinstance(elapsed_value, int):
                        elapsed_ms = elapsed_value
                    elif isinstance(elapsed_value, float):
                        elapsed_ms = int(elapsed_value)
                    continue
                if kind == "content":
                    normalized_blocks.append(
                        {
                            "id": str(raw_block.get("id") or f"content-{index}-{len(normalized_blocks)}"),
                            "kind": "content",
                            "content": str(raw_block.get("content") or ""),
                            "status": "aborted"
                            if str(raw_block.get("status") or "") == "aborted"
                            else "done",
                        }
                    )
                    continue
                if kind == "reasoning":
                    normalized_blocks.append(
                        {
                            "id": str(raw_block.get("id") or f"reasoning-{index}-{len(normalized_blocks)}"),
                            "kind": "reasoning",
                            "content": str(raw_block.get("content") or ""),
                        }
                    )
                    continue
                if kind == "runtime_notice":
                    normalized_blocks.append(
                        {
                            "id": str(raw_block.get("id") or f"runtime-notice-{index}-{len(normalized_blocks)}"),
                            "kind": "runtime_notice",
                            "eventType": str(raw_block.get("eventType") or "runtime_recreated"),
                            "title": str(raw_block.get("title") or "沙箱已重建"),
                            "detail": str(raw_block.get("detail") or ""),
                        }
                    )
                    continue
                if kind == "tool":
                    normalized_blocks.append(
                        {
                            "id": str(raw_block.get("id") or f"tool-{index}-{len(normalized_blocks)}"),
                            "kind": "tool",
                            "title": str(raw_block.get("title") or "tool"),
                            "meta": str(raw_block.get("meta") or "tool"),
                            "argumentsText": str(raw_block.get("argumentsText") or ""),
                            "outputText": str(raw_block.get("outputText") or ""),
                            "status": self._normalize_tool_status(raw_block.get("status")),
                        }
                    )

        if not normalized_blocks:
            content = str(message.get("content") or "")
            if content:
                normalized_blocks.append(
                    {
                        "id": f"content-{index}",
                        "kind": "content",
                        "content": content,
                        "status": "done",
                    }
                )

        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for offset, tool_call in enumerate(tool_calls):
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                if not isinstance(function, dict):
                    continue
                tool_id = str(tool_call.get("id") or f"call-{index}-{offset}")
                display = tool_display_service.resolve(str(function.get("name") or "tool"), self._parse_arguments(function.get("arguments")))
                normalized_blocks.append(
                    {
                        "id": tool_id,
                        "kind": "tool",
                        "title": display.get("title", str(function.get("name") or "tool")),
                        "meta": display.get("meta", "tool"),
                        "argumentsText": self._pretty_arguments(function.get("arguments")),
                        "outputText": "",
                        "status": "running",
                    }
                )

        if not normalized_blocks:
            return None

        return {
            "id": str(message.get("message_id") or f"assistant-{index}"),
            "role": "assistant",
            "blocks": normalized_blocks,
            "elapsedMs": elapsed_ms,
            "streaming": False,
        }

    def _index_open_tool_blocks(
        self,
        *,
        assistant_item: dict[str, Any],
        assistant_index: int,
        open_tool_blocks: dict[str, tuple[int, int]],
    ) -> None:
        blocks = assistant_item.get("blocks")
        if not isinstance(blocks, list):
            return
        for block_index, block in enumerate(blocks):
            if not isinstance(block, dict) or block.get("kind") != "tool":
                continue
            block_id = str(block.get("id") or "")
            if not block_id:
                continue
            if str(block.get("status") or "") == "running":
                open_tool_blocks[block_id] = (assistant_index, block_index)

    def _apply_pending_tool_results(
        self,
        assistant_item: dict[str, Any],
        pending_tool_results: dict[str, dict[str, Any]],
    ) -> None:
        blocks = assistant_item.get("blocks")
        if not isinstance(blocks, list):
            return
        for block in blocks:
            if not isinstance(block, dict) or block.get("kind") != "tool":
                continue
            block_id = str(block.get("id") or "")
            if not block_id or block_id not in pending_tool_results:
                continue
            pending = pending_tool_results.pop(block_id)
            if not str(block.get("outputText") or "").strip():
                block["outputText"] = str(pending.get("outputText") or "")
            current_status = str(block.get("status") or "")
            if current_status in {"", "running"}:
                block["status"] = str(pending.get("status") or "done")

    def _is_visible(self, message: dict[str, Any]) -> bool:
        if bool(message.get("ephemeral")):
            return False
        if message.get("visible_in_transcript") is False:
            return False
        return True

    def _normalize_tool_status(self, status: Any) -> str:
        value = str(status or "")
        if value in {"running", "done", "aborted"}:
            return value
        return "done"

    def _pretty_arguments(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, indent=2)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return ""
            try:
                parsed = json.loads(stripped)
            except Exception:
                return stripped
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        return str(value)

    def _parse_arguments(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return {}
            try:
                parsed = json.loads(stripped)
            except Exception:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    def _format_tool_output(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, (dict, list)):
            return json.dumps(content, ensure_ascii=False, indent=2)
        if not isinstance(content, str):
            return str(content)
        stripped = content.strip()
        if not stripped:
            return ""
        try:
            parsed = json.loads(stripped)
        except Exception:
            return content
        return json.dumps(parsed, ensure_ascii=False, indent=2)

    def _infer_tool_status(self, content: Any) -> str:
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except Exception:
                return "done"
        elif isinstance(content, dict):
            parsed = content
        else:
            return "done"
        if isinstance(parsed, dict) and parsed.get("aborted") is True:
            return "aborted"
        return "done"


transcript_service = TranscriptService()
