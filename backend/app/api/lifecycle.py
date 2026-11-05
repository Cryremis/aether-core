from __future__ import annotations

import hmac
import re

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from app.services.lifecycle_service import ServiceDraining, lifecycle_service


router = APIRouter(prefix="/api/v1/internal/lifecycle", include_in_schema=False)


class DrainRequest(BaseModel):
    owner: str = Field(min_length=16, max_length=128)


def authorize(token: str) -> None:
    if not hmac.compare_digest(token.encode("utf-8"), lifecycle_service.token.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Invalid lifecycle credential")


@router.get("")
async def status(x_aethercore_control: str = Header(default="")):
    authorize(x_aethercore_control)
    return lifecycle_service.snapshot()


@router.post("/drain")
async def drain(request: DrainRequest, x_aethercore_control: str = Header(default="")):
    authorize(x_aethercore_control)
    try:
        return lifecycle_service.begin_drain(request.owner)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/drain")
async def cancel_drain(request: DrainRequest, x_aethercore_control: str = Header(default="")):
    authorize(x_aethercore_control)
    try:
        lifecycle_service.cancel_drain(request.owner)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return lifecycle_service.snapshot()


class AdmissionMiddleware:
    """在路由产生任何写入前接纳请求；不阻塞事件回放和已运行任务的取消操作。"""

    _CANCEL_PATH = re.compile(r"^/api/v1/(?:agent/[^/]+/abort|host/runs/[^/]+/cancel)$")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}
                or scope["path"].startswith("/api/v1/internal/lifecycle")):
            await self.app(scope, receive, send)
            return
        try:
            with lifecycle_service.operation("requests", continuation=bool(self._CANCEL_PATH.fullmatch(scope["path"]))):
                await self.app(scope, receive, send)
        except ServiceDraining as exc:
            response = JSONResponse(
                {"detail": str(exc), "code": "service_restarting"},
                status_code=503,
                headers={"Retry-After": "3"},
            )
            await response(scope, receive, send)
