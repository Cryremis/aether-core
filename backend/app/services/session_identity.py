"""conversations 是会话归属的权威来源；会话快照仅缓存身份字段。"""
from __future__ import annotations

from typing import Any, Mapping


IDENTITY_FIELDS = ("conversation_id", "platform_id", "external_user_id", "owner_user_id")


class SessionIdentityConflict(ValueError):
    def __init__(self, session_id: str, fields: list[str]):
        self.session_id = session_id
        self.fields = fields
        super().__init__("会话身份信息不一致，请联系管理员核对后恢复")


def identity_updates(session_id: str, current: Mapping[str, Any], conversation: Mapping[str, Any] | None) -> dict[str, Any]:
    """只补空值；非空身份冲突必须先核对，不能悄悄改归属或混用权限。"""
    if conversation is None:
        return {}
    conflicts = [field for field in IDENTITY_FIELDS
                 if current.get(field) not in (None, "") and current.get(field) != conversation.get(field)]
    if conflicts:
        raise SessionIdentityConflict(session_id, conflicts)
    return {field: conversation[field] for field in IDENTITY_FIELDS
            if current.get(field) in (None, "") and conversation.get(field) not in (None, "")}
