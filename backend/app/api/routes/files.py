# backend/app/api/routes/files.py
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.schemas.common import ApiResponse
from app.schemas.files import FileListResponse
from app.services.artifact_service import artifact_service
from app.services.file_service import file_service
from app.services.session_service import session_service

router = APIRouter(prefix="/api/v1/agent/files", tags=["files"])


@router.post("/upload")
async def upload_file(
    session_id: str,
    upload_file: UploadFile = File(...),
) -> ApiResponse:
    """向会话工作区上传文件。"""

    session = session_service.get_or_create(session_id)
    record = await file_service.save_upload(session, upload_file)
    return ApiResponse(message="文件上传成功", data=record.model_dump(mode="json"))


@router.get("")
def list_files(session_id: str) -> FileListResponse:
    """列出会话文件与产物。"""

    session = session_service.get_or_create(session_id)
    return FileListResponse(
        items=[*file_service.list_uploads(session), *artifact_service.list_artifacts(session)]
    )


@router.get("/{file_id}/download")
def download_file(session_id: str, file_id: str) -> FileResponse:
    """下载会话文件或产物。"""

    session = session_service.get_or_create(session_id)
    file_path = file_service.resolve_file_path(session, file_id)
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path=file_path, filename=file_path.name)
