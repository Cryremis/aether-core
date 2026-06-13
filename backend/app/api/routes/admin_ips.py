from fastapi import APIRouter, Depends

from app.api.deps import AuthContext, require_system_admin
from app.schemas.common import ApiResponse
from app.services.system_network_service import system_network_service

router = APIRouter(prefix="/api/v1/admin/ips", tags=["admin-ips"])


@router.get("")
def get_system_network_snapshot(_auth: AuthContext = Depends(require_system_admin)) -> ApiResponse:
    snapshot = system_network_service.get_snapshot()
    return ApiResponse(message="服务器网络地址信息", data=snapshot)
