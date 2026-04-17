# backend/app/api/routes/files.py
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import AuthContext, get_auth_context
from app.schemas.common import ApiResponse
from app.schemas.files import FileListResponse
from app.services.artifact_service import artifact_service
from app.services.file_service import file_service
from app.services.session_service import session_service
from app.services.store import store_service

router = APIRouter(prefix="/api/v1/agent/files", tags=["files"])


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
    return FileListResponse(items=[*file_service.list_visible_files(session), *artifact_service.list_artifacts(session)])


@router.get("/{file_id}/download")
def download_file(session_id: str, file_id: str, auth: AuthContext = Depends(get_auth_context)) -> FileResponse:
    session = _ensure_session_access(session_id, auth)
    file_path = file_service.resolve_file_path(session, file_id)
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path=file_path, filename=file_path.name)
