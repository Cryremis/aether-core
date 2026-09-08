from typing import Literal
import hashlib
import html

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.api.deps import AuthContext, get_auth_context
from app.schemas.common import ApiResponse
from app.services.capability_service import CapabilityValidationError, capability_service
from app.services.session_service import session_service
from app.services.skill_service import skill_service
from app.services.store import store_service
from app.services.mcp_runtime_service import McpRuntimeError, mcp_runtime_service
from app.services.tool_catalog_service import tool_catalog_service
from app.services.secret_store_service import secret_store_service

router = APIRouter(prefix="/api/v1/capabilities", tags=["capabilities"])


def _identity(auth: AuthContext) -> dict:
    if auth.kind == "user" and auth.user:
        return {"user_id": auth.user.user_id}
    if auth.kind == "embed" and auth.platform_id and auth.external_user_id:
        return {"platform_id": auth.platform_id, "external_user_id": auth.external_user_id}
    raise HTTPException(status_code=401, detail="需要登录")


def _session(session_id: str, auth: AuthContext):
    conversation = store_service.get_conversation_by_session(session_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    if auth.kind == "user" and (not auth.user or conversation.get("owner_user_id") != auth.user.user_id):
        raise HTTPException(status_code=403, detail="无权访问该会话")
    if auth.kind == "embed" and (conversation.get("platform_id") != auth.platform_id or conversation.get("external_user_id") != auth.external_user_id):
        raise HTTPException(status_code=403, detail="无权访问该会话")
    return session_service.get_or_create(session_id)


def _platform(platform_id: int, auth: AuthContext) -> dict:
    platform = store_service.get_platform_by_id(platform_id)
    if not platform:
        raise HTTPException(status_code=404, detail="平台不存在")
    if auth.kind != "user" or not auth.user or (auth.role != "system_admin" and not store_service.is_platform_admin(platform_id=platform_id, user_id=auth.user.user_id)):
        raise HTTPException(status_code=403, detail="无权管理该平台")
    return platform


class McpConfigRequest(BaseModel):
    name: str
    transport: str
    command: list[str] = Field(default_factory=list)
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    auth: Literal["none", "oauth"] = "none"
    oauth_scopes: str = ""
    description: str = ""
    startup_timeout_sec: int = 10
    tool_timeout_sec: int = 60


@router.get("")
def list_capabilities(session_id: str | None = None, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    identity = _identity(auth)
    user_skills = capability_service.list_user_skills(**identity)
    user_mcp = capability_service.list_mcp(**identity)
    session_skills = []
    if session_id:
        session = _session(session_id, auth)
        session_skills = [item.model_dump(mode="json") for item in skill_service.list_for_session(session)]
        user_mcp = capability_service.list_effective_mcp(session)
        connected_sources = set(session.host_tool_collections)
        user_mcp = [dict(item, status="connected" if f"mcp:{item['id']}" in connected_sources else item.get("status", "configured")) for item in user_mcp]
    principal = str(auth.user.user_id) if auth.user else f"{auth.platform_id}:{auth.external_user_id}"
    namespace = hashlib.sha256(principal.encode("utf-8")).hexdigest()[:20]
    return ApiResponse(message="能力列表", data={"skills": user_skills, "mcp": user_mcp, "session_skills": session_skills, "preference_namespace": f"aethercore-capabilities:{auth.kind}:{namespace}"})


@router.get("/platform/{platform_id}")
def list_platform_capabilities(platform_id: int, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    platform = _platform(platform_id, auth)
    return ApiResponse(message="平台能力", data={"skills": platform_baseline_skills(platform["platform_key"]), "mcp": capability_service.list_platform_mcp(platform["platform_key"])})


def platform_baseline_skills(platform_key: str) -> list[dict]:
    from app.services.platform_baseline_service import platform_baseline_service
    return [item.model_dump(mode="json") for item in platform_baseline_service.list_skills(platform_key)]


@router.post("/skills")
async def upload_user_skill(skill_file: UploadFile = File(...), auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    try:
        items = capability_service.install_skill(raw_bytes=await skill_file.read(), filename=skill_file.filename or "SKILL.md", **_identity(auth))
    except (CapabilityValidationError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(message="用户技能已保存", data=items)


@router.delete("/skills/{name}")
def delete_user_skill(name: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    if not capability_service.delete_skill(name, **_identity(auth)):
        raise HTTPException(status_code=404, detail="技能不存在")
    return ApiResponse(message="用户技能已删除", data={"name": name})


@router.post("/mcp")
def add_user_mcp(
    request: McpConfigRequest,
    scope: Literal["user", "session", "platform"] = Query("user"),
    session_id: str | None = None,
    platform_id: int | None = None,
    auth: AuthContext = Depends(get_auth_context),
) -> ApiResponse:
    try:
        if scope == "session":
            if not session_id:
                raise CapabilityValidationError("会话级 MCP 需要 session_id")
            session = _session(session_id, auth)
            existing = next((entry for entry in session.session_mcp if entry["name"] == request.name), None)
            item = dict(capability_service._validate_mcp(request.model_dump(), existing=existing), scope="session")
            session.session_mcp = [entry for entry in session.session_mcp if entry["name"] != item["name"]] + [item]
            session_service.persist(session)
        elif scope == "platform":
            if platform_id is None:
                raise CapabilityValidationError("平台级 MCP 需要 platform_id")
            platform = _platform(platform_id, auth)
            item = capability_service.upsert_platform_mcp(platform["platform_key"], request.model_dump())
        else:
            item = capability_service.upsert_mcp(request.model_dump(), **_identity(auth))
    except CapabilityValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(message="MCP 配置已保存", data=item)


@router.delete("/mcp/{name}")
def delete_user_mcp(
    name: str,
    scope: Literal["user", "session", "platform"] = Query("user"),
    session_id: str | None = None,
    platform_id: int | None = None,
    auth: AuthContext = Depends(get_auth_context),
) -> ApiResponse:
    deleted = False
    if scope == "session" and session_id:
        session = _session(session_id, auth)
        removed = next((item for item in session.session_mcp if item["name"] == name), None)
        next_items = [item for item in session.session_mcp if item["name"] != name]
        deleted = len(next_items) != len(session.session_mcp)
        session.session_mcp = next_items
        session_service.persist(session)
        if removed:
            secret_store_service.delete(removed.get("secret_ref"))
    elif scope == "platform" and platform_id is not None:
        platform = _platform(platform_id, auth)
        deleted = capability_service.delete_platform_mcp(platform["platform_key"], name)
    elif scope == "user":
        deleted = capability_service.delete_mcp(name, **_identity(auth))
    if not deleted:
        raise HTTPException(status_code=404, detail="MCP 不存在")
    return ApiResponse(message="MCP 配置已删除", data={"name": name})


@router.post("/mcp/{name}/connect")
async def connect_mcp(name: str, session_id: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    session = _session(session_id, auth)
    config = next((item for item in capability_service.list_effective_mcp(session) if item["name"] == name), None)
    if not config:
        raise HTTPException(status_code=404, detail="MCP 不存在或已禁用")
    try:
        descriptors = await mcp_runtime_service.discover(session, config)
        mutation = tool_catalog_service.replace(session, descriptors, source_id=f"mcp:{config['id']}", replace_all=False)
        session_service.persist(session)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MCP 连接失败: {exc}") from exc
    return ApiResponse(message="MCP 已连接", data={"name": name, "status": "connected", "tool_count": len(descriptors), "catalog_revision": mutation.revision})


@router.post("/mcp/{name}/oauth/start")
async def start_mcp_oauth(name: str, session_id: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    session = _session(session_id, auth)
    config = next(
        (item for item in capability_service.list_effective_mcp(session, include_secrets=True) if item["name"] == name),
        None,
    )
    if not config:
        raise HTTPException(status_code=404, detail="MCP 不存在或已禁用")
    try:
        result = await mcp_runtime_service.begin_oauth(session, config)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"无法发起 OAuth: {exc}") from exc
    return ApiResponse(message="OAuth 授权已发起", data=result)


@router.get("/mcp/oauth/callback", response_class=HTMLResponse)
async def complete_mcp_oauth(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return HTMLResponse(f"<h1>授权失败</h1><p>{html.escape(error)}</p>", status_code=400)
    if not code or not state:
        return HTMLResponse("<h1>授权失败</h1><p>缺少 OAuth 回调参数。</p>", status_code=400)
    try:
        await mcp_runtime_service.complete_oauth(code=code, state=state)
    except Exception as exc:
        return HTMLResponse(f"<h1>授权失败</h1><p>{html.escape(str(exc)[:200])}</p>", status_code=400)
    return HTMLResponse("<h1>授权完成</h1><p>可以关闭此窗口并返回 AetherCore。</p><script>window.opener?.postMessage({type:'aethercore:mcp-oauth-complete'}, '*');window.close()</script>")


@router.delete("/mcp/{name}/oauth")
def revoke_mcp_oauth(name: str, session_id: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    session = _session(session_id, auth)
    config = next(
        (item for item in capability_service.list_effective_mcp(session, include_secrets=True) if item["name"] == name),
        None,
    )
    if not config:
        raise HTTPException(status_code=404, detail="MCP 不存在或已禁用")
    mcp_runtime_service.revoke_oauth(config)
    return ApiResponse(message="OAuth 凭据已删除", data={"name": name})


@router.post("/mcp/{name}/disconnect")
def disconnect_mcp(name: str, session_id: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    session = _session(session_id, auth)
    configs = [item for item in capability_service.list_effective_mcp(session) if item["name"] == name]
    for config in configs:
        tool_catalog_service.replace(session, [], source_id=f"mcp:{config['id']}", replace_all=False)
    session_service.persist(session)
    return ApiResponse(message="MCP 已断开", data={"name": name, "status": "configured"})
