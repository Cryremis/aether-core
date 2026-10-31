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


@pytest.fixture
def checkpoint_model(monkeypatch):
    """引擎用例只验证编排，摘要内容/失败语义在独立契约用例中验证。"""
    async def summarize(messages, runtime):
        return "【目标】继续任务\n【约束与事实】保留来源记录\n【已完成与证据】旧步骤已归档\n【待办与疑问】继续当前用户任务"
    monkeypatch.setattr("app.services.context.summarizer.compaction_summarizer.summarize", summarize)


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
