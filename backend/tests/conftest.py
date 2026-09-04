# backend/tests/conftest.py
"""测试全局夹具。

预置离线模型注册表:阻断测试对 models.dev 的真实网络依赖
(懒加载触发 fetch,SSL 慢连接曾把单个引擎测试拖到 40s+),
统一走最小内置配置,保证测试快速且离线可复现。

采用环境变量 AETHERCORE_DISABLE_MODELS_FETCH 而非方法级 patch,
保留 ModelsDevClient 真实行为逻辑可被 client 层单测覆盖。
"""

from __future__ import annotations

import pytest

from app.services.provider import models as provider_models
from app.services.provider.models import ModelsRegistry


@pytest.fixture(autouse=True, scope="session")
def _offline_models_registry(monkeysession):
    """session 级禁用 models.dev 网络获取,预初始化全局 registry(走 builtin fallback)。"""
    monkeysession.setenv("AETHERCORE_DISABLE_MODELS_FETCH", "true")
    registry = ModelsRegistry(
        cache_path=None,
        enable_background_refresh=False,
    )
    registry._ensure_loaded()
    assert registry._loaded
    provider_models._registry_instance = registry
    yield
    provider_models._registry_instance = None


@pytest.fixture(scope="session")
def monkeysession(request):
    """session 级 monkeypatch(标准配方,官方 monkeypatch 为 function 级)。"""
    from _pytest.monkeypatch import MonkeyPatch

    mpatch = MonkeyPatch()
    yield mpatch
    mpatch.undo()
