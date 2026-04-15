# backend/app/api/routes/sessions.py
from datetime import datetime, timezone

from fastapi import APIRouter

from app.schemas.common import ApiResponse
from app.schemas.session import SessionSummary
from app.services.artifact_service import artifact_service
from app.services.file_service import file_service
from app.services.session_service import session_service
from app.services.skill_service import skill_service

router = APIRouter(prefix="/api/v1/agent/sessions", tags=["sessions"])


@router.get("/{session_id}")
def get_session_summary(session_id: str) -> ApiResponse:
    """获取会话摘要。"""

    session = session_service.get_or_create(session_id)
    summary = SessionSummary(
        session_id=session.session_id,
        host_name=session.host_name,
        host_type=session.host_type,
        message_count=len(session.messages),
        created_at=datetime.fromtimestamp(session.created_at, tz=timezone.utc),
        skills=skill_service.list_for_session(session),
        files=[*file_service.list_uploads(session), *artifact_service.list_artifacts(session)],
    )
    return ApiResponse(message="会话摘要", data=summary.model_dump(mode="json"))
