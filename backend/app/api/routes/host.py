# backend/app/api/routes/host.py
import traceback
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse

from app.api.deps import require_platform_secret
from app.core.config import settings
from app.host.registry import host_registry
from app.schemas.common import ApiResponse
from app.schemas.host import (
    HostBindRequest,
    HostConversationCreateRequest,
    HostConversationPatchRequest,
    HostMessageRequest,
    HostToolCatalogPatchRequest,
    HostToolCatalogReplaceRequest,
)
from app.schemas.agent import AgentEvent
from app.services.agent_run_service import agent_run_service
from app.services.host_control_service import host_control_service
from app.services.session_service import session_service
from app.services.store import store_service
from app.services.tool_catalog_service import (
    ToolCatalogConflictError,
    ToolCatalogValidationError,
    tool_catalog_service,
)

router = APIRouter(prefix="/api/v1/host", tags=["host"])


def _host_session(session_id: str, platform: dict) -> object:
    conversation = store_service.get_conversation_by_session(session_id)
    if conversation is None or int(conversation.get("platform_id") or 0) != int(platform["platform_id"]):
        raise HTTPException(status_code=404, detail="目标宿主会话不存在")
    return session_service.get_or_create(session_id)


def _mutation_payload(result) -> dict:
    return {
        "revision": result.revision,
        "fingerprint": result.fingerprint,
        "changed": result.changed,
        "added": list(result.added),
        "updated": list(result.updated),
        "removed": list(result.removed),
        "tool_count": result.tool_count,
    }


def _sse(event: AgentEvent) -> str:
    lines = [f"event: {event.type}"]
    if event.seq is not None:
        lines.append(f"id: {event.seq}")
    lines.append(f"data: {event.model_dump_json()}")
    return "\n".join(lines) + "\n\n"


