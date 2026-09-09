import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.deps import AuthContext, get_auth_context, require_authenticated_user
from app.schemas.common import ApiResponse
from app.services.extension_store_service import extension_store_service
from app.services.store import store_service
from app.services.session_service import session_service
from app.api.routes.capabilities import _identity, _platform, _session

router = APIRouter(prefix="/api/v1/extensions", tags=["extensions"])


@router.get("")
def list_extensions(auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    if auth.kind not in {"user", "embed"}:
        raise HTTPException(status_code=401, detail="需要登录")
    return ApiResponse(message="拓展市场", data=extension_store_service.list_entries())


@router.post("")
async def publish_extension(kind: str = Form(...), name: str = Form(...), description: str = Form(""), version: str = Form("1.0.0"), artifact: UploadFile = File(...), auth: AuthContext = Depends(require_authenticated_user)) -> ApiResponse:
    try:
        item = extension_store_service.publish(kind=kind, name=name, description=description, version=version, submitter_user_id=auth.user.user_id, raw_bytes=await artifact.read(), filename=artifact.filename or "artifact.bin")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(message="拓展已发布", data=item)


@router.post("/{entry_id}/install")
def install_extension(entry_id: str, scope: str, session_id: str | None = None, platform_id: int | None = None, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    if scope not in {"user", "session", "platform"}:
        raise HTTPException(status_code=400, detail="安装作用域必须是 user、session 或 platform")
    identity = _identity(auth)
    if scope == "session" and not session_id:
        raise HTTPException(status_code=400, detail="会话安装需要 session_id")
    if scope == "platform" and platform_id is None:
        raise HTTPException(status_code=400, detail="平台安装需要 platform_id")
    session = _session(session_id, auth) if scope == "session" and session_id else None
    platform = _platform(platform_id, auth) if scope == "platform" and platform_id is not None else None
    try:
        result = extension_store_service.install(entry_id, scope=scope, session=session, platform_key=platform["platform_key"] if platform else None, identity=identity)
        if session: session_service.persist(session)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(message="拓展已安装", data=result)
