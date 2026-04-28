from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import AuthContext, require_admin
from app.schemas.common import ApiResponse
from app.schemas.prompt import PlatformPromptConfigUpdateRequest
from app.services.prompt_service import prompt_service
from app.services.store import store_service

router = APIRouter(prefix="/api/v1/prompts", tags=["prompts"])


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
def get_platform_prompt_config(platform_id: int, auth: AuthContext = Depends(require_admin)) -> ApiResponse:
    _get_managed_platform(platform_id, auth)
    summary = prompt_service.get_platform_summary(platform_id)
    return ApiResponse(message="平台提示词配置", data=summary.model_dump(mode="json") if summary else None)


@router.put("/platform/{platform_id}")
def update_platform_prompt_config(
    platform_id: int,
    request: PlatformPromptConfigUpdateRequest,
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    _get_managed_platform(platform_id, auth)
    summary = prompt_service.update_platform_config(platform_id, request)
    return ApiResponse(message="平台提示词配置已更新", data=summary.model_dump(mode="json"))


@router.delete("/platform/{platform_id}")
def delete_platform_prompt_config(platform_id: int, auth: AuthContext = Depends(require_admin)) -> ApiResponse:
    _get_managed_platform(platform_id, auth)
    prompt_service.delete_platform_config(platform_id)
    return ApiResponse(message="平台提示词配置已删除")
