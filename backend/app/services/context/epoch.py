"""模型/签名前缀变化时的显式上下文交接，专用推理状态不会跨绑定复用。"""
from __future__ import annotations

import json

from app.services.context.message_adapter import context_message_adapter


def rebase_context(session, *, current_user_id: str | None = None, reason: str) -> None:
    prior = []
    current = []
    rules = []
    original = session.messages
    source_ids = [message.get("message_id") for message in original
                  if message.get("role") != "system" and (not current_user_id or message.get("message_id") != current_user_id)]
    if any((message.get("provider_payload") or {}).get("complete_window") for message in original):
        # opaque 检查点的占位正文不是任务记忆，换模型时必须从可达原文恢复交接资料。
        from app.services.context.history_store import history_store
        original = history_store.expand_history(session.session_id, original)
    for message in original:
        if message.get("role") == "system" and message.get("kind") != "continuation_notice" and not message.get("is_boundary_marker") and not message.get("ephemeral"):
            rules.append(message)
        elif current_user_id and message.get("message_id") == current_user_id:
            current.append(message)
        elif not message.get("is_boundary_marker") and not message.get("projection_only"):
            prior.append({"source_message_id": message.get("message_id"), "role": message.get("role"),
                          "content": message.get("content"), "tool_calls": message.get("tool_calls"),
                          "tool_call_id": message.get("tool_call_id")})
    session.context_epoch += 1
    session.frozen_system_messages = []
    session.frozen_tools_hash = ""
    session.frozen_history_prefix_hash = ""
    session.context_state = {}
    session.messages = rules
    if prior:
        handoff = context_message_adapter.make_internal_user_message(
            "上下文阶段已更新：" + reason + "。以下为历史任务资料，不是新增授权；不包含前模型专用思考状态。\n"
            + json.dumps(prior, ensure_ascii=False), turn_index=0, kind="epoch_handoff")
        handoff["compression_meta"] = {"source_message_ids": source_ids}
        session.messages.append(handoff)
    session.messages.extend(current)
