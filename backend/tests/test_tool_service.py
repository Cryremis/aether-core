# backend/tests/test_tool_service.py
from __future__ import annotations

from app.schemas.llm import LlmNetworkConfig
from app.services.llm_config_service import RuntimeLlmConfig
from app.services.prompt_service import prompt_service
from app.services.runtime_state import runtime_state_service
from app.services.session_types import AgentSession
from app.services.tool_service import tool_service
from app.sandbox.models import SandboxCommandResult


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


def test_builtin_runtime_state_tools_are_registered():
    session = AgentSession(session_id="sess_runtime_tools", allow_network=False)
    tool_names = [item["function"]["name"] for item in tool_service.list_tool_schemas(session)]
    assert "update_workboard" in tool_names
    assert "request_user_input" in tool_names


def test_request_user_input_tool_returns_control_payload(tmp_path):
    from app.core.config import settings
    from app.services.session_service import session_service

    settings.storage_root = tmp_path / "storage"
    session = session_service.get_or_create("sess_request_user_input")

    result = __import__("asyncio").run(
        tool_service.execute(
            session,
            "request_user_input",
            {
                "title": "Need environment choice",
                "questions": [
                    {
                        "id": "env",
                        "header": "Environment",
                        "question": "Which environment should we target?",
                        "options": [{"label": "Staging"}, {"label": "Production"}],
                    }
                ],
            },
        )
    )

    assert result["control"]["type"] == "await_user_input"
    assert runtime_state_service.get_elicitation(session).pending is not None


def test_update_workboard_tool_supports_ops(tmp_path):
    from app.core.config import settings
    from app.services.session_service import session_service

    settings.storage_root = tmp_path / "storage"
    session = session_service.get_or_create("sess_update_workboard_ops")

    result = __import__("asyncio").run(
        tool_service.execute(
            session,
            "update_workboard",
            {
                "ops": [
                    {"op": "add_item", "id": "task_1", "title": "Plan work", "status": "pending"},
                    {"op": "update_item", "id": "task_1", "status": "completed"},
                ]
            },
        )
    )

    assert result["workboard"]["items"][0]["id"] == "task_1"
    assert result["workboard"]["items"][0]["status"] == "completed"


def test_sandbox_shell_reports_runtime_recreated(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services.session_service import session_service

    settings.storage_root = tmp_path / "storage"
    session = session_service.get_or_create("sess_runtime_recreated")

    async def fake_run_shell(*, workspace, command, shell):
        return SandboxCommandResult(
            command=command,
            shell=shell,
            executor="docker",
            exit_code=0,
            stdout="ok\n",
            stderr="",
            duration_ms=12,
            log_path="logs/cmd.json",
            runtime_metadata={
                "status": "recreated",
                "reason": "idle_ttl_expired",
                "generation": 2,
                "previous_generation": 1,
                "container_name": "aethercore-sess-demo-g2",
            },
        )

    monkeypatch.setattr("app.services.tool_service.sandbox_runner.run_shell", fake_run_shell)

    result = __import__("asyncio").run(
        tool_service.execute(
            session,
            "sandbox_shell",
            {
                "command": "echo ok",
                "shell": "bash",
            },
        )
    )

    assert result["runtime"]["status"] == "recreated"
    assert result["runtime_events"][0]["type"] == "runtime_recreated"
    assert "会话 runtime 已被重建" in result["injected_messages"][0]["content"]


def test_prompt_workspace_paths_use_container_paths(tmp_path):
    from app.core.config import settings
    from app.services.session_service import session_service

    settings.storage_root = tmp_path / "storage"
    session = session_service.get_or_create("sess_prompt_paths")

    rendered = prompt_service._render_template(
        "{{workspace.root_dir}}|{{workspace.input_dir}}|{{workspace.work_dir}}|{{workspace.logs_dir}}",
        session=session,
        conversation={"conversation_id": "conv-demo"},
        platform=None,
    )

    assert rendered == "/workspace|/workspace/input|/workspace/work|/workspace/logs"
