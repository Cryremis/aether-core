"""按模型 API 步骤切分历史，工具调用和全部结果属于同一依赖组。"""
from __future__ import annotations


def api_groups(messages: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    current: list[dict] = []
    has_assistant = False
    pending: set[str] = set()
    for message in messages:
        if message.get("projection_only"):
            # UI 聚合记录跟随对应步骤，但不构成新的模型 API 轮次。
            current.append(message)
            continue
        role = message.get("role")
        if current and not pending and (role == "user" or (role == "assistant" and has_assistant)):
            groups.append(current)
            current = []
            has_assistant = False
        current.append(message)
        if role == "assistant":
            has_assistant = True
            pending.update(str(call.get("id")) for call in message.get("tool_calls", []))
        elif role == "tool":
            pending.discard(str(message.get("tool_call_id")))
    if current:
        groups.append(current)
    return groups


def split_checkpoint(messages: list[dict], estimator, token_budget: int) -> tuple[list[dict], list[dict]]:
    groups = api_groups([m for m in messages if not m.get("is_boundary_marker")])
    kept = 0
    tokens = 0
    for group in reversed(groups):
        if kept >= 2 and tokens >= token_budget:
            break
        from app.services.context.message_adapter import context_message_adapter
        tokens += estimator.estimate_messages_tokens(context_message_adapter.to_api_messages(group))
        kept += 1
    split = len(groups) - kept
    if split == 0 and len(groups) >= 2 and all(message.get("kind") in {"epoch_handoff", "epoch_checkpoint", "compact_summary"} for message in groups[0]):
        split = 1
    older = [message for group in groups[:split] for message in group]
    recent = [message for group in groups[split:] for message in group]
    # 当前任务的原始用户输入必须在工作集中，不依赖摘要记住用户目标。
    latest_user = next((m for m in reversed(messages) if m.get("role") == "user" and m.get("kind", "user") == "user" and not m.get("is_compact_summary")), None)
    if latest_user in older:
        older.remove(latest_user)
        recent.insert(0, latest_user)
    return older, recent
