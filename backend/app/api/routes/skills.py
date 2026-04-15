# backend/app/api/routes/skills.py
from fastapi import APIRouter, File, Form, UploadFile

from app.schemas.common import ApiResponse
from app.services.session_service import session_service
from app.services.skill_service import skill_service

router = APIRouter(prefix="/api/v1/agent/skills", tags=["skills"])


@router.get("")
def list_skills(session_id: str | None = None) -> ApiResponse:
    """列出内置技能、宿主注入技能与上传技能。"""

    session = session_service.get_or_create(session_id)
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
) -> ApiResponse:
    """上传并安装用户自定义技能。"""

    session = session_service.get_or_create(session_id)
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
