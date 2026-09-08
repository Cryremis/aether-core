from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import AuthContext, get_auth_context
from app.schemas.common import ApiResponse
from app.services.capability_service import CapabilityValidationError, capability_service
from app.services.session_service import session_service
from app.services.skill_service import skill_service

router = APIRouter(prefix="/api/v1/capabilities", tags=["capabilities"])


def _identity(auth: AuthContext) -> dict:
    if auth.kind == "user" and auth.user:
        return {"user_id": auth.user.user_id}
    if auth.kind == "embed" and auth.platform_id and auth.external_user_id:
        return {"platform_id": auth.platform_id, "external_user_id": auth.external_user_id}
    raise HTTPException(status_code=401, detail="需要登录")


class McpConfigRequest(BaseModel):
    name: str
    transport: str
    command: list[str] = Field(default_factory=list)
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
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
        session = session_service.get_or_create(session_id)
        session_skills = [item.model_dump(mode="json") for item in skill_service.list_for_session(session)]
    return ApiResponse(message="能力列表", data={"skills": user_skills, "mcp": user_mcp, "session_skills": session_skills, "preference_namespace": f"aethercore-capabilities:{auth.kind}:{auth.platform_id or auth.user.user_id}"})


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
def add_user_mcp(request: McpConfigRequest, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    try:
        item = capability_service.upsert_mcp(request.model_dump(), **_identity(auth))
    except CapabilityValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(message="MCP 配置已保存", data=item)


@router.delete("/mcp/{name}")
def delete_user_mcp(name: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    if not capability_service.delete_mcp(name, **_identity(auth)):
        raise HTTPException(status_code=404, detail="MCP 不存在")
    return ApiResponse(message="MCP 配置已删除", data={"name": name})
