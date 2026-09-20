from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import AuthContext, require_admin
from app.schemas.common import ApiResponse
from app.services.workspace_runtime_service import workspace_runtime_service
from app.services.store import store_service

router = APIRouter(prefix="/api/v1/admin/runtimes", tags=["runtimes"])

ACTIVE_RUNTIME_STATUSES = {"provisioning", "running", "executing"}


def _can_manage_runtime(runtime: dict, auth: AuthContext) -> bool:
    if auth.kind != "user" or auth.user is None:
        return False
    if auth.role == "system_admin":
        return True
    platform_id = runtime.get("platform_id")
    if platform_id is None:
        return False
    return store_service.is_platform_admin(platform_id=int(platform_id), user_id=auth.user.user_id)


@router.get("")
async def list_runtimes(
    include_inactive: bool = Query(default=False),
    _auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    items = await workspace_runtime_service.list_runtimes(refresh=True)
    visible = [item for item in items if _can_manage_runtime(item, _auth)]
    if not include_inactive:
        visible = [item for item in visible if item.get("status") in ACTIVE_RUNTIME_STATUSES]
    return ApiResponse(message="Workspace runtime 列表", data=visible)


@router.post("/{workspace_id}/collect")
async def collect_runtime(workspace_id: str, _auth: AuthContext = Depends(require_admin)) -> ApiResponse:
    runtime = store_service.get_workspace_runtime(workspace_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="目标 runtime 不存在")
    if not _can_manage_runtime(runtime, _auth):
        raise HTTPException(status_code=403, detail="无权管理该 runtime")
    if runtime.get("status") not in ACTIVE_RUNTIME_STATUSES:
        return ApiResponse(message="runtime 当前已关闭", data=runtime)
    updated = await workspace_runtime_service.collect_runtime(workspace_id, reason="admin_collected")
    return ApiResponse(message="runtime 已回收", data=updated or {"workspace_id": workspace_id, "status": "missing"})
