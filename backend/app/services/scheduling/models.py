from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScheduleIdentity:
    """调用方身份。由后端注入，绝不信任模型或请求伪造身份。"""

    owner_user_id: int | None = None
    platform_id: int | None = None
    external_user_id: str | None = None
    external_org_id: str | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.owner_user_id or (self.platform_id and self.external_user_id))

    def matches(self, row: dict[str, Any]) -> bool:
        if self.owner_user_id is not None:
            return row.get("owner_user_id") == self.owner_user_id
        return (
            self.platform_id is not None
            and self.external_user_id is not None
            and row.get("platform_id") == self.platform_id
            and row.get("external_user_id") == self.external_user_id
        )
