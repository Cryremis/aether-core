# backend/tests/test_tool_service.py
from __future__ import annotations

from app.schemas.llm import LlmNetworkConfig
from app.services.llm_config_service import RuntimeLlmConfig
from app.services.session_types import AgentSession
from app.services.tool_service import tool_service


def make_runtime_config(*, base_url: str, model: str) -> RuntimeLlmConfig:
    return RuntimeLlmConfig(
        scope="global",
        provider_kind="litellm",
        api_format="openai-compatible",
        base_url=base_url,
        model=model,
        api_key="demo-key",
        extra_headers={},
        extra_body={},
        network=LlmNetworkConfig(enabled=True),
        enabled=True,
    )


def test_network_tools_follow_session_allow_network(monkeypatch):
    monkeypatch.setattr(
        tool_service,
        "_resolve_runtime_config",
        lambda session: make_runtime_config(base_url="https://open.bigmodel.cn/api/paas/v4", model="glm-4-air"),
    )

    session = AgentSession(session_id="sess_no_network", allow_network=False)
    tool_names = [item["function"]["name"] for item in tool_service.list_tool_schemas(session)]
    assert "web_search" not in tool_names
    assert "web_fetch" not in tool_names

    session.allow_network = True
    tool_names = [item["function"]["name"] for item in tool_service.list_tool_schemas(session)]
    assert "web_search" in tool_names
    assert "web_fetch" in tool_names


def test_web_search_hidden_when_model_has_no_native_support(monkeypatch):
    monkeypatch.setattr(
        tool_service,
        "_resolve_runtime_config",
        lambda session: make_runtime_config(base_url="https://example.com/v1", model="demo-model"),
    )

    session = AgentSession(session_id="sess_no_native", allow_network=True)
    tool_names = [item["function"]["name"] for item in tool_service.list_tool_schemas(session)]
    assert "web_search" not in tool_names
    assert "web_fetch" in tool_names


def test_web_search_visible_for_dashscope_native_support(monkeypatch):
    monkeypatch.setattr(
        tool_service,
        "_resolve_runtime_config",
        lambda session: make_runtime_config(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen-plus",
        ),
    )

    session = AgentSession(session_id="sess_dashscope_native", allow_network=True)
    tool_names = [item["function"]["name"] for item in tool_service.list_tool_schemas(session)]
    assert "web_search" in tool_names
