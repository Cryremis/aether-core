from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import AuthContext, require_system_admin
from app.schemas.common import ApiResponse
from app.services.system_network_service import system_network_service

router = APIRouter(prefix="/api/v1/admin/ips", tags=["admin-ips"])


@router.get("")
def get_system_network_snapshot(_auth: AuthContext = Depends(require_system_admin)) -> ApiResponse:
    snapshot = system_network_service.get_snapshot()
    return ApiResponse(message="服务器网络地址信息", data=snapshot)


@router.post("/routes/80")
def add_route_for_80_network(
    _auth: AuthContext = Depends(require_system_admin),
) -> ApiResponse:
    try:
        result = system_network_service.apply_route_for_80_network()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(message="80 网段静态路由已执行", data=result)
