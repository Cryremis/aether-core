# backend/app/schemas/files.py
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class FileRecord(BaseModel):
    """上传文件或生成产物的元数据。"""

    file_id: str
    session_id: str
    name: str
    relative_path: str
    size: int
    media_type: str
    category: Literal["upload", "artifact", "skill", "platform"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FileListResponse(BaseModel):
    """文件列表返回体。"""

    items: list[FileRecord] = Field(default_factory=list)
