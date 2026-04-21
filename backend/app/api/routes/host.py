# backend/app/api/routes/host.py
import logging
from fastapi import APIRouter, HTTPException

from app.host.registry import host_registry
from app.schemas.common import ApiResponse
from app.schemas.host import HostBindRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/host", tags=["host"])


@router.post("/bind")
def bind_host(request: HostBindRequest) -> ApiResponse:
    """宿主平台绑定会话并注入能力。"""
    try:
        summary = host_registry.bind(request)
        return ApiResponse(message="宿主绑定成功", data=summary)
    except Exception as e:
        import traceback
        logger.error(f"绑定失败: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"绑定失败: {str(e)}")
