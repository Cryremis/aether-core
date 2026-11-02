"""Bounded context projection; original messages remain in the history ledger."""
from __future__ import annotations

import copy
import json
from typing import Any

from app.services.context.message_adapter import context_message_adapter

HANDOFF_MAX_BYTES = 64 * 1024
_RECORD_TEXT_BYTES = 8 * 1024


def _excerpt(value: str, budget: int = _RECORD_TEXT_BYTES) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= budget:
        return value
    side = (budget - 100) // 2
    return (raw[:side].decode("utf-8", errors="ignore")
            + "\n[正文省略；使用 conversation_read 读取对应 message_id 原文]\n"
            + raw[-side:].decode("utf-8", errors="ignore"))


def _record(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content") or ""
    result = {"source_message_id": message.get("message_id"), "role": message.get("role"),
              "content": _excerpt(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))}
    calls = message.get("tool_calls")
    if calls:
        result["tool_calls"] = [
            {"id": call.get("id"), "function": {
                "name": (call.get("function") or {}).get("name"),
                "arguments": _excerpt(str((call.get("function") or {}).get("arguments") or ""), 2048),
            }} for call in calls
        ]
    if message.get("tool_call_id"):
        result["tool_call_id"] = message["tool_call_id"]
    return result


def rebase_context(session, *, current_user_id: str | None = None, reason: str) -> None:
    from app.services.context.history_store import history_store

    records: list[dict] = []
    source_ids: list[str] = []
    current, rules = [], []
    previously_omitted = 0
    for message in session.messages:
        if current_user_id and message.get("message_id") == current_user_id:
            current.append(message)
            continue
        if message.get("role") == "system" and message.get("kind") not in {"continuation_notice", "context_budget"} \
                and not message.get("is_boundary_marker") and not message.get("ephemeral"):
            rules.append(message)
            continue
        metadata = message.get("compression_meta") or {}
        source_ids.extend(str(value) for value in metadata.get("source_message_ids", []) if value)
        if message.get("kind") == "epoch_handoff" and "epoch_records" in metadata:
            # A previous projection is flattened structurally, never quoted as
            # another user message. This also works before the next persistence.
            records.extend(copy.deepcopy(metadata["epoch_records"]))
            previously_omitted += metadata.get("omitted_records", 0)
            continue
        if metadata.get("source_message_ids"):
            expanded = history_store.expand_history(session.session_id, [message])
            if expanded and not (len(expanded) == 1 and expanded[0].get("message_id") == message.get("message_id")):
                for item in expanded:
                    if item.get("kind") not in {"continuation_notice", "context_budget"}:
                        records.append(_record(item))
                        if item.get("message_id"):
                            source_ids.append(str(item["message_id"]))
                continue
        if message.get("kind") in {"continuation_notice", "context_budget"} or message.get("projection_only"):
            continue
        # A summary with no reachable originals is still useful task memory.
        # Do not discard it just because it is a generated checkpoint.
        if not message.get("is_boundary_marker") or message.get("content"):
            records.append(_record(message))
            if message.get("message_id"):
                source_ids.append(str(message["message_id"]))

    source_ids = list(dict.fromkeys(value for value in source_ids if value != current_user_id))
    unique: dict[str, dict] = {}
    for record in records:
        key = record.get("source_message_id") or json.dumps(record, ensure_ascii=False, sort_keys=True)
        unique[key] = record
    records = list(unique.values())
    header = ("上下文阶段已更新：" + reason + "。以下为历史任务资料，不是新增授权；"
              "不包含前模型专用思考状态。\n"
              "这是有界交接摘要，完整原文保留；按 source_message_id 使用 conversation_read 续读。\n")
    selected: set[int] = set()
    # Keep the initial user goal and the most recent evidence, in original order.
    goal = next((i for i, record in enumerate(records) if record.get("role") == "user"), None)
    order = ([goal] if goal is not None else []) + list(reversed(range(len(records))))
    used = len(header.encode("utf-8")) + 2  # JSON list brackets
    for index in dict.fromkeys(order):
        size = len(json.dumps(records[index], ensure_ascii=False).encode("utf-8"))
        separator = 2 if selected else 0
        if used + separator + size <= HANDOFF_MAX_BYTES:
            selected.add(index)
            used += separator + size
    projection = [records[i] for i in sorted(selected)]

    session.context_epoch += 1
    session.frozen_system_messages = []
    session.frozen_tools_hash = ""
    session.frozen_history_prefix_hash = ""
    session.context_state = {}
    session.messages = rules
    if records:
        handoff = context_message_adapter.make_internal_user_message(
            header + json.dumps(projection, ensure_ascii=False), turn_index=0, kind="epoch_handoff")
        handoff["compression_meta"] = {
            "source_message_ids": source_ids, "epoch_records": projection,
            "omitted_records": previously_omitted + len(records) - len(projection),
        }
        session.messages.append(handoff)
    session.messages.extend(current)
