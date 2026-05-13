from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from app.core.config import settings
from app.schemas.platform import (
    PlatformRuntimeImageBuildSpec,
    PlatformRuntimeImageGuide,
    PlatformRuntimeImageSummary,
)
from app.services.store import store_service


class PlatformRuntimeImageService:
    """管理平台级容器镜像发布与构建规范。"""

    _UPLOAD_CHUNK_SIZE = 1024 * 1024
    _LOAD_NAME_PATTERN = re.compile(r"(?:Loaded image(?: ID)?:\s*)(?P<name>\S+)", re.IGNORECASE)

    def get_summary(self, platform_id: int) -> PlatformRuntimeImageSummary:
        platform = self._require_platform(platform_id)
        custom_image = str(platform.get("sandbox_image") or "").strip() or None
        return PlatformRuntimeImageSummary(
            platform_id=platform_id,
            custom_image=custom_image,
            resolved_image=custom_image or settings.sandbox_docker_image,
            updated_at=platform.get("sandbox_image_updated_at"),
        )

    def get_guide(self, platform_id: int) -> PlatformRuntimeImageGuide:
        platform = self._require_platform(platform_id)
        current = self.resolve_for_platform(platform_id)
        sample_dockerfile = f"""FROM ubuntu:24.04

RUN apt-get update \\
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \\
        bash ca-certificates curl git jq python3 python3-pip nodejs npm powershell \\
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 10001 sandbox || true

WORKDIR {settings.sandbox_docker_work_dir}

ENV HOME={settings.sandbox_docker_home_dir}
ENV XDG_CACHE_HOME={settings.sandbox_docker_cache_dir}
ENV XDG_CONFIG_HOME={settings.sandbox_docker_home_dir}/.config
ENV AETHER_SANDBOX_ROOT={settings.sandbox_docker_workspace_mount}
ENV AETHER_SKILLS_DIR={settings.sandbox_docker_skills_dir}
ENV AETHER_WORK_DIR={settings.sandbox_docker_work_dir}
ENV AETHER_LOGS_DIR={settings.sandbox_docker_logs_dir}

CMD ["/bin/bash", "-lc", "while true; do sleep 3600; done"]
"""
        spec = PlatformRuntimeImageBuildSpec(
            target_os="linux",
            target_arch="amd64",
            image_format="Docker / OCI image tarball",
            shell="bash",
            recommended_base="ubuntu:24.04 或其他兼容的 linux/amd64 基础镜像",
            entrypoint='镜像应支持 `/bin/bash -lc "while true; do sleep 3600; done"`',
            expected_workspace_root=settings.sandbox_docker_workspace_mount,
            required_directories=[
                settings.sandbox_docker_skills_dir,
                settings.sandbox_docker_work_dir,
                settings.sandbox_docker_logs_dir,
                settings.sandbox_docker_home_dir,
                settings.sandbox_docker_cache_dir,
            ],
            required_env_vars=[
                "AETHER_SANDBOX_ROOT",
                "AETHER_SKILLS_DIR",
                "AETHER_WORK_DIR",
                "AETHER_LOGS_DIR",
                "HOME",
                "XDG_CACHE_HOME",
                "XDG_CONFIG_HOME",
            ],
            resource_limits=[
                f"memory={settings.sandbox_docker_memory}",
                f"cpus={settings.sandbox_docker_cpus}",
                f"pids_limit={settings.sandbox_docker_pids_limit}",
                f"network_mode={settings.sandbox_docker_network_mode if settings.sandbox_allow_network else 'none'}",
            ],
            build_steps=[
                "使用 linux/amd64 构建镜像",
                "确保镜像内存在 `/bin/bash`，并可执行 `bash -lc`",
                "建议预装你们平台依赖的语言运行时和 CLI，例如 Python、Node.js、Git",
                "使用 `docker save your-image:tag -o platform-image.tar` 导出镜像包",
                "在管理台上传 `.tar` 或 `.tar.gz`，导入成功后会直接启用为当前镜像",
            ],
            sample_dockerfile=sample_dockerfile,
            notes=[
                "平台只保留一个 current 镜像，上传成功后会替换当前镜像。",
                "系统会回收该平台现有 runtime，后续新的 sandbox 调用统一使用新镜像。",
                "上传包仅用于导入，导入完成后会删除临时文件，不会长期保留。",
            ],
        )
        return PlatformRuntimeImageGuide(
            platform_id=platform_id,
            display_name=str(platform.get("display_name") or ""),
            current_image=current,
            build_spec=spec,
        )

    def update_image(self, platform_id: int, image: str) -> PlatformRuntimeImageSummary:
        normalized = self._normalize_image_name(image)
        platform = store_service.update_platform_sandbox_image(platform_id=platform_id, image=normalized)
        if platform is None:
            raise RuntimeError("平台不存在")
        return self.get_summary(platform_id)

    def clear_image(self, platform_id: int) -> PlatformRuntimeImageSummary:
        platform = store_service.update_platform_sandbox_image(platform_id=platform_id, image=None)
        if platform is None:
            raise RuntimeError("平台不存在")
        return self.get_summary(platform_id)

    async def publish_uploaded_image(self, platform_id: int, upload_file: UploadFile) -> PlatformRuntimeImageSummary:
        self._require_platform(platform_id)
        docker_binary = self._require_docker_binary()
        safe_suffix = "".join(Path(upload_file.filename or "").suffixes) or ".tar"
        with tempfile.NamedTemporaryFile(prefix="aethercore-platform-image-", suffix=safe_suffix, delete=False) as temp_file:
            temp_path = Path(temp_file.name)
        try:
            await self._stream_upload_to_path(upload_file, temp_path)
            loaded_image = await self._docker_load_image(docker_binary, temp_path)
            normalized = self._normalize_image_name(loaded_image)
            await self._verify_image_available(docker_binary, normalized)
            return self.update_image(platform_id, normalized)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def resolve_for_platform(self, platform_id: int | None) -> str:
        if platform_id is None:
            return settings.sandbox_docker_image
        platform = store_service.get_platform_by_id(int(platform_id))
        if platform is None:
            return settings.sandbox_docker_image
        custom_image = str(platform.get("sandbox_image") or "").strip()
        return custom_image or settings.sandbox_docker_image

    async def cleanup_replaced_image(self, image: str, *, keep_image: str) -> None:
        normalized_old = image.strip()
        normalized_keep = keep_image.strip()
        if not normalized_old or normalized_old == normalized_keep:
            return
        docker_binary = self._resolve_docker_binary()
        if not docker_binary:
            return
        process = await asyncio.create_subprocess_exec(
            docker_binary,
            "image",
            "rm",
            "-f",
            normalized_old,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            **self._subprocess_kwargs(),
        )
        await process.communicate()

    async def _stream_upload_to_path(self, upload_file: UploadFile, target_path: Path) -> None:
        with target_path.open("wb") as handle:
            while True:
                chunk = await upload_file.read(self._UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)

    async def _docker_load_image(self, docker_binary: str, image_path: Path) -> str:
        process = await asyncio.create_subprocess_exec(
            docker_binary,
            "load",
            "-i",
            str(image_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **self._subprocess_kwargs(),
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        if process.returncode != 0:
            error_text = self._decode_output(stderr_bytes).strip() or self._decode_output(stdout_bytes).strip()
            raise RuntimeError(error_text or "导入镜像失败")
        output = "\n".join(
            part for part in [self._decode_output(stdout_bytes).strip(), self._decode_output(stderr_bytes).strip()] if part
        )
        for line in output.splitlines():
            match = self._LOAD_NAME_PATTERN.search(line.strip())
            if match:
                return match.group("name")
        raise RuntimeError("镜像已导入，但无法解析镜像名称，请确认导出的 tar 包包含可命名标签。")

    async def _verify_image_available(self, docker_binary: str, image: str) -> None:
        process = await asyncio.create_subprocess_exec(
            docker_binary,
            "image",
            "inspect",
            image,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            **self._subprocess_kwargs(),
        )
        _, stderr_bytes = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(self._decode_output(stderr_bytes).strip() or "导入后的镜像不可用")

    def _normalize_image_name(self, image: str) -> str:
        normalized = image.strip()
        if not normalized:
            raise RuntimeError("镜像名称不能为空")
        if any(ch.isspace() for ch in normalized):
            raise RuntimeError("镜像名称不能包含空白字符")
        return normalized

    def _require_platform(self, platform_id: int) -> dict[str, Any]:
        platform = store_service.get_platform_by_id(platform_id)
        if platform is None:
            raise RuntimeError("平台不存在")
        return platform

    def _resolve_docker_binary(self) -> str | None:
        configured = settings.sandbox_docker_command.strip()
        candidates = [
            shutil.which(configured),
            shutil.which("docker.exe"),
            configured if Path(configured).suffix else None,
        ]
        for candidate in candidates:
            if not candidate:
                continue
            if Path(candidate).exists() or shutil.which(candidate):
                return candidate
        return None

    def _require_docker_binary(self) -> str:
        docker_binary = self._resolve_docker_binary()
        if not docker_binary:
            raise RuntimeError("未找到 docker 可执行文件，无法导入镜像。")
        return docker_binary

    def _subprocess_kwargs(self) -> dict[str, Any]:
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            return {"creationflags": subprocess.CREATE_NO_WINDOW}
        return {}

    def _decode_output(self, value: bytes) -> str:
        if not value:
            return ""
        for encoding in ("utf-8", "utf-16-le", "utf-16", "gbk"):
            try:
                decoded = value.decode(encoding)
                if "\x00" in decoded:
                    decoded = decoded.replace("\x00", "")
                return decoded
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="replace").replace("\x00", "")


platform_runtime_image_service = PlatformRuntimeImageService()
