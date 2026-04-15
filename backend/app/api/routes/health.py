# backend/app/api/routes/health.py
from fastapi import APIRouter

from app.schemas.common import ApiResponse

router = APIRouter(prefix="/api/v1/health", tags=["health"])


@router.get("")
def health() -> ApiResponse:
    """健康检查。"""

    return ApiResponse(message="AetherCore backend is running")
