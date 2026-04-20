# backend/tests/test_network_service.py
from __future__ import annotations

import asyncio

from app.schemas.llm import LlmNetworkConfig
from app.services.llm_config_service import RuntimeLlmConfig, llm_config_service
from app.services.network_service import NetworkPolicyError, network_service


def make_runtime_config(**overrides):
    network = LlmNetworkConfig(enabled=True, **overrides.pop("network_overrides", {}))
    return RuntimeLlmConfig(
        scope="user",
        provider_kind="litellm",
        api_format="openai-compatible",
        base_url=overrides.pop("base_url", "https://open.bigmodel.cn/api/paas/v4"),
        model=overrides.pop("model", "glm-4-air"),
        api_key="llm-key",
        extra_headers={},
        extra_body={},
        network=network,
        enabled=True,
        **overrides,
    )


def test_web_search_uses_native_provider(monkeypatch):
    runtime = make_runtime_config()

    class FakeAdapter:
        name = "fake_native"

        async def search(self, **kwargs):
            return {
                "query": kwargs["query"],
                "provider": "glm_web_search",
                "summary": "native summary",
                "results": [{"title": "Example", "url": "https://example.com", "snippet": "", "source": "glm_web_search"}],
            }

    def fake_resolve(_runtime_config):
        return FakeAdapter(), "https://example.com/native-search"

    monkeypatch.setattr(network_service, "_resolve_search_adapter", fake_resolve)

    result = asyncio.run(
        network_service.web_search(
            session=None,  # type: ignore[arg-type]
            runtime_config=runtime,
            query="latest news",
        )
    )

    assert result["strategy"] == "fake_native"
    assert result["provider"] == "glm_web_search"


def test_dashscope_is_recognized_as_native_search_provider():
    runtime = make_runtime_config(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus",
    )
    assert network_service.supports_web_search(runtime) is True


def test_dashscope_glm_is_not_recognized_as_native_search_provider():
    runtime = make_runtime_config(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="glm-5",
    )
    assert network_service.supports_web_search(runtime) is False


def test_web_search_rejects_when_model_has_no_native_capability():
    runtime = make_runtime_config(base_url="https://example.com/v1", model="demo-model")

    try:
        asyncio.run(
            network_service.web_search(
                session=None,  # type: ignore[arg-type]
                runtime_config=runtime,
                query="latest news",
            )
        )
    except NetworkPolicyError as exc:
        assert "原生联网搜索能力" in str(exc)
    else:
        raise AssertionError("expected NetworkPolicyError")


def test_web_fetch_rejects_blocked_domain():
    runtime = make_runtime_config(network_overrides={"blocked_domains": ["example.com"]})

    try:
        asyncio.run(
            network_service.web_fetch(
                runtime_config=runtime,
                url="https://example.com/secret",
            )
        )
    except NetworkPolicyError as exc:
        assert "域名策略" in str(exc)
    else:
        raise AssertionError("expected NetworkPolicyError")


def test_public_summary_keeps_network_governance_only():
    global_summary = llm_config_service.get_global_summary()
    assert isinstance(global_summary.network.allowed_domains, list)
    assert isinstance(global_summary.network.blocked_domains, list)
