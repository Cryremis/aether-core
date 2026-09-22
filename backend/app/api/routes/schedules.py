from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import AuthContext, get_auth_context
from app.schemas.common import ApiResponse
from app.schemas.schedule import (
    ScheduleCreateRequest,
    ScheduleListResponse,
    ScheduleRunView,
    ScheduleTaskView,
    ScheduleUpdateRequest,
)
from app.services.scheduling.errors import (
    ScheduleConflictError,
    ScheduleError,
    SchedulePermissionError,
)
from app.services.scheduling.models import ScheduleIdentity
from app.services.scheduling.scheduler import schedule_scheduler
from app.services.scheduling.service import schedule_service


router = APIRouter(prefix="/api/v1/agent/schedules", tags=["schedules"])


def _identity(auth: AuthContext) -> ScheduleIdentity:
    if auth.kind == "user":
        if auth.user is None:
            raise HTTPException(status_code=401, detail="未授权")
        return ScheduleIdentity(owner_user_id=auth.user.user_id)
    if auth.kind == "embed":
        if auth.platform_id is None or auth.external_user_id is None:
            raise HTTPException(status_code=401, detail="未授权")
        return ScheduleIdentity(
            platform_id=auth.platform_id,
            external_user_id=auth.external_user_id,
            external_org_id=auth.external_org_id,
        )
    raise HTTPException(status_code=401, detail="未授权")


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, SchedulePermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ScheduleConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ScheduleError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.get("")
def list_schedules(
    status: str | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    auth: AuthContext = Depends(get_auth_context),
) -> ScheduleListResponse:
    try:
        items, total = schedule_service.list(
            identity=_identity(auth),
            status=status,
            search=search,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return ScheduleListResponse(items=items, total=total)
    except Exception as exc:
        raise _error(exc) from exc


@router.post("")
def create_schedule(
    request: ScheduleCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> ApiResponse:
    try:
        task = schedule_service.create(
            request,
            identity=_identity(auth),
            created_via="ui",
            created_by_user_id=auth.user.user_id if auth.user else None,
        )
        return ApiResponse(message="定时任务已创建", data=task.model_dump(mode="json"))
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/health")
def scheduler_health(auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    _identity(auth)
    return ApiResponse(data=schedule_scheduler.health())


@router.get("/{task_id}")
def get_schedule(
    task_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> ScheduleTaskView:
    try:
        return schedule_service.get(task_id, identity=_identity(auth))
    except Exception as exc:
        raise _error(exc) from exc


@router.patch("/{task_id}")
def update_schedule(
    task_id: str,
    request: ScheduleUpdateRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> ScheduleTaskView:
    try:
        return schedule_service.update(
            task_id, request, identity=_identity(auth)
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{task_id}/pause")
def pause_schedule(
    task_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> ScheduleTaskView:
    try:
        return schedule_service.pause(task_id, identity=_identity(auth))
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{task_id}/resume")
def resume_schedule(
    task_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> ScheduleTaskView:
    try:
        return schedule_service.resume(task_id, identity=_identity(auth))
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{task_id}/approve")
def approve_schedule(
    task_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> ScheduleTaskView:
    try:
        return schedule_service.approve(task_id, identity=_identity(auth))
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{task_id}/reject")
def reject_schedule(
    task_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> ScheduleTaskView:
    try:
        return schedule_service.reject(task_id, identity=_identity(auth))
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{task_id}/run")
def run_schedule_now(
    task_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> ApiResponse:
    try:
        run = schedule_service.create_manual_run(
            task_id, identity=_identity(auth)
        )
        return ApiResponse(message="已创建手动执行", data=ScheduleRunView.model_validate(run).model_dump(mode="json"))
    except Exception as exc:
        raise _error(exc) from exc


@router.delete("/{task_id}")
def archive_schedule(
    task_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> ScheduleTaskView:
    try:
        return schedule_service.archive(task_id, identity=_identity(auth))
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/{task_id}/runs")
def list_schedule_runs(
    task_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    auth: AuthContext = Depends(get_auth_context),
) -> ApiResponse:
    try:
        runs = schedule_service.list_runs(
            task_id,
            identity=_identity(auth),
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return ApiResponse(
            data=[
                ScheduleRunView.model_validate(run).model_dump(mode="json")
                for run in runs
            ]
        )
    except Exception as exc:
        raise _error(exc) from exc
