from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import AuthContext, require_admin
from app.schemas.common import ApiResponse
from app.schemas.platform import PlatformSandboxProxyConfigUpdateRequest
from app.services.platform_sandbox_proxy_service import platform_sandbox_proxy_service
from app.services.session_runtime_service import session_runtime_service
from app.services.store import store_service

router = APIRouter(prefix="/api/v1/platform-sandbox-proxy", tags=["platform-sandbox-proxy"])


def _get_managed_platform(platform_id: int, auth: AuthContext) -> dict:
    platform = store_service.get_platform_by_id(platform_id)
    if platform is None:
        raise HTTPException(status_code=404, detail="平台不存在")
    if auth.role != "system_admin":
        assert auth.user is not None
        if not store_service.is_platform_admin(platform_id=platform_id, user_id=auth.user.user_id):
            raise HTTPException(status_code=403, detail="无权管理该平台")
    return platform


@router.get("/platform/{platform_id}")
def get_platform_sandbox_proxy_config(platform_id: int, auth: AuthContext = Depends(require_admin)) -> ApiResponse:
    _get_managed_platform(platform_id, auth)
    summary = platform_sandbox_proxy_service.get_summary(platform_id)
    return ApiResponse(message="平台 sandbox 代理配置", data=summary.model_dump(mode="json"))


@router.put("/platform/{platform_id}")
async def update_platform_sandbox_proxy_config(
    platform_id: int,
    request: PlatformSandboxProxyConfigUpdateRequest,
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    _get_managed_platform(platform_id, auth)
    summary = platform_sandbox_proxy_service.update_config(platform_id, request)
    recycled = await session_runtime_service.collect_platform_runtimes(platform_id, reason="platform_sandbox_proxy_updated")
    return ApiResponse(
        message="平台 sandbox 代理配置已更新",
        data={
            **summary.model_dump(mode="json"),
            "recycled_runtime_count": recycled,
        },
    )


@router.delete("/platform/{platform_id}")
async def delete_platform_sandbox_proxy_config(
    platform_id: int,
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    _get_managed_platform(platform_id, auth)
    summary = platform_sandbox_proxy_service.clear_config(platform_id)
    recycled = await session_runtime_service.collect_platform_runtimes(platform_id, reason="platform_sandbox_proxy_cleared")
    return ApiResponse(
        message="平台 sandbox 代理配置已删除",
        data={
            **summary.model_dump(mode="json"),
            "recycled_runtime_count": recycled,
        },
    )
