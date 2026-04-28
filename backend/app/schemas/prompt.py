from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class PlatformPromptConfigUpdateRequest(BaseModel):
    enabled: bool = True
    system_prompt: str = ""


class PlatformPromptConfigSummary(BaseModel):
    enabled: bool = True
    system_prompt: str = ""
    updated_at: datetime | None = Field(default_factory=lambda: datetime.now(timezone.utc))
