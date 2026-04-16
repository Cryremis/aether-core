# backend/app/api/routes/skills.py
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.deps import AuthContext, get_auth_context
from app.schemas.common import ApiResponse
from app.services.session_service import session_service
from app.services.skill_service import skill_service
from app.services.store import store_service

router = APIRouter(prefix="/api/v1/agent/skills", tags=["skills"])


def _ensure_session_access(session_id: str, auth: AuthContext):
    conversation = store_service.get_conversation_by_session(session_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if auth.kind == "admin":
        if auth.user is None or conversation.get("owner_user_id") != auth.user.user_id:
            raise HTTPException(status_code=403, detail="无权访问该会话")
    elif auth.kind == "embed":
        if (
            conversation.get("conversation_id") != auth.conversation_id
            or conversation.get("platform_id") != auth.platform_id
            or conversation.get("external_user_id") != auth.external_user_id
        ):
            raise HTTPException(status_code=403, detail="无权访问该会话")
    else:
        raise HTTPException(status_code=401, detail="未授权")
    return session_service.get_or_create(session_id)


@router.get("")
def list_skills(session_id: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    session = _ensure_session_access(session_id, auth)
    items = [item.model_dump(mode="json") for item in skill_service.list_for_session(session)]
    return ApiResponse(message="技能列表", data=items)


@router.post("/upload")
async def upload_skill(
    session_id: str,
    name: str = Form(...),
    description: str = Form(...),
    content: str | None = Form(default=None),
    allowed_tools: str = Form(default=""),
    tags: str = Form(default="upload"),
    skill_file: UploadFile | None = File(default=None),
    auth: AuthContext = Depends(get_auth_context),
) -> ApiResponse:
    session = _ensure_session_access(session_id, auth)
    raw_content = content or ""
    if skill_file is not None:
        raw_content = (await skill_file.read()).decode("utf-8", errors="replace")
    card = skill_service.install_skill_from_text(
        session=session,
        name=name,
        description=description,
        content=raw_content,
        allowed_tools=[item.strip() for item in allowed_tools.split(",") if item.strip()],
        tags=[item.strip() for item in tags.split(",") if item.strip()],
    )
    return ApiResponse(message="技能上传成功", data=card.model_dump(mode="json"))
