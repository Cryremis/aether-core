# backend/app/api/routes/files.py
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import AuthContext, get_auth_context
from app.schemas.common import ApiResponse
from app.schemas.files import FileListResponse
from app.services.artifact_service import artifact_service
from app.services.file_service import file_service
from app.services.platform_baseline_service import platform_baseline_service
from app.services.session_service import session_service
from app.services.store import store_service

router = APIRouter(prefix="/api/v1/agent/files", tags=["files"])


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


def _ensure_session_access(session_id: str, auth: AuthContext):
    conversation = store_service.get_conversation_by_session(session_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if auth.kind == "user":
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
    _ensure_embed_baseline(auth, conversation, session)
    return session


@router.post("/upload")
async def upload_file(
    session_id: str,
    upload_file: UploadFile = File(...),
    auth: AuthContext = Depends(get_auth_context),
) -> ApiResponse:
    session = _ensure_session_access(session_id, auth)
    record = await file_service.save_upload(session, upload_file)
    return ApiResponse(message="文件上传成功", data=record.model_dump(mode="json"))


@router.get("")
def list_files(session_id: str, auth: AuthContext = Depends(get_auth_context)) -> FileListResponse:
    session = _ensure_session_access(session_id, auth)
    return FileListResponse(items=[*file_service.list_uploads(session), *artifact_service.list_artifacts(session)])


@router.get("/{file_id}/download")
def download_file(session_id: str, file_id: str, auth: AuthContext = Depends(get_auth_context)) -> FileResponse:
    session = _ensure_session_access(session_id, auth)
    file_path = file_service.resolve_file_path(session, file_id)
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path=file_path, filename=file_path.name)


@router.get("/{file_id}/content")
def read_file_content(session_id: str, file_id: str, auth: AuthContext = Depends(get_auth_context)) -> ApiResponse:
    session = _ensure_session_access(session_id, auth)
    try:
        content = file_service.read_text(session, file_id=file_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    except IsADirectoryError as exc:
        raise HTTPException(status_code=400, detail="目标是目录，无法预览") from exc
    return ApiResponse(message="文件内容", data={"content": content})


@router.put("/{file_id}/content")
async def update_file_content(
    session_id: str,
    file_id: str,
    payload: dict,
    auth: AuthContext = Depends(get_auth_context),
) -> ApiResponse:
    session = _ensure_session_access(session_id, auth)
    file_path = file_service.resolve_file_path(session, file_id)
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if file_path.is_dir():
        raise HTTPException(status_code=400, detail="目标是目录，无法编辑")
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content 必须是字符串")
    file_path.write_text(content, encoding="utf-8")
    artifact_service.sync_output_directory(session)
    return ApiResponse(message="文件已保存", data={"items": [item.model_dump(mode="json") for item in file_service.list_sidebar_files(session)]})
