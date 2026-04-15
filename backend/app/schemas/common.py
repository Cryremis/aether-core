# backend/app/schemas/common.py
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class ApiResponse(BaseModel):
    """通用接口返回体。"""

    success: bool = True
    message: str = ""
    data: Any | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
