import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import AuthContext, require_admin
from app.schemas.common import ApiResponse
from app.schemas.session import SessionSummary
from app.services.artifact_service import artifact_service
from app.services.file_service import file_service
from app.services.runtime_state import runtime_state_service
from app.services.session_service import session_service
from app.services.skill_service import skill_service
from app.services.store import store_service

router = APIRouter(prefix="/api/v1/admin/conversations", tags=["admin-conversations"])


def _managed_platform_ids(auth: AuthContext) -> list[int]:
    if auth.kind != "user" or auth.user is None:
        raise HTTPException(status_code=401, detail="未授权")
    if auth.role == "system_admin":
        return [int(item["platform_id"]) for item in store_service.list_platforms()]
    return store_service.list_managed_platform_ids(auth.user.user_id)


def _ensure_admin_conversation_access(conversation: dict, auth: AuthContext) -> None:
    if auth.kind != "user" or auth.user is None:
        raise HTTPException(status_code=401, detail="未授权")
    if auth.role == "system_admin":
        return
    platform_id = conversation.get("platform_id")
    if platform_id is None or not store_service.is_platform_admin(platform_id=int(platform_id), user_id=auth.user.user_id):
        raise HTTPException(status_code=403, detail="无权审计该会话")


def _inflate_conversation_summary(row: dict) -> dict:
    metadata = json.loads(row.get("metadata_json") or "{}")
    platform = store_service.get_platform_by_id(int(row["platform_id"])) if row.get("platform_id") else None
    owner = store_service.get_user_by_id(int(row["owner_user_id"])) if row.get("owner_user_id") else None
    return {
        "conversation_id": row["conversation_id"],
        "session_id": row["session_id"],
        "title": row.get("title") or "新对话",
        "host_name": row.get("host_name") or "",
        "platform_id": row.get("platform_id"),
        "platform_display_name": platform["display_name"] if platform else None,
        "owner_user_id": row.get("owner_user_id"),
        "owner_user_name": owner.full_name if owner else metadata.get("owner_name"),
        "external_user_id": row.get("external_user_id"),
        "external_user_name": metadata.get("external_user_name"),
        "external_org_id": row.get("external_org_id"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "last_message_at": row.get("last_message_at"),
        "message_count": int(row.get("message_count") or 0),
        "conversation_key": row.get("conversation_key"),
    }


@router.get("")
def list_admin_conversations(
    platform_id: int | None = Query(default=None),
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    managed_platform_ids = _managed_platform_ids(auth)
    if auth.role != "system_admin" and not managed_platform_ids:
        return ApiResponse(message="审计会话列表", data=[])

    if platform_id is not None:
        if auth.role != "system_admin" and platform_id not in managed_platform_ids:
            raise HTTPException(status_code=403, detail="无权查看该平台会话")
        rows = store_service.list_conversations_for_platform_ids([platform_id])
    elif auth.role == "system_admin":
        rows = store_service.list_all_conversations()
    else:
        rows = store_service.list_conversations_for_platform_ids(managed_platform_ids)

    items = [_inflate_conversation_summary(row) for row in rows]
    return ApiResponse(message="审计会话列表", data=items)


@router.get("/{session_id}")
def get_admin_conversation_detail(
    session_id: str,
    auth: AuthContext = Depends(require_admin),
) -> ApiResponse:
    conversation = store_service.get_conversation_by_session(session_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    _ensure_admin_conversation_access(conversation, auth)

    session = session_service.get_or_create(session_id)
    session.conversation_id = conversation.get("conversation_id")
    summary = SessionSummary(
        session_id=session.session_id,
        conversation_id=session.conversation_id,
        title=conversation.get("title") or "新对话",
        host_name=session.host_name,
        message_count=len(session.messages),
        allow_network=session.allow_network,
        created_at=datetime.fromisoformat(conversation["created_at"]) if conversation.get("created_at") else datetime.now(timezone.utc),
        skills=skill_service.list_for_session(session),
        files=[*file_service.list_uploads(session), *artifact_service.list_artifacts(session)],
        messages=session.messages,
        context_state=session.context_state,
        runtime=store_service.get_session_runtime(session.session_id),
        workboard=runtime_state_service.get_workboard(session),
        elicitation=runtime_state_service.get_elicitation(session),
    )
    payload = summary.model_dump(mode="json")
    payload["audit"] = _inflate_conversation_summary(conversation)
    return ApiResponse(message="审计会话详情", data=payload)
