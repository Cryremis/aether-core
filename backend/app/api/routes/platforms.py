# backend/app/api/routes/platforms.py
import sqlite3

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import AuthContext, require_admin, require_platform_secret, require_system_admin
from app.schemas.common import ApiResponse
from app.schemas.platform import (
    EmbedBootstrapRequest,
    EmbedBootstrapResponse,
    PlatformBaselineDirectoryRequest,
    PlatformBaselineMoveRequest,
    PlatformAdminAssignRequest,
    PlatformBaselineWriteRequest,
    PlatformCreateRequest,
    PlatformSummary,
)
from app.services.conversation_service import conversation_service
from app.services.platform_baseline_service import platform_baseline_service
from app.services.store import store_service

router = APIRouter(prefix="/api/v1/platforms", tags=["platforms"])


def _get_managed_platform(platform_id: int, auth: AuthContext) -> dict:
    platform = next((item for item in store_service.list_platforms() if item["platform_id"] == platform_id), None)
    if platform is None:
        raise HTTPException(status_code=404, detail="平台不存在")
    if auth.role != "system_admin":
        if auth.user is None or not store_service.is_platform_admin(platform_id=platform_id, user_id=auth.user.user_id):
            raise HTTPException(status_code=403, detail="无权管理该平台")
    return platform


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
    normalized_platform_key = request.platform_key.strip().lower()
    try:
        row = store_service.create_platform(
            platform_key=normalized_platform_key,
            display_name=request.display_name,
            host_type=request.host_type,
            description=request.description,
            owner_user_id=owner_user_id,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=f'platform_key "{normalized_platform_key}" 已存在') from exc
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
    _get_managed_platform(platform_id, auth)
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


@router.get("/{platform_id}/baseline")
def get_platform_baseline(
    platform_id: int,
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    platform = _get_managed_platform(platform_id, auth)
    summary = platform_baseline_service.list_summary(platform["platform_key"])
    return ApiResponse(message="平台基线环境", data=summary.model_dump(mode="json"))


@router.get("/{platform_id}/baseline/files/content")
def get_platform_baseline_file_content(
    platform_id: int,
    relative_path: str = Query(...),
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    platform = _get_managed_platform(platform_id, auth)
    try:
        content = platform_baseline_service.read_text(platform["platform_key"], relative_path=relative_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ApiResponse(message="平台基线文件内容", data=content.model_dump(mode="json"))


@router.post("/{platform_id}/baseline/files/text")
def write_platform_baseline_file(
    platform_id: int,
    request: PlatformBaselineWriteRequest,
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    platform = _get_managed_platform(platform_id, auth)
    try:
        item = platform_baseline_service.write_text(platform["platform_key"], request)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(message="平台基线文件已保存", data=item.model_dump(mode="json"))


@router.post("/{platform_id}/baseline/directories")
def create_platform_baseline_directory(
    platform_id: int,
    request: PlatformBaselineDirectoryRequest,
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    platform = _get_managed_platform(platform_id, auth)
    try:
        item = platform_baseline_service.create_directory(platform["platform_key"], request)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(message="平台基线目录已创建", data=item.model_dump(mode="json"))


@router.patch("/{platform_id}/baseline/paths")
def move_platform_baseline_path(
    platform_id: int,
    request: PlatformBaselineMoveRequest,
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    platform = _get_managed_platform(platform_id, auth)
    try:
        item = platform_baseline_service.move_path(platform["platform_key"], request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(message="平台基线路径已更新", data=item.model_dump(mode="json"))


@router.post("/{platform_id}/baseline/files")
async def upload_platform_baseline_file(
    platform_id: int,
    upload_file: UploadFile = File(...),
    target_relative_dir: str = Query(default="work"),
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    platform = _get_managed_platform(platform_id, auth)
    try:
        item = await platform_baseline_service.upload_file(
            platform["platform_key"],
            upload_file=upload_file,
            target_relative_dir=target_relative_dir,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(message="平台基线文件上传成功", data=item.model_dump(mode="json"))


@router.delete("/{platform_id}/baseline/files")
def delete_platform_baseline_file(
    platform_id: int,
    relative_path: str = Query(...),
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    platform = _get_managed_platform(platform_id, auth)
    try:
        platform_baseline_service.delete_file(platform["platform_key"], relative_path=relative_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(message="平台基线文件已删除")


@router.get("/{platform_id}/baseline/files/download")
def download_platform_baseline_file(
    platform_id: int,
    relative_path: str = Query(...),
    auth: AuthContext = Depends(require_admin),
) -> FileResponse:
    platform = _get_managed_platform(platform_id, auth)
    try:
        path = platform_baseline_service.resolve_file(platform["platform_key"], relative_path=relative_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path=path, filename=path.name)


@router.post("/{platform_id}/baseline/skills")
async def upload_platform_baseline_skill(
    platform_id: int,
    skill_file: UploadFile = File(...),
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    platform = _get_managed_platform(platform_id, auth)
    items = await platform_baseline_service.upload_skill(platform["platform_key"], upload_file=skill_file)
    return ApiResponse(
        message="平台基线技能上传成功",
        data={"items": [item.model_dump(mode="json") for item in items]},
    )


@router.delete("/{platform_id}/baseline/skills/{skill_name}")
def delete_platform_baseline_skill(
    platform_id: int,
    skill_name: str,
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    platform = _get_managed_platform(platform_id, auth)
    try:
        platform_baseline_service.delete_skill(platform["platform_key"], skill_name=skill_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ApiResponse(message="平台基线技能已删除")


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
