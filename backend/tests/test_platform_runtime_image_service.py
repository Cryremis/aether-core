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


class FakeDockerProcess:
    """按命令返回预设 stdout 的假 docker 子进程。"""

    def __init__(self, stdout: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""

    async def wait(self) -> int:
        return self.returncode


def initialize_store(tmp_path: Path) -> None:
    settings.storage_root = tmp_path / "storage"
    store_service._db_path = settings.storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    store_service.initialize()


def _install_fake_docker(monkeypatch, responses: list[FakeDockerProcess], calls: list[list[str]]) -> None:
    """把 image service 的 docker 子进程调用替换为预设响应序列,并记录每次 argv。"""

    async def fake_exec(_binary, *argv, **_kwargs):
        calls.append(list(argv))
        return responses.pop(0) if responses else FakeDockerProcess()

    monkeypatch.setattr(
        "app.services.platform_runtime_image_service.asyncio.create_subprocess_exec",
        fake_exec,
    )


def test_prune_old_auto_images_keeps_latest_two(monkeypatch):
    """4 个历史 tag 时,仅保留刚构建的 + 次新 1 个,其余被 rmi(non-force)。"""
    listing = FakeDockerProcess(
        stdout=(
            "1-aaaa\t2026-09-01 10:00:00 +0800 CST\n"
            "1-bbbb\t2026-09-03 10:00:00 +0800 CST\n"
            "1-cccc\t2026-09-05 10:00:00 +0800 CST\n"
            "1-dddd\t2026-09-07 10:00:00 +0800 CST\n"
            "2-xxxx\t2026-09-06 10:00:00 +0800 CST\n"
        ).encode("utf-8")
    )
    calls: list[list[str]] = []
    _install_fake_docker(monkeypatch, [listing, FakeDockerProcess(), FakeDockerProcess()], calls)

    asyncio.run(
        platform_runtime_image_service._prune_old_auto_images(
            "docker", platform_id=1, keep="aethercore-platform-runtime:1-dddd"
        )
    )

    rmi_calls = [c for c in calls if c[:2] == ["image", "rm"]]
    removed = {c[2].split(":")[-1] for c in rmi_calls}
    # 保留 ddd(刚构建)+ ccc(次新),删除 aaa/bbb;其它平台(2-xxxx)不触碰
    assert removed == {"1-aaaa", "1-bbbb"}
    assert not any(":2-" in c[2] for c in rmi_calls)
    # rmi 不带 -f(被容器占用时 Docker 拒绝,作为安全网)
    assert all("-f" not in c for c in calls if c[:2] == ["image", "rm"])


def test_prune_old_auto_images_noop_when_within_retention(monkeypatch):
    """历史 tag 不超过 2 个时不触发任何 rmi。"""
    listing = FakeDockerProcess(
        stdout=(
            "3-aaaa\t2026-09-01 10:00:00 +0800 CST\n"
            "3-bbbb\t2026-09-07 10:00:00 +0800 CST\n"
        ).encode("utf-8")
    )
    calls: list[list[str]] = []
    _install_fake_docker(monkeypatch, [listing], calls)

    asyncio.run(
        platform_runtime_image_service._prune_old_auto_images(
            "docker", platform_id=3, keep="aethercore-platform-runtime:3-bbbb"
        )
    )

    assert not any(c[:2] == ["image", "rm"] for c in calls)


def test_prune_old_auto_images_swallows_docker_failure(monkeypatch):
    """docker images 失败时静默返回,不抛异常。"""
    calls: list[list[str]] = []
    _install_fake_docker(monkeypatch, [FakeDockerProcess(returncode=1)], calls)

    asyncio.run(
        platform_runtime_image_service._prune_old_auto_images(
            "docker", platform_id=1, keep="aethercore-platform-runtime:1-zzzz"
        )
    )
    assert len(calls) == 1


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
