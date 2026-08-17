# backend/app/api/routes/host.py
import traceback
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.deps import require_platform_secret
from app.core.config import settings
from app.host.registry import host_registry
from app.schemas.common import ApiResponse
from app.schemas.host import (
    HostBindRequest,
    HostToolCatalogPatchRequest,
    HostToolCatalogReplaceRequest,
)
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


@router.get("/public/embed/aethercore-embed.js", include_in_schema=False)
def get_public_embed_loader() -> FileResponse:
    """公开返回官方 embed loader，供宿主直接通过 AetherCore 域名加载。"""
    asset_path = settings.project_root / "host-adapters" / "universal" / "aethercore-embed.js"
    if not asset_path.exists():
        raise HTTPException(status_code=404, detail="官方 embed loader 不存在")
    return FileResponse(path=Path(asset_path), media_type="application/javascript", filename="aethercore-embed.js")


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