@router.get("/public/embed/aethercore-embed.js", include_in_schema=False)
def get_public_embed_loader() -> FileResponse:
    """公开返回官方 embed loader，供宿主直接通过 AetherCore 域名加载。"""
    asset_path = settings.project_root / "host-adapters" / "universal" / "aethercore-embed.js"
    if not asset_path.exists():
        raise HTTPException(status_code=404, detail="官方 embed loader 不存在")
    return FileResponse(
        path=Path(asset_path),
        media_type="application/javascript",
        filename="aethercore-embed.js",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/bind")
def bind_host(
    request: HostBindRequest,
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    """宿主平台绑定会话并注入能力。"""
    if platform["platform_key"] != request.platform_key:
        raise HTTPException(status_code=403, detail="平台密钥与目标平台不匹配")
    try:
        summary = host_registry.bind(request, platform=platform)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        print(f"[host/bind] RuntimeError: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"运行时错误: {str(exc)}") from exc
    except FileNotFoundError as exc:
        print(f"[host/bind] FileNotFoundError: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"文件不存在: {str(exc)}") from exc
    except Exception as exc:
        print(f"[host/bind] Unexpected error: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"绑定失败: {type(exc).__name__}: {str(exc)}") from exc
    summary["workbench_url"] = (
        f"{settings.resolved_manage_frontend_public_base_url}"
        f"?embed_token={summary['token']}&session_id={summary['session_id']}"
    )
    return ApiResponse(message="宿主绑定成功", data=summary)


@router.post("/conversations")
def create_host_conversation(
    request: HostConversationCreateRequest,
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    """创建或幂等复用一个宿主会话，可直接指定隐藏状态。"""
    try:
        result = host_control_service.create_conversation(platform=platform, request=request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(message="宿主会话已创建", data=result)


@router.get("/conversations")
def list_host_conversations(
    external_user_id: str = Query(min_length=1, max_length=256),
    include_hidden: bool = False,
    include_archived: bool = False,
    include_deleted: bool = False,
    limit: int = Query(default=20, ge=1, le=200),
    cursor: int = Query(default=0, ge=0),
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    result = host_control_service.list_conversations(
        platform=platform,
        external_user_id=external_user_id,
        include_hidden=include_hidden,
        include_archived=include_archived,
        include_deleted=include_deleted,
        limit=limit,
        offset=cursor,
    )
    return ApiResponse(message="宿主会话列表", data=result)


@router.get("/conversations/{conversation_id}")
def get_host_conversation(
    conversation_id: str,
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    try:
        result = host_control_service.get_conversation(platform=platform, conversation_id=conversation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    return ApiResponse(message="宿主会话详情", data=result)


@router.get("/workspaces/{workspace_id}")
def get_host_workspace(
    workspace_id: str,
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    try:
        result = host_control_service.get_workspace(platform=platform, workspace_id=workspace_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Workspace 不存在") from exc
    return ApiResponse(message="Workspace 详情", data=result)


@router.get("/workspaces/{workspace_id}/members")
def list_host_workspace_members(
    workspace_id: str,
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    try:
        result = host_control_service.list_workspace_members(
            platform=platform,
            workspace_id=workspace_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Workspace 不存在") from exc
    return ApiResponse(message="Workspace 成员列表", data=result)


@router.get("/workspaces/{workspace_id}/runtime")
def get_host_workspace_runtime(
    workspace_id: str,
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    try:
        result = host_control_service.get_workspace_runtime(
            platform=platform,
            workspace_id=workspace_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Workspace 不存在") from exc
    return ApiResponse(message="Workspace runtime 状态", data=result)


@router.patch("/conversations/{conversation_id}")
def patch_host_conversation(
    conversation_id: str,
    request: HostConversationPatchRequest,
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    try:
        result = host_control_service.patch_conversation(
            platform=platform,
            conversation_id=conversation_id,
            request=request,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ApiResponse(message="宿主会话已更新", data=result)


@router.post("/conversations/{conversation_id}/hide")
def hide_host_conversation(
    conversation_id: str,
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    try:
        result = host_control_service.set_visibility(
            platform=platform,
            conversation_id=conversation_id,
            visibility="hidden",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    return ApiResponse(message="宿主会话已隐藏", data=result)


@router.post("/conversations/{conversation_id}/restore")
def restore_host_conversation(
    conversation_id: str,
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    try:
        result = host_control_service.set_visibility(
            platform=platform,
            conversation_id=conversation_id,
            visibility="normal",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    return ApiResponse(message="宿主会话已恢复", data=result)


@router.delete("/conversations/{conversation_id}")
def delete_host_conversation(
    conversation_id: str,
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    try:
        deleted = host_control_service.delete_conversation(platform=platform, conversation_id=conversation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    if not deleted:
        raise HTTPException(status_code=409, detail="会话已删除")
    return ApiResponse(message="宿主会话已删除")


@router.post("/conversations/{conversation_id}/messages")
async def send_host_message(
    conversation_id: str,
    request: HostMessageRequest,
    platform: dict = Depends(require_platform_secret),
):
    try:
        result = await host_control_service.send_message(
            platform=platform,
            conversation_id=conversation_id,
            message=request.message,
            allow_network=request.allow_network,
            client_message_id=request.client_message_id,
            idempotency_key=request.idempotency_key,
            reasoning_effort=request.reasoning_effort,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if request.response_mode != "stream":
        return ApiResponse(message="Agent 运行已创建", data=result)

    run_id = str(result["run_id"])

    async def event_stream():
        queue = await agent_run_service.subscribe(run_id)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _sse(event)
        finally:
            await agent_run_service.unsubscribe(run_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/conversations/{conversation_id}/assistant-messages")
def list_host_assistant_messages(
    conversation_id: str,
    limit: int = Query(default=20, ge=1, le=200),
    include_subagents: bool = True,
    run_status: str | None = Query(default=None),
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    statuses = {item.strip() for item in (run_status or "").split(",") if item.strip()} or None
    try:
        result = host_control_service.assistant_messages(
            platform=platform,
            conversation_id=conversation_id,
            limit=limit,
            include_subagents=include_subagents,
            run_statuses=statuses,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    return ApiResponse(message="最近 AI 回复", data=result)


@router.get("/runs/{run_id}")
def get_host_run(
    run_id: str,
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    try:
        result = host_control_service.get_run(platform=platform, run_id=run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="运行不存在") from exc
    return ApiResponse(message="运行详情", data=result)


@router.get("/runs/{run_id}/events")
def list_host_run_events(
    run_id: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=2000),
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    try:
        items = host_control_service.list_run_events(
            platform=platform,
            run_id=run_id,
            after_seq=after_seq,
            limit=limit,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="运行不存在") from exc
    return ApiResponse(message="运行事件", data={"items": items, "next_cursor": items[-1]["seq"] if items else None})


@router.post("/runs/{run_id}/cancel")
async def cancel_host_run(
    run_id: str,
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    try:
        result = await host_control_service.cancel_run(platform=platform, run_id=run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="运行不存在") from exc
    return ApiResponse(message="运行取消请求已提交", data=result)


@router.get("/sessions/{session_id}/tools")
def get_host_tools(
    session_id: str,
    include_descriptors: bool = Query(default=False),
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    """Read the current host tool catalog without exposing unrelated session state."""
    session = _host_session(session_id, platform)
    snapshot = tool_catalog_service.snapshot(session)
    tools = [
        {
            "name": str(descriptor["name"]),
            "source_ids": list(snapshot.sources_by_name.get(str(descriptor["name"]), ("host:legacy",))),
            **({"descriptor": descriptor} if include_descriptors else {}),
        }
        for descriptor in snapshot.descriptors
    ]
    return ApiResponse(
        message="工具目录读取成功",
        data={
            "revision": snapshot.revision,
            "fingerprint": snapshot.fingerprint,
            "tool_count": len(tools),
            "tools": tools,
            "refresh_policy": session.tool_refresh_policy,
        },
    )


@router.get("/sessions/{session_id}/binding")
def get_host_session_binding(
    session_id: str,
    external_user_id: str = Query(min_length=1, max_length=256),
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    """Verify ownership using persisted conversation metadata, never callback context."""
    conversation = store_service.get_conversation_by_session(session_id)
    if (conversation is None
        or conversation.get("platform_id") != platform["platform_id"]
        or conversation.get("external_user_id") != external_user_id):
        raise HTTPException(status_code=404, detail="目标宿主会话不存在")
    session = session_service.get_or_create(session_id)
    return ApiResponse(data={
        "session_id": session_id,
        "conversation_id": conversation["conversation_id"],
        "platform_key": platform["platform_key"],
        "external_user_id": external_user_id,
        "page_context": session.host_context.get("page", {}),
    })


@router.put("/sessions/{session_id}/tools")
def replace_host_tools(
    session_id: str,
    request: HostToolCatalogReplaceRequest,
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    """Atomically replace a source-owned part of the host tool catalog."""
    session = _host_session(session_id, platform)
    try:
        result = tool_catalog_service.replace(
            session,
            [item.model_dump(mode="json") for item in request.tools],
            source_id=request.source_id,
            replace_all=request.replace_all,
            expected_revision=request.expected_revision,
        )
    except ToolCatalogConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ToolCatalogValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session_service.persist(session)
    return ApiResponse(message="工具目录已更新", data=_mutation_payload(result))


@router.patch("/sessions/{session_id}/tools")
def patch_host_tools(
    session_id: str,
    request: HostToolCatalogPatchRequest,
    platform: dict = Depends(require_platform_secret),
) -> ApiResponse:
    """Atomically add, update, and remove source-owned host tools."""
    session = _host_session(session_id, platform)
    upserts = [
        operation.tool.model_dump(mode="json")
        for operation in request.operations
        if operation.op == "upsert"
    ]
    removals = [
        operation.name
        for operation in request.operations
        if operation.op == "remove"
    ]
    try:
        result = tool_catalog_service.apply_operations(
            session,
            source_id=request.source_id,
            upserts=upserts,
            removals=removals,
            expected_revision=request.expected_revision,
        )
    except ToolCatalogConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ToolCatalogValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session_service.persist(session)
    return ApiResponse(message="工具目录增量更新成功", data=_mutation_payload(result))
