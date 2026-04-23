# backend/app/api/routes/llm.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import AuthContext, require_admin
from app.schemas.common import ApiResponse
from app.schemas.llm import LlmConfigUpdateRequest
from app.services.llm_config_service import llm_config_service
from app.services.store import store_service

router = APIRouter(prefix="/api/v1/llm", tags=["llm"])


@router.get("/user")
def get_user_llm_config(auth: AuthContext = Depends(require_admin)) -> ApiResponse:
    assert auth.user is not None
    summary = llm_config_service.get_user_summary(auth.user.user_id)
    resolved = llm_config_service.resolve_summary_for_user(auth.user)
    return ApiResponse(
        message="用户 LLM 配置",
        data={
            "config": summary.model_dump(mode="json") if summary else None,
            "resolved": resolved.model_dump(mode="json"),
        },
    )


@router.put("/user")
def update_user_llm_config(
    request: LlmConfigUpdateRequest,
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    assert auth.user is not None
    summary = llm_config_service.update_user_config(auth.user, request)
    return ApiResponse(message="用户 LLM 配置已更新", data=summary.model_dump(mode="json"))


@router.delete("/user")
def delete_user_llm_config(auth: AuthContext = Depends(require_admin)) -> ApiResponse:
    assert auth.user is not None
    llm_config_service.delete_user_config(auth.user.user_id)
    return ApiResponse(message="用户 LLM 配置已删除")


@router.get("/platform/{platform_id}")
def get_platform_llm_config(platform_id: int, auth: AuthContext = Depends(require_admin)) -> ApiResponse:
    platform = next((item for item in store_service.list_platforms() if item["platform_id"] == platform_id), None)
    if platform is None:
        raise HTTPException(status_code=404, detail="平台不存在")
    if auth.role != "system_admin":
        assert auth.user is not None
        if not store_service.is_platform_admin(platform_id=platform_id, user_id=auth.user.user_id):
            raise HTTPException(status_code=403, detail="无权管理该平台")
    summary = llm_config_service.get_platform_summary(platform_id)
    return ApiResponse(message="平台 LLM 配置", data=summary.model_dump(mode="json") if summary else None)


@router.put("/platform/{platform_id}")
def update_platform_llm_config(
    platform_id: int,
    request: LlmConfigUpdateRequest,
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    platform = next((item for item in store_service.list_platforms() if item["platform_id"] == platform_id), None)
    if platform is None:
        raise HTTPException(status_code=404, detail="平台不存在")
    if auth.role != "system_admin":
        assert auth.user is not None
        if not store_service.is_platform_admin(platform_id=platform_id, user_id=auth.user.user_id):
            raise HTTPException(status_code=403, detail="无权管理该平台")
    summary = llm_config_service.update_platform_config(platform_id, request)
    return ApiResponse(message="平台 LLM 配置已更新", data=summary.model_dump(mode="json"))


@router.delete("/platform/{platform_id}")
def delete_platform_llm_config(platform_id: int, auth: AuthContext = Depends(require_admin)) -> ApiResponse:
    platform = next((item for item in store_service.list_platforms() if item["platform_id"] == platform_id), None)
    if platform is None:
        raise HTTPException(status_code=404, detail="平台不存在")
    if auth.role != "system_admin":
        assert auth.user is not None
        if not store_service.is_platform_admin(platform_id=platform_id, user_id=auth.user.user_id):
            raise HTTPException(status_code=403, detail="无权管理该平台")
    llm_config_service.delete_platform_config(platform_id)
    return ApiResponse(message="平台 LLM 配置已删除")
