# backend/app/api/routes/sessions.py
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import AuthContext, can_access_embedded_conversation, embedded_organization, get_auth_context
from app.schemas.common import ApiResponse
from app.schemas.session import SessionSummary, WorkboardUpdateRequest
from app.services.runtime_state import runtime_state_service
from app.services.agent_run_service import agent_run_service
from app.services.cross_chat_service import cross_chat_service
from app.services.conversation_service import conversation_service
from app.services.file_service import file_service
from app.services.platform_baseline_service import platform_baseline_service
from app.services.session_service import session_service
from app.services.skill_service import skill_service
from app.services.subagent_service import subagent_service
from app.services.store import store_service
from app.services.transcript_service import transcript_service

router = APIRouter(prefix="/api/v1/agent/sessions", tags=["sessions"])


def _ensure_embed_baseline(auth: AuthContext, conversation: dict, session) -> None:
    if auth.kind != "embed":
        return
    platform_id = conversation.get("platform_id")
    if platform_id is None:
        return
    platform = store_service.get_platform_by_id(int(platform_id))
    if platform is None:
        return
    expected_root = str(platform_baseline_service.ensure_platform_root(platform["platform_key"]).resolve())
    if session.baseline_root != expected_root:
        platform_baseline_service.materialize_to_session(platform["platform_key"], session)


def _authorized_conversation(session_id: str, auth: AuthContext):
    conversation = store_service.get_conversation_by_session(session_id)
    if conversation is None or conversation.get("deleted_at") is not None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if auth.kind == "user":
        if auth.user is None or conversation.get("owner_user_id") != auth.user.user_id:
            raise HTTPException(status_code=403, detail="无权访问该会话")
    elif auth.kind == "embed":
        if not can_access_embedded_conversation(auth, conversation):
            raise HTTPException(status_code=403, detail="无权访问该会话")
    else:
        raise HTTPException(status_code=401, detail="未授权")
    return conversation


def _ensure_session_access(session_id: str, auth: AuthContext):
    conversation = _authorized_conversation(session_id, auth)
    session = session_service.get_or_create(session_id)
    session.conversation_id = conversation.get("conversation_id")
    _ensure_embed_baseline(auth, conversation, session)
    return conversation, session


@router.post("/bootstrap")
def bootstrap_session(
    session_id: str | None = None,
    auth: AuthContext = Depends(get_auth_context),
) -> ApiResponse:
    if auth.kind == "user":
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
        if not can_access_embedded_conversation(auth, source_conversation):
            raise HTTPException(status_code=403, detail="无权基于该会话创建新会话")
        source_session = session_service.get_or_create(source_conversation["session_id"])
        session = session_service.get_or_create()
        platform = store_service.get_platform_by_id(int(auth.platform_id))
        if platform is None:
            raise HTTPException(status_code=404, detail="target platform does not exist")
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
        platform_baseline_service.materialize_to_session(platform["platform_key"], session)
        session_service.clone_host_state(source_session, session)
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
    if auth.kind == "user" and auth.user is not None:
        items = [item.model_dump(mode="json") for item in conversation_service.list_for_user(auth.user)]
        return ApiResponse(message="历史会话", data=items)
    if auth.kind == "embed" and auth.platform_id is not None and auth.external_user_id is not None:
        items = [
            item.model_dump(mode="json")
            for item in conversation_service.list_for_host_user(
                platform_id=auth.platform_id,
                external_user_id=auth.external_user_id,
                external_org_id=embedded_organization(auth),
                match_organization=True,
            )
        ]
        return ApiResponse(message="历史会话", data=items)
    raise HTTPException(status_code=401, detail="未授权")


