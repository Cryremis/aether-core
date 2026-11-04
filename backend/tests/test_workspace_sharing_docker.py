"""显式启用的真实容器回归；只使用本机已有 aethercore-sandbox 镜像。"""
from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from dataclasses import replace

import pytest

from app.core.config import settings
from app.sandbox.manager import sandbox_manager
from app.services.shared_skill_service import shared_skill_service
from app.services.platform_runtime_image_service import platform_runtime_image_service
from app.services.workspace_runtime_service import WorkspaceRuntimeService
from app.services.workspace_sync_service import WorkspaceSyncService


@pytest.mark.skipif(os.environ.get("AETHERCORE_DOCKER_TESTS") != "1", reason="requires explicit local Docker opt-in")
def test_real_container_shared_assets_delta_and_recreation(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
    monkeypatch.setattr(settings, "sandbox_allow_network", False)
    monkeypatch.setattr(settings, "sandbox_docker_read_only_rootfs", False)
    workspace = sandbox_manager.ensure_workspace("ws_docker_test", "sess_docker_test")
    source = tmp_path / "skill"
    source.mkdir()
    (source / "SKILL.md").write_text("shared skill")
    (source / "data.txt").write_text("shared resource")
    _, logical = shared_skill_service.reference(workspace, source, name="demo")
    image = "aethercore-sharing-test:" + uuid.uuid4().hex[:12]
    containers = []

    def docker(*args):
        return subprocess.run(["docker", *args], capture_output=True, text=True, check=True, timeout=120).stdout.strip()

    baseline = tmp_path / "baseline"
    for section in ("skills/baseline", "work", "logs"):
        (baseline / section).mkdir(parents=True)
    (baseline / "skills/baseline/large.bin").write_bytes(b"\0" * (4 * 1024 * 1024))
    (baseline / "work/base.txt").write_text("original")
    workspace = replace(workspace, baseline_root=baseline)
    snapshot = shared_skill_service.pin_baseline(workspace)
    platform_runtime_image_service._write_baseline_tarball(snapshot, tmp_path / "baseline.tar")
    (tmp_path / "Dockerfile").write_text(platform_runtime_image_service._build_auto_image_dockerfile("aethercore-sandbox:latest"))
    # 不把测试 storage 或缓存送入镜像构建上下文。
    (tmp_path / ".dockerignore").write_text("*\n!Dockerfile\n!.dockerignore\n!baseline.tar\n")
    service, runtime = WorkspaceSyncService(), WorkspaceRuntimeService()

    def create():
        name = "aethercore-sharing-test-" + uuid.uuid4().hex[:12]
        containers.append(name)
        docker(*runtime._build_run_args(workspace, name, image))
        asyncio.run(service.hydrate_container(docker_binary="docker", container_name=name, workspace=workspace))
        return name

    def capture(name):
        return asyncio.run(service.capture_container_delta(docker_binary="docker", container_name=name, workspace=workspace))

    try:
        docker("build", "--pull=false", "-t", image, str(tmp_path))
        first = create()
        (baseline / "work/base.txt").write_text("next baseline")
        assert sandbox_manager.resolve_logical_path(workspace, "work/base.txt").read_text() == "original"
        assert docker("exec", first, "cat", "/workspace/work/base.txt") == "original"
        assert docker("exec", first, "sh", "-c", "test ! -e /tmp/baseline.tar && echo clean") == "clean"
        assert docker("exec", first, "cat", logical + "/data.txt") == "shared resource"
        readonly = subprocess.run(["docker", "exec", first, "sh", "-c", f"echo bad > {logical}/data.txt"], capture_output=True)
        assert readonly.returncode != 0
        docker("exec", first, "sh", "-c", "mkdir -p /workspace/skills/.available/called; echo called > /workspace/skills/.available/called/SKILL.md; echo changed > /workspace/work/base.txt")
        capture(first)
        assert not (workspace.skills_dir / "baseline").exists()
        assert (workspace.work_dir / "base.txt").read_text().strip() == "changed"
        capture(first)
        docker("exec", first, "rm", "/workspace/skills/baseline/large.bin")
        assert "skills/baseline/large.bin" in capture(first).tombstones
        second = create()
        assert docker("exec", second, "cat", "/workspace/work/base.txt") == "changed"
        assert docker("exec", second, "cat", logical + "/data.txt") == "shared resource"
        assert docker("exec", second, "sh", "-c", "test ! -e /workspace/skills/baseline/large.bin && echo deleted") == "deleted"
        assert not (workspace.skills_dir / "baseline/large.bin").exists()
    finally:
        for container in containers:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30)
        subprocess.run(["docker", "image", "rm", image], capture_output=True, timeout=30)
