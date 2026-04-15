# backend/app/api/routes/health.py
from fastapi import APIRouter

from app.sandbox.runner import sandbox_runner
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/api/v1/health", tags=["health"])


@router.get("")
async def health() -> ApiResponse:
    """健康检查。"""

    return ApiResponse(
        message="AetherCore backend is running",
        data={"sandbox": await sandbox_runner.check_status()},
    )
