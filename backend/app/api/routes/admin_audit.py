from fastapi import APIRouter, Depends

from app.api.deps import AuthContext, require_system_admin
from app.schemas.admin_audit import PlatformAuditOverviewItem, SystemAuditOverview
from app.schemas.common import ApiResponse
from app.services.store import store_service

router = APIRouter(prefix="/api/v1/admin/audit", tags=["admin-audit"])


@router.get("/overview")
def get_system_audit_overview(_auth: AuthContext = Depends(require_system_admin)) -> ApiResponse:
    overview = store_service.get_system_audit_overview()
    payload = SystemAuditOverview(
        **{
            **overview,
            "platforms": [PlatformAuditOverviewItem(**item) for item in overview.get("platforms", [])],
        }
    )
    return ApiResponse(message="系统审计概览", data=payload.model_dump(mode="json"))
