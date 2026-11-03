import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zipfile import BadZipFile
from pydantic import BaseModel

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.deps import AuthContext, get_auth_context
from app.schemas.common import ApiResponse
from app.services.extension_store_service import extension_store_service
from app.services.skill_loader import SkillValidationError
from app.services.skill_preview import preview_artifact
from app.services.store import store_service
from app.services.session_service import session_service
from app.api.routes.capabilities import _identity, _platform, _session, _session_record


def _context(auth, session_id=None, platform_id=None, *, readonly=False):
    _identity(auth)
    if session_id:
        if readonly:
            bound_platform = session_service.read_skill_context(_session_record(session_id, auth)).platform_id
        else:
            bound_platform = _session(session_id, auth).platform_id
        if bound_platform is None:
            raise HTTPException(status_code=400, detail="会话没有宿主平台")
        if platform_id is not None and platform_id != bound_platform:
            raise HTTPException(status_code=403, detail="会话与目标市场平台不匹配")
        return bound_platform
    if auth.kind == "embed":
        return auth.platform_id
    if platform_id is not None:
        return _platform(platform_id, auth)["platform_id"]
    _identity(auth)
    return store_service.get_platform_by_key("standalone")["platform_id"]


def _author(auth):
    return f"user:{auth.user.user_id}" if auth.user else f"embed:{auth.platform_id}:{auth.external_user_id}"


def _public_entry(item: dict) -> dict:
    fields = ("entry_id", "kind", "name", "description", "submitter_user_id", "submitter_name", "submitter_account",
              "current_version", "usage_count", "updated_at", "created_at", "maintenance_policy", "revision", "status", "version")
    data = {key: item[key] for key in fields if key in item}
    if "versions" in item:
        data["versions"] = [{"version": row["version"], "created_at": row["created_at"]} for row in item["versions"]]
    return data

router = APIRouter(prefix="/api/v1/extensions", tags=["extensions"])


class ExtensionManageRequest(BaseModel):
    expected_revision: int
    maintenance_policy: Literal["creator_only", "platform_members"] | None = None
    status: Literal["active", "unlisted"] | None = None


@router.patch("/{entry_id}")
def manage_extension(entry_id: str, request: ExtensionManageRequest, session_id: str | None = None, platform_id: int | None = None, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    try:
        item = extension_store_service.manage(entry_id, _context(auth, session_id, platform_id), _author(auth), **request.model_dump())
        return ApiResponse(message="拓展已更新", data=_public_entry(item))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("")
def list_extensions(session_id: str | None = None, platform_id: int | None = None, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    if auth.kind not in {"user", "embed"}:
        raise HTTPException(status_code=401, detail="需要登录")
    return ApiResponse(message="拓展市场", data=[_public_entry(item) for item in extension_store_service.list_entries(_context(auth, session_id, platform_id))])


@router.post("")
async def publish_extension(kind: str = Form(...), name: str = Form(...), description: str = Form(""), version: str = Form("1.0.0"), artifact: UploadFile = File(...), session_id: str | None = None, platform_id: int | None = None, maintenance_policy: str | None = Form(None), entry_id: str | None = Form(None), expected_revision: int | None = Form(None), base_version: str | None = Form(None), idempotency_key: str | None = Form(None), auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    try:
        item = extension_store_service.publish(kind=kind, name=name, description=description, version=version, submitter_user_id=auth.user.user_id if auth.user else None, platform_id=_context(auth, session_id, platform_id), author_key=_author(auth), maintenance_policy=maintenance_policy, entry_id=entry_id, expected_revision=expected_revision, base_version=base_version, idempotency_key=idempotency_key, raw_bytes=await artifact.read(), filename=artifact.filename or "artifact.bin")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(message="拓展已发布", data=_public_entry(item))


@router.post("/{entry_id}/install")
def install_extension(entry_id: str, scope: str, session_id: str | None = None, platform_id: int | None = None,
                      version: str | None = None, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
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
        result = extension_store_service.install(entry_id, scope=scope, session=session, platform_key=platform["platform_key"] if platform else None, identity=identity, platform_id=_context(auth, session_id, platform_id), version=version)
        if session: session_service.persist(session)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(message="拓展已安装", data=result)


@router.get("/{entry_id}")
def inspect_extension(entry_id: str, session_id: str | None = None, platform_id: int | None = None, version: str | None = None, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    try:
        item = extension_store_service.inspect(entry_id, _context(auth, session_id, platform_id, readonly=True), version)
        return ApiResponse(message="拓展详情", data=_public_entry(item))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{entry_id}/preview")
def preview_extension(entry_id: str, session_id: str | None = None, platform_id: int | None = None,
                      version: str | None = None, file_path: str | None = None,
                      auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    try:
        item = extension_store_service.inspect(entry_id, _context(auth, session_id, platform_id, readonly=True), version)
        artifact = Path(item["artifact_path"])
        if item["kind"] == "skill":
            preview = preview_artifact(artifact, file_path)
        else:
            config = json.loads(artifact.read_text(encoding="utf-8"))
            if "mcpServers" in config:
                config = next(iter(config["mcpServers"].values()))
            url = urlsplit(config.get("url") or "")
            preview = {"mcp": {"transport": config.get("transport") or ("streamable_http" if url.hostname else "stdio"),
                               "auth": config.get("auth", "none"), "hostname": url.hostname}}
        data = {**_public_entry(item), **preview} if file_path is None else preview
        return ApiResponse(message="拓展预览", data=data)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="拓展或文件不存在") from exc
    except (SkillValidationError, BadZipFile, ValueError) as exc:
        raise HTTPException(status_code=400, detail="拓展包格式无效，无法预览") from exc