@router.get("/{session_id}")
def get_session_summary(session_id: str, auth: AuthContext = Depends(get_auth_context),
                        transcript_limit: Annotated[int | None, Query(ge=1, le=100)] = None) -> ApiResponse:
    conversation, session = _ensure_session_access(session_id, auth)
    cross_chat_inbox_cursor = cross_chat_service.inbox_cursor(conversation)
    # 先取事件游标，再读历史。两次读之间落盘的事件可以重放，消息 ID 去重。
    active_run = agent_run_service.get_active_run_view(session)
    # 兼容旧客户端，工作台明确选择有界的展示投影。
    page = transcript_service.workbench_page(session, limit=transcript_limit) if transcript_limit else None
    transcript = page["transcript"] if page is not None else transcript_service.build_session_transcript(session)
    summary = SessionSummary(
        session_id=session.session_id,
        workspace_id=session.workspace_id,
        conversation_id=session.conversation_id,
        title=conversation.get("title") or "新对话",
        host_name=session.host_name,
        message_count=page["transcript_page"]["total"] if page is not None else len(transcript),
        allow_network=session.allow_network,
        created_at=datetime.fromisoformat(conversation["created_at"]) if conversation.get("created_at") else datetime.now(timezone.utc),
        skills=skill_service.list_for_session(session),
        files=file_service.list_sidebar_files(session),
        messages=[] if page is not None else transcript_service.visible_session_messages(session),
        transcript=transcript,
        context_state=session.context_state,
        runtime=store_service.get_workspace_runtime(session.workspace_id) if session.workspace_id else None,
        workboard=runtime_state_service.get_workboard(session),
        elicitation=agent_run_service.get_answerable_elicitation(session),
        active_run=active_run,
        cross_chat_inbox_cursor=cross_chat_inbox_cursor,
        visibility=str(conversation.get("visibility") or "normal"),
        archived_at=conversation.get("archived_at"),
        pinned_at=conversation.get("pinned_at"),
        revision=int(conversation.get("revision") or 0),
        subagents=subagent_service.list_subagents(session),
    )
    data = summary.model_dump(mode="json")
    if page is not None:
        data.pop("messages")
        data["transcript_page"] = page["transcript_page"]
    return ApiResponse(message="会话摘要", data=data)


@router.get("/{session_id}/transcript")
def get_session_transcript(session_id: str, auth: AuthContext = Depends(get_auth_context),
                           before: str | None = Query(None, max_length=384),
                           limit: int = Query(40, ge=1, le=100)) -> ApiResponse:
    _authorized_conversation(session_id, auth)
    session = session_service.get_or_create(session_id)
    try:
        page = transcript_service.workbench_page(session, limit=limit, before=before)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail="聊天历史已变化，请重新加载会话") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="历史游标无效") from exc
    return ApiResponse(message="聊天历史", data=page)


@router.get("/{session_id}/transcript/{message_id}/tools/{block_id}")
def get_transcript_tool_detail(session_id: str, message_id: str, block_id: str,
                               auth: AuthContext = Depends(get_auth_context),
                               offset: int = Query(0, ge=0)) -> ApiResponse:
    _authorized_conversation(session_id, auth)
    session = session_service.get_or_create(session_id)
    detail = transcript_service.workbench_tool_detail(session, message_id, block_id, offset)
    if detail is None:
        raise HTTPException(status_code=404, detail="工具输出不存在")
    return ApiResponse(message="工具输出", data=detail)


@router.get("/{session_id}/subagents")
def list_session_subagents(session_id: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    _, session = _ensure_session_access(session_id, auth)
    return ApiResponse(message="子代理列表", data=subagent_service.list_subagents(session))


@router.post("/{session_id}/subagents/{subagent_id}/cancel")
async def cancel_subagent(
    session_id: str,
    subagent_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> ApiResponse:
    _, session = _ensure_session_access(session_id, auth)
    try:
        cancelled = await subagent_service.cancel(session, subagent_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ApiResponse(message="子代理停止流程已完成", data=cancelled)


@router.post("/{session_id}/subagents/{subagent_id}/destroy")
async def destroy_subagent(
    session_id: str,
    subagent_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> ApiResponse:
    _, session = _ensure_session_access(session_id, auth)
    try:
        destroyed = await subagent_service.destroy(session, subagent_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ApiResponse(message="子代理已销毁", data=destroyed)


@router.get("/{session_id}/workboard")
def get_session_workboard(session_id: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    _, session = _ensure_session_access(session_id, auth)
    workboard = runtime_state_service.get_workboard(session)
    return ApiResponse(message="任务清单", data=workboard.model_dump(mode="json"))


@router.patch("/{session_id}/workboard")
def update_session_workboard(
    session_id: str,
    payload: WorkboardUpdateRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> ApiResponse:
    _, session = _ensure_session_access(session_id, auth)
    updated = runtime_state_service.update_workboard(session, payload.model_dump(exclude_none=True))
    return ApiResponse(message="任务清单已更新", data=updated.model_dump(mode="json"))


@router.delete("/{session_id}")
def delete_session(session_id: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    conversation = store_service.get_conversation_by_session(session_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if auth.kind == "user":
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
    if auth.kind == "user":
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
