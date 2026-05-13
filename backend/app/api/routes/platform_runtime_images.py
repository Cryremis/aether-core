from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import AuthContext, require_admin
from app.schemas.common import ApiResponse
from app.schemas.platform import PlatformRuntimeImageUpdateRequest
from app.services.platform_runtime_image_service import platform_runtime_image_service
from app.services.session_runtime_service import session_runtime_service
from app.services.store import store_service

router = APIRouter(prefix="/api/v1/platform-runtime-images", tags=["platform-runtime-images"])


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
def get_platform_runtime_image(platform_id: int, auth: AuthContext = Depends(require_admin)) -> ApiResponse:
    _get_managed_platform(platform_id, auth)
    summary = platform_runtime_image_service.get_summary(platform_id)
    return ApiResponse(message="平台运行镜像配置", data=summary.model_dump(mode="json"))


@router.get("/platform/{platform_id}/guide")
def get_platform_runtime_image_guide(platform_id: int, auth: AuthContext = Depends(require_admin)) -> ApiResponse:
    _get_managed_platform(platform_id, auth)
    guide = platform_runtime_image_service.get_guide(platform_id)
    return ApiResponse(message="平台运行镜像构建规范", data=guide.model_dump(mode="json"))


@router.put("/platform/{platform_id}")
async def update_platform_runtime_image(
    platform_id: int,
    request: PlatformRuntimeImageUpdateRequest,
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    _get_managed_platform(platform_id, auth)
    summary = platform_runtime_image_service.update_image(platform_id, request.image)
    recycled = await session_runtime_service.collect_platform_runtimes(platform_id, reason="platform_image_updated")
    return ApiResponse(
        message="平台运行镜像已更新",
        data={
            **summary.model_dump(mode="json"),
            "recycled_runtime_count": recycled,
        },
    )


@router.post("/platform/{platform_id}/upload")
async def upload_platform_runtime_image(
    platform_id: int,
    image_file: UploadFile = File(...),
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    _get_managed_platform(platform_id, auth)
    previous = platform_runtime_image_service.get_summary(platform_id)
    summary = await platform_runtime_image_service.publish_uploaded_image(platform_id, image_file)
    recycled = await session_runtime_service.collect_platform_runtimes(platform_id, reason="platform_image_uploaded")
    if previous.custom_image:
        await platform_runtime_image_service.cleanup_replaced_image(previous.custom_image, keep_image=summary.resolved_image)
    return ApiResponse(
        message="平台镜像上传并启用成功",
        data={
            **summary.model_dump(mode="json"),
            "recycled_runtime_count": recycled,
        },
    )


@router.delete("/platform/{platform_id}")
async def clear_platform_runtime_image(platform_id: int, auth: AuthContext = Depends(require_admin)) -> ApiResponse:
    _get_managed_platform(platform_id, auth)
    summary = platform_runtime_image_service.clear_image(platform_id)
    recycled = await session_runtime_service.collect_platform_runtimes(platform_id, reason="platform_image_cleared")
    return ApiResponse(
        message="平台运行镜像覆盖已清除",
        data={
            **summary.model_dump(mode="json"),
            "recycled_runtime_count": recycled,
        },
    )
