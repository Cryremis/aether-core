from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.config import settings
from app.services.platform_runtime_image_service import platform_runtime_image_service
from app.services.store import store_service


class FakeUploadFile:
    def __init__(self, filename: str, chunks: list[bytes]) -> None:
        self.filename = filename
        self._chunks = list(chunks)

    async def read(self, _size: int = -1) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def initialize_store(tmp_path: Path) -> None:
    settings.storage_root = tmp_path / "storage"
    store_service._db_path = settings.storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    store_service.initialize()


def test_stream_upload_to_path_writes_all_chunks(tmp_path):
    target = tmp_path / "uploaded-image.tar"
    upload = FakeUploadFile("uploaded-image.tar", [b"hello", b"-", b"world"])

    asyncio.run(platform_runtime_image_service._stream_upload_to_path(upload, target))

    assert target.read_bytes() == b"hello-world"


def test_get_runtime_image_guide_contains_contract(tmp_path):
    initialize_store(tmp_path)
    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None
    platform = store_service.create_platform(
        platform_key="runtime-guide-service",
        display_name="Runtime Guide Service",
        host_type="embedded",
        description="guide test",
        owner_user_id=admin.user_id,
    )

    guide = platform_runtime_image_service.get_guide(int(platform["platform_id"]))

    assert guide.build_spec.target_os == "linux"
    assert guide.build_spec.target_arch == "amd64"
    assert settings.sandbox_docker_work_dir in guide.build_spec.required_directories
    assert "AETHER_WORK_DIR" in guide.build_spec.required_env_vars
    assert "FROM ubuntu:24.04" in guide.build_spec.sample_dockerfile
