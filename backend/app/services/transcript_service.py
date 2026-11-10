from __future__ import annotations

import base64
import binascii
import json
from bisect import bisect_right
from typing import Any

from app.services.tool_display_service import tool_display_service


class TranscriptService:
    """Manage persisted UI transcript projections."""

    _WORKBENCH_PAGE_BYTES = 512 * 1024
    _TOOL_PREVIEW_CHARS = 2_048
    _TOOL_DETAIL_CHARS = 32_768

    def workbench_page(self, session, *, limit: int, before: str | None = None) -> dict[str, Any]:
        # 会话装载/收尾维护展示投影。热读只访问当前页，无需再次展开模型归档。
        revision = (session.context_state.get("timeline") or {}).get("revision", 0)
        transcript = session.transcript
        end = len(transcript)
        if before:
            try:
                cursor_revision, index, anchor_id = json.loads(base64.urlsafe_b64decode(before))
                if type(index) is not int or index < 1 or not isinstance(anchor_id, str):
                    raise ValueError("invalid history cursor")
            except (TypeError, ValueError, binascii.Error) as exc:
                raise ValueError("invalid history cursor") from exc
            if cursor_revision != revision or index >= end or transcript[index]["id"] != anchor_id:
                raise LookupError("stale history cursor")
            end = index
        rows, size = [], 0
        for item in reversed(transcript[max(0, end - limit):end]):
            row = self._workbench_preview(item)
            size += len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
            if rows and size > self._WORKBENCH_PAGE_BYTES:
                break
            rows.append(row)
        start = end - len(rows)
        next_cursor = base64.urlsafe_b64encode(json.dumps([revision, start, transcript[start]["id"]]).encode()).decode() if start else None
        return {"transcript": list(reversed(rows)), "transcript_page": {
            "total": len(transcript), "start": start, "end": end, "has_more": start > 0,
            "next_cursor": next_cursor, "revision": revision,
        }}

    def _workbench_preview(self, item: dict[str, Any]) -> dict[str, Any]:
        if item.get("role") != "assistant":
            return item
        blocks = []
        for block in item.get("blocks") or []:
            if block.get("kind") == "tool" and block.get("toolName") not in {"subagent_create", "subagent_send_message"}:
                output = str(block.get("outputText") or "")
                live_output = str(block.get("liveOutputText") or "")
                if len(output or live_output) > self._TOOL_PREVIEW_CHARS:
                    block = {**block, "outputText": (output or live_output)[:self._TOOL_PREVIEW_CHARS],
                             "liveOutputText": "", "detailAvailable": True}
            blocks.append(block)
        return {**item, "blocks": blocks}

    def workbench_tool_detail(self, session, message_id: str, block_id: str, offset: int) -> dict[str, Any] | None:
        for item in reversed(session.transcript):
            if item.get("id") != message_id or item.get("role") != "assistant":
                continue
            for block in item.get("blocks") or []:
                if block.get("id") == block_id and block.get("kind") == "tool":
                    output = str(block.get("outputText") or block.get("liveOutputText") or "")
                    end = min(len(output), offset + self._TOOL_DETAIL_CHARS)
                    return {"text": output[offset:end], "next_offset": end if end < len(output) else None}
            break
        return None

    def build_session_transcript(self, session) -> list[dict[str, Any]]:
        return self._build_transcript_with_compactions(session, audit=False)

    def visible_session_messages(self, session) -> list[dict[str, Any]]:
        from app.services.context.history_store import history_store
        return [message for message in history_store.expand_history(session.session_id, session.messages)
                if self._is_visible(message)]

    def build_audit_transcript(self, session) -> list[dict[str, Any]]:
        return self._build_transcript_with_compactions(session, audit=True)

    def _build_transcript_with_compactions(self, session, *, audit: bool) -> list[dict[str, Any]]:
        from app.services.context.history_store import history_store
        expanded = history_store.expand_history(session.session_id, session.messages)
        transcript = self.build_persisted_transcript(expanded)
        if not session.compaction_audit:
            return transcript
        positions = {message.get("message_id"): index for index, message in enumerate(expanded)}
        row_positions = [positions.get(item["id"], -1) for item in transcript]
        # 用原消息锚点定位，不能把摘要放到模型检查点的头部替换位置。
        events: dict[int, list[dict[str, Any]]] = {}
        for record in session.compaction_audit:
            anchor = positions.get(record.get("after_message_id"))
            if anchor is None:
                continue  # 被编辑/重跑丢弃的分支不回流到当前审计时间线。
            slot = bisect_right(row_positions, anchor)
            item = self.compaction_item(record)
            if audit:
                item.update(title="上下文压缩（仅审计可见）", summary=record["summary"],
                            detail=f"策略：{record['strategy']}；原因：{record['cause']}；{item['detail']}")
            events.setdefault(slot, []).append(item)
        result = []
        for index in range(len(transcript) + 1):
            result.extend(events.get(index, []))
            if index < len(transcript):
                result.append(transcript[index])
        return result

    @staticmethod
    def compaction_item(record: dict[str, Any]) -> dict[str, Any]:
        before, after = int(record["tokens_before"]), int(record["tokens_after"])
        return {
            "id": record["id"], "role": "system_event", "eventType": "context_compacted",
            "title": "上下文已压缩", "detail": f"输入估算 {before:,} → {after:,} tokens",
            "timestamp": record["timestamp"],
        }

    _SYSTEM_EVENT_TITLES: dict[str, str] = {
        "context_compacted": "上下文已压缩",
        "context_recovered": "上下文已恢复",
        "context_warning": "上下文接近上限",
        "context_blocked": "上下文已阻塞",
        "runtime_created": "沙箱已创建",
        "runtime_recreated": "沙箱已重建",
    }

    def build_chat_transcript(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Legacy transcript projection used only for old sessions migration."""
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
                        "timestamp": self._message_timestamp(message),
                    }
                )
                continue

            if role == "elicitation_response":
                transcript.append(self.transcript_item_from_committed_message(message, index=index))
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
                        "timestamp": self._message_timestamp(message),
                    }
                )

        return transcript

    def build_persisted_transcript(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        transcript: list[dict[str, Any]] = []
        latest_by_run = {message["run_id"]: index for index, message in enumerate(messages)
                         if message.get("run_id") and message.get("role") == "assistant" and self._is_visible(message)}
        for index, message in enumerate(messages):
            if not self._is_visible(message):
                continue
            role = str(message.get("role") or "")
            if role == "assistant" and message.get("run_id") and latest_by_run[message["run_id"]] != index:
                continue
            if role == "user":
                transcript.append(self.transcript_item_from_committed_message(message, index=index))
                continue
            if role == "elicitation_response":
                transcript.append(self.transcript_item_from_committed_message(message, index=index))
                continue
            if role == "assistant":
                assistant_item = self._assistant_from_message(message, index=index)
                if assistant_item is not None:
                    transcript.append(assistant_item)
        return transcript

    def transcript_item_from_committed_message(self, message: dict[str, Any], *, index: int = 0) -> dict[str, Any]:
        role = str(message.get("role") or "")
        if role == "elicitation_response":
            answers = message.get("answers")
            answers_list = answers if isinstance(answers, list) else []
            return {
                "id": str(message.get("message_id") or f"elicitation-response-{index}"),
                "role": "elicitation_response",
                "request_id": str(message.get("request_id") or ""),
                "title": str(message.get("title") or "问题已回复"),
                "summary": str(message.get("summary") or ""),
                "answers": [
                    {
                        "id": str(item.get("id") or f"answer-{index}-{answer_index}"),
                        "header": str(item.get("header") or "问题"),
                        "value": str(item.get("value") or "未填写"),
                    }
                    for answer_index, item in enumerate(answers_list)
                    if isinstance(item, dict)
                ],
                "timestamp": self._message_timestamp(message),
            }

        return {
            "id": str(message.get("message_id") or f"user-{index}"),
            "role": "user",
            "content": str(message.get("content") or ""),
            "timestamp": self._message_timestamp(message),
        }

    def assistant_item_from_blocks(
        self,
        *,
        message_id: str,
        blocks: list[dict[str, Any]],
        elapsed_ms: int | None,
        streaming: bool = False,
        response_started_at: str | None = None,
        timestamp: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_blocks: list[dict[str, Any]] = []
        normalized_elapsed = elapsed_ms
        normalized_started_at: str | None = None

        for raw_block in blocks:
            if not isinstance(raw_block, dict):
                continue
            kind = str(raw_block.get("kind") or "")
            if kind == "elapsed":
                elapsed_value = raw_block.get("elapsed_ms")
                if isinstance(elapsed_value, int):
                    normalized_elapsed = elapsed_value
                elif isinstance(elapsed_value, float):
                    normalized_elapsed = int(elapsed_value)
                started_at_value = raw_block.get("started_at")
                if isinstance(started_at_value, str):
                    normalized_started_at = started_at_value
                continue
            if kind == "content":
                normalized_blocks.append(
                    {
                        "id": str(raw_block.get("id") or f"{message_id}-content-{len(normalized_blocks)}"),
                        "kind": "content",
                        "content": str(raw_block.get("content") or ""),
                        "status": str(raw_block["status"])
                        if str(raw_block.get("status") or "") in {"aborted", "incomplete"}
                        else ("streaming" if streaming and str(raw_block.get("status") or "") == "streaming" else "done"),
                    }
                )
                continue
            if kind == "reasoning":
                block = {
                    "id": str(raw_block.get("id") or f"{message_id}-reasoning-{len(normalized_blocks)}"),
                    "kind": "reasoning",
                    "content": str(raw_block.get("content") or ""),
                }
                if isinstance(raw_block.get("started_at"), str):
                    block["started_at"] = raw_block["started_at"]
                if isinstance(raw_block.get("ended_at"), str):
                    block["ended_at"] = raw_block["ended_at"]
                if isinstance(raw_block.get("status"), str):
                    block["status"] = raw_block["status"]
                normalized_blocks.append(block)
                continue
            if kind == "runtime_notice":
                normalized_blocks.append(
                    {
                        "id": str(raw_block.get("id") or f"{message_id}-runtime-{len(normalized_blocks)}"),
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
                        "id": str(raw_block.get("id") or f"{message_id}-tool-{len(normalized_blocks)}"),
                        "kind": "tool",
                        "title": str(raw_block.get("title") or "tool"),
                            "toolName": str(raw_block.get("toolName") or raw_block.get("title") or "tool"),
                        "meta": str(raw_block.get("meta") or "tool"),
                        "argumentsText": str(raw_block.get("argumentsText") or ""),
                        "outputText": str(raw_block.get("outputText") or ""),
                        "status": self._normalize_tool_status(raw_block.get("status")),
                    }
                )

        return {
            "id": message_id,
            "role": "assistant",
            "blocks": normalized_blocks,
            "elapsedMs": normalized_elapsed,
            "streaming": streaming,
            "responseStartedAt": response_started_at or normalized_started_at,
            "timestamp": timestamp,
            **({"runId": run_id} if run_id else {}),
        }

    def system_event_item(
        self,
        *,
        event_type: str,
        detail: str | None = None,
        item_id: str | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": item_id or f"system-event-{event_type}",
            "role": "system_event",
            "title": self._SYSTEM_EVENT_TITLES.get(event_type, event_type),
            "detail": detail,
            "eventType": event_type,
            "timestamp": timestamp,
        }

    def append_item(self, transcript: list[dict[str, Any]], item: dict[str, Any]) -> list[dict[str, Any]]:
        item_id = str(item.get("id") or "")
        if item_id:
            for index, current in enumerate(transcript):
                if str(current.get("id") or "") == item_id:
                    next_transcript = list(transcript)
                    next_transcript[index] = item
                    return next_transcript
        return [*transcript, item]

    def _assistant_from_message(self, message: dict[str, Any], *, index: int) -> dict[str, Any] | None:
        blocks = message.get("blocks")
        normalized_blocks: list[dict[str, Any]] = []
        elapsed_ms: int | None = None
        started_at: str | None = None

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
                    started_at_value = raw_block.get("started_at")
                    if isinstance(started_at_value, str):
                        started_at = started_at_value
                    continue
                if kind == "content":
                    normalized_blocks.append(
                        {
                            "id": str(raw_block.get("id") or f"content-{index}-{len(normalized_blocks)}"),
                            "kind": "content",
                            "content": str(raw_block.get("content") or ""),
                            "status": str(raw_block["status"])
                            if str(raw_block.get("status") or "") in {"aborted", "incomplete"}
                            else "done",
                        }
                    )
                    continue
                if kind == "reasoning":
                    block = {
                        "id": str(raw_block.get("id") or f"reasoning-{index}-{len(normalized_blocks)}"),
                        "kind": "reasoning",
                        "content": str(raw_block.get("content") or ""),
                    }
                    if isinstance(raw_block.get("started_at"), str):
                        block["started_at"] = raw_block["started_at"]
                    if isinstance(raw_block.get("ended_at"), str):
                        block["ended_at"] = raw_block["ended_at"]
                    if isinstance(raw_block.get("status"), str):
                        block["status"] = raw_block["status"]
                    normalized_blocks.append(block)
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
                            "toolName": str(raw_block.get("toolName") or raw_block.get("title") or "tool"),
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
                display = tool_display_service.resolve(
                    str(function.get("name") or "tool"),
                    self._parse_arguments(function.get("arguments")),
                )
                normalized_blocks.append(
                    {
                        "id": tool_id,
                        "kind": "tool",
                        "title": display.get("title", str(function.get("name") or "tool")),
                        "toolName": str(function.get("name") or "tool"),
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
            "responseStartedAt": started_at,
            "timestamp": self._message_timestamp(message),
            **({"runId": message["run_id"]} if message.get("run_id") else {}),
        }

    def _message_timestamp(self, message: dict[str, Any]) -> str | None:
        timestamp = message.get("timestamp")
        return timestamp if isinstance(timestamp, str) else None

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
        if message.get("kind") == "skill_context":
            return False
        if bool(message.get("ephemeral")):
            return False
        return message.get("visible_in_transcript") is not False

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
