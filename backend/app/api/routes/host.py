# backend/app/api/routes/host.py
import traceback
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_platform_secret
from app.core.config import settings
from app.host.registry import host_registry
from app.schemas.common import ApiResponse
from app.schemas.host import HostBindRequest

router = APIRouter(prefix="/api/v1/host", tags=["host"])


@router.post("/bind")
def bind_host(
    request: HostBindRequest,
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    """宿主平台绑定会话并注入能力。"""
    if platform["platform_key"] != request.platform_key:
        raise HTTPException(status_code=403, detail="平台密钥与目标平台不匹配")
    try:
        summary = host_registry.bind(request, platform=platform)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        print(f"[host/bind] RuntimeError: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"运行时错误: {str(exc)}") from exc
    except FileNotFoundError as exc:
        print(f"[host/bind] FileNotFoundError: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"文件不存在: {str(exc)}") from exc
    except Exception as exc:
        print(f"[host/bind] Unexpected error: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"绑定失败: {type(exc).__name__}: {str(exc)}") from exc
    summary["workbench_url"] = (
        f"{settings.resolved_manage_frontend_public_base_url}"
        f"?embed_token={summary['token']}&session_id={summary['session_id']}"
    )
    return ApiResponse(message="宿主绑定成功", data=summary)
