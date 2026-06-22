from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.schemas.platform import PlatformSandboxProxyConfigSummary, PlatformSandboxProxyConfigUpdateRequest
from app.services.store import store_service


@dataclass(frozen=True)
class EffectiveSandboxProxyConfig:
    enabled: bool
    http_proxy: str = ""
    https_proxy: str = ""
    all_proxy: str = ""
    no_proxy: str = ""
    inherit_host_proxy: bool = True
    source: str = "global"

    def to_env_map(self) -> dict[str, str]:
        env: dict[str, str] = {}
        mapping = {
            "HTTP_PROXY": self.http_proxy,
            "HTTPS_PROXY": self.https_proxy,
            "ALL_PROXY": self.all_proxy,
            "NO_PROXY": self.no_proxy,
        }
        for key, value in mapping.items():
            normalized = value.strip()
            if not normalized:
                continue
            env[key] = normalized
            env[key.lower()] = normalized
        return env


class PlatformSandboxProxyService:
    def get_summary(self, platform_id: int) -> PlatformSandboxProxyConfigSummary:
        platform = self._require_platform(platform_id)
        return self._to_summary(platform_id, platform)

    def update_config(
        self,
        platform_id: int,
        request: PlatformSandboxProxyConfigUpdateRequest,
    ) -> PlatformSandboxProxyConfigSummary:
        platform = store_service.update_platform_sandbox_proxy_config(
            platform_id=platform_id,
            enabled=request.enabled,
            http_proxy=request.http_proxy,
            https_proxy=request.https_proxy,
            all_proxy=request.all_proxy,
            no_proxy=request.no_proxy,
            inherit_host_proxy=request.inherit_host_proxy,
        )
        if platform is None:
            raise RuntimeError("平台不存在")
        return self._to_summary(platform_id, platform)

    def clear_config(self, platform_id: int) -> PlatformSandboxProxyConfigSummary:
        platform = store_service.clear_platform_sandbox_proxy_config(platform_id=platform_id)
        if platform is None:
            raise RuntimeError("平台不存在")
        return self._to_summary(platform_id, platform)

    def resolve_for_platform(self, platform_id: int | None) -> EffectiveSandboxProxyConfig:
        if platform_id is not None:
            platform = store_service.get_platform_by_id(int(platform_id))
            if platform is not None and self._platform_has_explicit_config(platform):
                return EffectiveSandboxProxyConfig(
                    enabled=bool(platform.get("sandbox_proxy_enabled")),
                    http_proxy=str(platform.get("sandbox_proxy_http") or ""),
                    https_proxy=str(platform.get("sandbox_proxy_https") or ""),
                    all_proxy=str(platform.get("sandbox_proxy_all") or ""),
                    no_proxy=str(platform.get("sandbox_proxy_no_proxy") or ""),
                    inherit_host_proxy=bool(platform.get("sandbox_proxy_inherit_host_proxy", True)),
                    source="platform",
                )
        return EffectiveSandboxProxyConfig(
            enabled=settings.sandbox_proxy_enabled,
            http_proxy=settings.sandbox_proxy_http,
            https_proxy=settings.sandbox_proxy_https,
            all_proxy=settings.sandbox_proxy_all,
            no_proxy=settings.sandbox_proxy_no_proxy,
            inherit_host_proxy=settings.sandbox_proxy_inherit_host_proxy,
            source="global",
        )

    def _require_platform(self, platform_id: int) -> dict[str, Any]:
        platform = store_service.get_platform_by_id(platform_id)
        if platform is None:
            raise RuntimeError("平台不存在")
        return platform

    def _to_summary(self, platform_id: int, platform: dict[str, Any]) -> PlatformSandboxProxyConfigSummary:
        return PlatformSandboxProxyConfigSummary(
            platform_id=platform_id,
            enabled=bool(platform.get("sandbox_proxy_enabled")),
            http_proxy=str(platform.get("sandbox_proxy_http") or ""),
            https_proxy=str(platform.get("sandbox_proxy_https") or ""),
            all_proxy=str(platform.get("sandbox_proxy_all") or ""),
            no_proxy=str(platform.get("sandbox_proxy_no_proxy") or ""),
            inherit_host_proxy=bool(platform.get("sandbox_proxy_inherit_host_proxy", True)),
            updated_at=platform.get("sandbox_proxy_updated_at"),
        )

    def _platform_has_explicit_config(self, platform: dict[str, Any]) -> bool:
        if platform.get("sandbox_proxy_updated_at"):
            return True
        return any(
            str(platform.get(field) or "").strip()
            for field in (
                "sandbox_proxy_http",
                "sandbox_proxy_https",
                "sandbox_proxy_all",
                "sandbox_proxy_no_proxy",
            )
        )


platform_sandbox_proxy_service = PlatformSandboxProxyService()
