# backend/app/api/routes/sessions.py
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import AuthContext, get_auth_context
from app.schemas.common import ApiResponse
from app.schemas.session import SessionSummary
from app.services.artifact_service import artifact_service
from app.services.conversation_service import conversation_service
from app.services.file_service import file_service
from app.services.session_service import session_service
from app.services.skill_service import skill_service
from app.services.store import store_service

router = APIRouter(prefix="/api/v1/agent/sessions", tags=["sessions"])


@router.post("/bootstrap")
def bootstrap_session(
    session_id: str | None = None,
    auth: AuthContext = Depends(get_auth_context),
) -> ApiResponse:
    if auth.kind == "admin":
        assert auth.user is not None
        try:
            session = conversation_service.bootstrap_admin_workbench(auth.user, session_id=session_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    elif auth.kind == "embed":
        if auth.platform_id is None or auth.external_user_id is None or auth.conversation_id is None:
            raise HTTPException(status_code=401, detail="未授权")
        source_conversation = (
            store_service.get_conversation_by_session(session_id)
            if session_id
            else store_service.get_conversation(auth.conversation_id)
        )
        if source_conversation is None:
            raise HTTPException(status_code=404, detail="来源会话不存在")
        if (
            source_conversation.get("platform_id") != auth.platform_id
            or source_conversation.get("external_user_id") != auth.external_user_id
        ):
            raise HTTPException(status_code=403, detail="无权基于该会话创建新会话")
        source_session = session_service.get_or_create(source_conversation["session_id"])
        session = session_service.get_or_create()
        conversation = store_service.create_conversation(
            session_id=session.session_id,
            title="新对话",
            host_name=source_session.host_name,
            platform_id=auth.platform_id,
            external_user_id=auth.external_user_id,
            external_org_id=source_conversation.get("external_org_id"),
            conversation_key=None,
            metadata={},
        )
        session_service.attach_host(
            session=session,
            host_name=source_session.host_name,
            context=dict(source_session.host_context),
            tools=[dict(item) for item in source_session.host_tools],
            skills=[dict(item) for item in source_session.host_skills],
            apis=[dict(item) for item in source_session.host_apis],
        )
        session.conversation_id = conversation["conversation_id"]
        session.allow_network = source_session.allow_network
        session_service.persist(session)
    else:
        raise HTTPException(status_code=401, detail="未授权")

    return ApiResponse(
        message="工作台会话已就绪",
        data={
            "session_id": session.session_id,
            "conversation_id": session.conversation_id,
            "host_name": session.host_name,
        },
    )


@router.get("")
def list_sessions(auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    if auth.kind == "admin" and auth.user is not None:
        items = [item.model_dump(mode="json") for item in conversation_service.list_for_admin(auth.user)]
        return ApiResponse(message="历史会话", data=items)
    if auth.kind == "embed" and auth.platform_id is not None and auth.external_user_id is not None:
        items = [
            item.model_dump(mode="json")
            for item in conversation_service.list_for_host_user(
                platform_id=auth.platform_id,
                external_user_id=auth.external_user_id,
            )
        ]
        return ApiResponse(message="历史会话", data=items)
    raise HTTPException(status_code=401, detail="未授权")


@router.get("/{session_id}")
def get_session_summary(session_id: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    conversation = store_service.get_conversation_by_session(session_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if auth.kind == "admin":
        if auth.user is None or conversation.get("owner_user_id") != auth.user.user_id:
            raise HTTPException(status_code=403, detail="无权访问该会话")
    elif auth.kind == "embed":
        if (
            conversation.get("platform_id") != auth.platform_id
            or conversation.get("external_user_id") != auth.external_user_id
        ):
            raise HTTPException(status_code=403, detail="无权访问该会话")
    else:
        raise HTTPException(status_code=401, detail="未授权")

    session = session_service.get_or_create(session_id)
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
    )
    return ApiResponse(message="会话摘要", data=summary.model_dump(mode="json"))


@router.delete("/{session_id}")
def delete_session(session_id: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    conversation = store_service.get_conversation_by_session(session_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if auth.kind == "admin":
        if auth.user is None or conversation.get("owner_user_id") != auth.user.user_id:
            raise HTTPException(status_code=403, detail="无权删除该会话")
    elif auth.kind == "embed":
        if (
            conversation.get("platform_id") != auth.platform_id
            or conversation.get("external_user_id") != auth.external_user_id
        ):
            raise HTTPException(status_code=403, detail="无权删除该会话")
    else:
        raise HTTPException(status_code=401, detail="未授权")
    deleted = store_service.delete_conversation(session_id)
    if deleted:
        session_service.delete_session(session_id)
    return ApiResponse(message="会话已删除", data={"deleted": deleted})


@router.patch("/{session_id}/title")
def rename_session(session_id: str, title: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    conversation = store_service.get_conversation_by_session(session_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if auth.kind == "admin":
        if auth.user is None or conversation.get("owner_user_id") != auth.user.user_id:
            raise HTTPException(status_code=403, detail="无权重命名该会话")
    elif auth.kind == "embed":
        if (
            conversation.get("platform_id") != auth.platform_id
            or conversation.get("external_user_id") != auth.external_user_id
        ):
            raise HTTPException(status_code=403, detail="无权重命名该会话")
    else:
        raise HTTPException(status_code=401, detail="未授权")
    updated = store_service.update_conversation_title(session_id, title.strip()[:80] or "新对话")
    return ApiResponse(message="会话已重命名", data={"updated": updated, "title": title.strip()[:80] or "新对话"})
