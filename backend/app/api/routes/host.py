# backend/app/api/routes/host.py
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_platform_secret
from app.host.registry import host_registry
from app.schemas.common import ApiResponse
from app.schemas.host import HostBindRequest

router = APIRouter(prefix="/api/v1/host", tags=["host"])


@router.post("/bind")
def bind_host(
    request: HostBindRequest,
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    """宿主平台绑定会话并注入能力。"""
    if platform["platform_key"] != request.platform_key:
        raise HTTPException(status_code=403, detail="平台密钥与目标平台不匹配")
    try:
        summary = host_registry.bind(request, platform=platform)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(message="宿主绑定成功", data=summary)
