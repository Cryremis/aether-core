from __future__ import annotations

import asyncio
from types import MappingProxyType

from app.services.session_types import AgentSession
from app.services.tool_catalog_service import HostToolCatalogSnapshot
from app.services.tool_result_service import ToolExecutionResult
from app.services.tool_service import ToolCatalogSnapshot, tool_service


class _FakeNetworkConfig:
    enabled = False


class _FakeRuntimeConfig:
    network = _FakeNetworkConfig()


def _host_snapshot() -> HostToolCatalogSnapshot:
    descriptor = {
        "name": "host_wait",
        "description": "wait for a host task",
        "endpoint": "/api/tools/wait",
        "method": "POST",
        "requires_auth": False,
        "auth_inject": False,
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
            "additionalProperties": False,
        },
    }
    return HostToolCatalogSnapshot(
        revision=1,
        fingerprint="test",
        descriptors=(descriptor,),
        descriptors_by_name=MappingProxyType({"host_wait": descriptor}),
        sources_by_name=MappingProxyType({"host_wait": ("host:test",)}),
    )


def _catalog_snapshot() -> ToolCatalogSnapshot:
    host_snapshot = _host_snapshot()
    return ToolCatalogSnapshot(
        revision=host_snapshot.revision,
        fingerprint=host_snapshot.fingerprint,
        schemas=(),
        host_descriptors_by_name=host_snapshot.descriptors_by_name,
    )


def test_every_tool_schema_exposes_timeout_seconds(monkeypatch):
    monkeypatch.setattr(
        tool_service,
        "_resolve_runtime_config",
        lambda _session: _FakeRuntimeConfig(),
    )
    session = AgentSession(session_id="sess_tool_timeout_schema")

    schemas = tool_service.list_tool_schemas(session, _host_snapshot=_host_snapshot())

    assert schemas
    for schema in schemas:
        properties = schema["function"]["parameters"]["properties"]
        assert "timeout_seconds" in properties


def test_host_tool_timeout_is_engine_reserved_and_not_forwarded(monkeypatch):
    captured = {}

    async def fake_invoke_host_tool(
        _session,
        _descriptor,
        arguments,
        *,
        timeout_seconds=None,
    ):
        captured["arguments"] = arguments
        captured["timeout_seconds"] = timeout_seconds
        return ToolExecutionResult.success("host_tool.completed", "宿主工具执行完成")

    monkeypatch.setattr(tool_service, "_invoke_host_tool", fake_invoke_host_tool)
    session = AgentSession(session_id="sess_host_timeout_strip")

    result = asyncio.run(
        tool_service.execute(
            session,
            "host_wait",
            {"task_id": "task-1", "timeout_seconds": 7},
            catalog_snapshot=_catalog_snapshot(),
        )
    )

    assert result.status == "success"
    assert captured["arguments"] == {"task_id": "task-1"}
    assert captured["timeout_seconds"] == 7


def test_tool_execution_timeout_returns_structured_failure(monkeypatch):
    async def slow_handler(_session, _arguments):
        await asyncio.sleep(1.1)
        return ToolExecutionResult.success("tool.completed", "不应到达")

    monkeypatch.setattr(
        tool_service._registry,
        "get_handler",
        lambda _name: slow_handler,
    )
    session = AgentSession(session_id="sess_tool_timeout")

    result = asyncio.run(
        tool_service.execute(session, "slow_tool", {"timeout_seconds": 1})
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "TOOL_EXECUTION_TIMEOUT"
    assert result.error.retryable is True
    assert result.data["timeout_seconds"] == 1


def test_invalid_tool_timeout_returns_structured_failure(monkeypatch):
    async def unreachable_handler(_session, _arguments):
        raise AssertionError("不应执行工具")

    monkeypatch.setattr(
        tool_service._registry,
        "get_handler",
        lambda _name: unreachable_handler,
    )
    session = AgentSession(session_id="sess_invalid_tool_timeout")

    result = asyncio.run(
        tool_service.execute(session, "invalid_timeout_tool", {"timeout_seconds": 0})
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "TOOL_TIMEOUT_INVALID"
    assert result.error.retryable is True


def test_builtin_timeout_argument_is_preserved_for_tool_semantics(monkeypatch):
    captured = {}

    async def fake_sandbox_shell(
        _session,
        arguments,
        *,
        run_id=None,
        tool_call_id=None,
        output_event_callback=None,
    ):
        captured["arguments"] = arguments
        return ToolExecutionResult.success("shell.executed", "命令已执行")

    monkeypatch.setattr(tool_service, "_handle_sandbox_shell", fake_sandbox_shell)
    session = AgentSession(session_id="sess_builtin_timeout")

    result = asyncio.run(
        tool_service.execute(
            session,
            "sandbox_shell",
            {"command": "echo ok", "timeout_seconds": 3},
        )
    )

    assert result.status == "success"
    assert captured["arguments"]["timeout_seconds"] == 3
