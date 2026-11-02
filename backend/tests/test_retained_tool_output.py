from __future__ import annotations

import asyncio
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace
from uuid import uuid4

import pytest

from app.services.session_types import AgentSession
from app.services.tool_service import ToolCatalogSnapshot, tool_service
from app.services.tool_result_service import ToolExecutionResult, ToolResultRenderer
from app.services.tool_output_reader import read_tool_output


def test_full_host_output_survives_preview_and_unicode_paging():
    original = "header\n" + "一行🙂\n" * 12000 + "END\nnext_cursor=source-cursor"
    result = replace(ToolExecutionResult.success("host_tool.completed", "read", {"public_output": original}),
                     result_ref={"run_id": "run", "tool_call_id": "call"})
    preview = ToolResultRenderer().render(result)
    assert len(preview) <= ToolResultRenderer.max_output_chars
    assert "tool_result_read" in preview and "omitted" in preview
    assert "END" in preview and "header" in preview
    receipt, offset, recovered = result.public_payload(), 0, []
    while True:
        page = read_tool_output(receipt, {"offset": offset, "limit": 701})
        recovered.append(page["content"])
        offset = page["next_offset"]
        if not page["has_more"]:
            break
    assert "".join(recovered) == original


def test_json_pointer_search_recovers_scalar_past_old_4000_limit():
    receipt = {"data": {"data": {"logs": [{"content": "x" * 50000 + "\nERROR sentinel\n"}]}}}
    page = read_tool_output(receipt, {"path": "/data/data/logs/0/content", "query": "ERROR", "limit": 12000})
    assert page["offset"] == 50001 and page["content"] == "ERROR sentinel\n"
    empty = read_tool_output(receipt, {"query": "does-not-exist"})
    assert empty["matched"] is False and not empty["has_more"]


def test_stored_receipt_is_scoped_to_session_and_readable_after_reload(tmp_path, monkeypatch):
    from app.services.context.history_store import history_store
    from app.services.store import store_service
    monkeypatch.setattr(store_service, "_db_path", tmp_path / "receipts.db")
    session = AgentSession(session_id="test_result_" + uuid4().hex)
    run, call = uuid4().hex, uuid4().hex
    payload = ToolExecutionResult.success("host_tool.completed", "read", {"public_output": "a" * 40000 + "sentinel"}).public_payload()
    state, _ = history_store.claim_tool(session.session_id, run, call, "digest")
    assert state == "claimed"
    history_store.finish_tool(session.session_id, run, call, payload)
    result = asyncio.run(tool_service._handle_tool_result_read(session, {"run_id": run, "tool_call_id": call, "offset": 40000}))
    assert result.data["content"] == "sentinel"
    denied = asyncio.run(tool_service._handle_tool_result_read(AgentSession(session_id="other"), {"run_id": run, "tool_call_id": call}))
    assert denied.error.code == "TOOL_RESULT_NOT_FOUND"


def test_business_timeout_is_preserved_and_call_timeout_is_separate(monkeypatch):
    descriptor = {"name": "host_wait", "input_schema": {"type": "object", "properties": {
        "timeout_seconds": {"type": "number"}, "task_id": {"type": "string"}}}}
    snapshot = ToolCatalogSnapshot(revision=1, fingerprint="test", schemas=(),
                                   host_descriptors_by_name=MappingProxyType({"host_wait": descriptor}))
    captured = {}
    async def invoke(_session, _descriptor, arguments, *, timeout_seconds=None):
        captured.update(arguments=arguments, timeout=timeout_seconds)
        return ToolExecutionResult.success("host_tool.completed", "done")
    monkeypatch.setattr(tool_service, "_invoke_host_tool", invoke)
    result = asyncio.run(tool_service.execute(AgentSession(session_id="timeout_business"), "host_wait",
        {"task_id": "task", "timeout_seconds": 86400.5, "call_timeout_seconds": 7}, catalog_snapshot=snapshot))
    assert result.status == "success"
    assert captured == {"arguments": {"task_id": "task", "timeout_seconds": 86400.5}, "timeout": 7}


