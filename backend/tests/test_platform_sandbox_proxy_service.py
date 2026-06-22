from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.schemas.platform import PlatformSandboxProxyConfigUpdateRequest
from app.services.platform_sandbox_proxy_service import platform_sandbox_proxy_service
from app.services.store import store_service


def initialize_store(tmp_path: Path) -> None:
    settings.storage_root = tmp_path / "storage"
    store_service._db_path = settings.storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    store_service.initialize()


def test_platform_sandbox_proxy_resolves_platform_override(tmp_path):
    initialize_store(tmp_path)
    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None
    platform = store_service.create_platform(
        platform_key="proxy-platform",
        display_name="Proxy Platform",
        host_type="embedded",
        description="proxy test",
        owner_user_id=admin.user_id,
    )

    summary = platform_sandbox_proxy_service.update_config(
        int(platform["platform_id"]),
        PlatformSandboxProxyConfigUpdateRequest(
            enabled=True,
            http_proxy="http://proxy.internal:7890",
            https_proxy="http://proxy.internal:7890",
            no_proxy="dashscope.aliyuncs.com,localhost",
            inherit_host_proxy=False,
        ),
    )

    assert summary.enabled is True
    effective = platform_sandbox_proxy_service.resolve_for_platform(int(platform["platform_id"]))
    assert effective.enabled is True
    assert effective.http_proxy == "http://proxy.internal:7890"
    assert effective.inherit_host_proxy is False
    env_map = effective.to_env_map()
    assert env_map["HTTP_PROXY"] == "http://proxy.internal:7890"
    assert env_map["http_proxy"] == "http://proxy.internal:7890"
    assert env_map["NO_PROXY"] == "dashscope.aliyuncs.com,localhost"


def test_platform_sandbox_proxy_falls_back_to_global_defaults(tmp_path):
    initialize_store(tmp_path)
    settings.sandbox_proxy_enabled = True
    settings.sandbox_proxy_http = "http://global.proxy:9000"
    settings.sandbox_proxy_https = "http://global.proxy:9000"
    settings.sandbox_proxy_all = ""
    settings.sandbox_proxy_no_proxy = "localhost"
    settings.sandbox_proxy_inherit_host_proxy = False

    effective = platform_sandbox_proxy_service.resolve_for_platform(None)
    assert effective.enabled is True
    assert effective.http_proxy == "http://global.proxy:9000"
    assert effective.inherit_host_proxy is False
