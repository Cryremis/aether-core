# backend/app/api/routes/host.py
from fastapi import APIRouter

from app.host.registry import host_registry
from app.schemas.common import ApiResponse
from app.schemas.host import HostBindRequest

router = APIRouter(prefix="/api/v1/host", tags=["host"])


@router.post("/bind")
def bind_host(request: HostBindRequest) -> ApiResponse:
    """宿主平台绑定会话并注入能力。"""

    summary = host_registry.bind(request)
    return ApiResponse(message="宿主绑定成功", data=summary.model_dump(mode="json"))