def test_tool_result_page_keeps_multiline_body_without_scalar_clipping():
    body = "line\n" * 2400
    rendered = ToolResultRenderer().render(ToolExecutionResult.success("tool.result.page", "read", {"content": body, "has_more": False}))
    assert body in rendered

def test_only_host_business_timeouts_add_the_separate_transport_parameter(monkeypatch):
    from app.services.tool_catalog_service import HostToolCatalogSnapshot
    descriptor = {"name": "host_wait", "description": "wait", "input_schema": {
        "type": "object", "properties": {"timeout_seconds": {"type": "number"}}}}
    snapshot = HostToolCatalogSnapshot(revision=1, fingerprint="test", descriptors=(descriptor,),
        descriptors_by_name=MappingProxyType({"host_wait": descriptor}),
        sources_by_name=MappingProxyType({"host_wait": ("test",)}))
    monkeypatch.setattr(tool_service, "_resolve_runtime_config",
                        lambda session: SimpleNamespace(network=SimpleNamespace(enabled=False)))
    schemas = tool_service.list_tool_schemas(AgentSession(session_id="timeout_schema"), _host_snapshot=snapshot)
    host = next(schema for schema in schemas if schema["function"]["name"] == "host_wait")
    assert host["function"]["parameters"]["properties"]["timeout_seconds"] == {"type": "number"}
    assert "call_timeout_seconds" in host["function"]["parameters"]["properties"]
    assert "call_timeout_seconds" not in descriptor["input_schema"]["properties"]
    assert "call_timeout_seconds" not in tool_service.get_context_compact_schema()["function"]["parameters"]["properties"]


def test_json_host_result_is_used_directly_and_default_read_path_matches_preview():
    import json
    structured = {"summary": "metrics", "data": {"sub_tasks": [{"id": "case3", "metrics": {"ttft": 326360}}]}}
    result = ToolExecutionResult.success("host_tool.completed", "read", {
        "output_format": "json", "public_output": "display-only text", "data": structured,
    })
    preview = ToolResultRenderer().render(result)
    assert json.loads(preview) == structured
    receipt = result.public_payload()
    page = read_tool_output(receipt, {})
    assert page["path"] == "/data/data"
    assert json.loads(page["content"]) == structured
    selected = read_tool_output(receipt, {"path": "/data/data/data/sub_tasks/0/metrics/ttft"})
    assert selected["content"] == "326360"


def test_host_error_prefers_diagnostic_text_and_keeps_machine_readable_code():
    import httpx
    payload = {"status": "error", "public_output": "error: node_id is required\ncode: INVALID_ARGUMENTS",
               "data": {"data": {"detail": {"code": "INVALID_ARGUMENTS", "fields": ["node_id"], "retryable": False}}}}
    response = httpx.Response(422, json=payload)
    result = tool_service._host_failure("monitor", response)
    assert result.error.code == "INVALID_ARGUMENTS"
    assert "node_id is required" in result.error.message
    assert not result.error.retryable
    assert result.data["host_response"] == payload
    assert "public_output" not in result.error.message


def test_failed_host_receipt_keeps_original_fields_and_is_not_an_unknown_effect(tmp_path, monkeypatch):
    from app.services.context.history_store import history_store
    from app.services.store import store_service
    import httpx
    monkeypatch.setattr(store_service, "_db_path", tmp_path / "host-errors.db")
    session = AgentSession("failed_receipt")
    async def invoke(*args, **kwargs):
        return tool_service._host_failure("monitor", httpx.Response(503, json={
            "detail": {"code": "DATASOURCE_UNAVAILABLE", "message": "Prometheus unavailable", "retryable": True}}))
    monkeypatch.setattr(tool_service, "_execute_with_timeout", invoke)
    result = asyncio.run(tool_service.execute(session, "monitor", {}, run_id="run", tool_call_id="call"))
    assert result.error.code == "DATASOURCE_UNAVAILABLE"
    receipt = history_store.tool_receipt(session.session_id, "run", "call")
    assert receipt["error"]["code"] == "DATASOURCE_UNAVAILABLE"
    assert receipt["data"]["host_response"]["detail"]["retryable"] is True
