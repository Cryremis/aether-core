# backend/app/api/routes/platforms.py
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import AuthContext, require_admin, require_platform_secret, require_system_admin
from app.schemas.common import ApiResponse
from app.schemas.platform import (
    EmbedBootstrapRequest,
    EmbedBootstrapResponse,
    PlatformAdminAssignRequest,
    PlatformCreateRequest,
    PlatformSummary,
)
from app.services.conversation_service import conversation_service
from app.services.store import store_service

router = APIRouter(prefix="/api/v1/platforms", tags=["platforms"])


@router.post("")
def create_platform(
    request: PlatformCreateRequest,
    auth: AuthContext = Depends(require_system_admin),
) -> ApiResponse:
    assert auth.user is not None
    owner_user_id = request.owner_user_id or auth.user.user_id
    owner = store_service.get_user_by_id(owner_user_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="负责人管理员不存在")
    row = store_service.create_platform(
        platform_key=request.platform_key,
        display_name=request.display_name,
        host_type=request.host_type,
        description=request.description,
        owner_user_id=owner_user_id,
    )
    return ApiResponse(
        message="平台注册成功",
        data=PlatformSummary(
            platform_id=row["platform_id"],
            platform_key=row["platform_key"],
            display_name=row["display_name"],
            host_type=row["host_type"],
            description=row["description"],
            owner_user_id=row["owner_user_id"],
            owner_name=owner.full_name,
            host_secret=row["host_secret"],
            created_at=row["created_at"],
        ).model_dump(mode="json"),
    )


@router.post("/{platform_id}/admins")
def assign_platform_admin(
    platform_id: int,
    request: PlatformAdminAssignRequest,
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    platform = next((item for item in store_service.list_platforms() if item["platform_id"] == platform_id), None)
    if platform is None:
        raise HTTPException(status_code=404, detail="平台不存在")
    if auth.role != "system_admin" and (auth.user is None or not store_service.is_platform_admin(platform_id=platform_id, user_id=auth.user.user_id)):
        raise HTTPException(status_code=403, detail="无权管理该平台")
    target_user = store_service.get_user_by_id(request.user_id)
    if target_user is None:
        raise HTTPException(status_code=404, detail="目标管理员不存在")
    store_service.add_platform_admin(platform_id=platform_id, user_id=request.user_id)
    return ApiResponse(message="平台管理员已更新")


@router.get("")
def list_platforms(auth: AuthContext = Depends(require_admin)) -> ApiResponse:
    items = []
    for row in store_service.list_platforms():
        if auth.role != "system_admin" and (auth.user is None or not store_service.is_platform_admin(platform_id=row["platform_id"], user_id=auth.user.user_id)):
            continue
        owner = store_service.get_user_by_id(row["owner_user_id"])
        items.append(
            PlatformSummary(
                platform_id=row["platform_id"],
                platform_key=row["platform_key"],
                display_name=row["display_name"],
                host_type=row["host_type"],
                description=row["description"],
                owner_user_id=row["owner_user_id"],
                owner_name=owner.full_name if owner else "未知管理员",
                host_secret=row["host_secret"],
                created_at=row["created_at"],
            ).model_dump(mode="json")
        )
    return ApiResponse(message="平台列表", data=items)


@router.post("/embed/bootstrap", response_model=EmbedBootstrapResponse)
def bootstrap_embed(
    request: EmbedBootstrapRequest,
    platform: dict = Depends(require_platform_secret),
) -> EmbedBootstrapResponse:
    if platform["platform_key"] != request.platform_key:
        raise HTTPException(status_code=403, detail="平台密钥与目标平台不匹配")
    session, embed_token = conversation_service.bootstrap_host_workbench(
        platform_key=request.platform_key,
        external_user_id=request.external_user_id,
        external_user_name=request.external_user_name,
        external_org_id=request.external_org_id,
        conversation_id=request.conversation_id,
        conversation_key=request.conversation_key,
        host_name=request.host_name,
        host_type=request.host_type,
    )
    return EmbedBootstrapResponse(
        conversation_id=session.conversation_id or "",
        session_id=session.session_id,
        embed_token=embed_token,
        host_name=session.host_name,
        host_type=session.host_type,
    )
