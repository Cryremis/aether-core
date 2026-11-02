from __future__ import annotations

import asyncio
import json

import pytest

from app.core.config import settings
from app.services.runtime_state import runtime_state_service
from app.services.session_types import AgentSession
from app.services.tool_argument_schema import validate_arguments
from app.services.tool_service import ToolRegistry, tool_service


def test_registry_preserves_full_schema_and_owns_its_copy():
    schema = {
        "type": "object",
        "$defs": {"operation": {"type": "object", "required": ["script"],
                               "properties": {"script": {"type": "string"}}}},
        "properties": {"ops": {"type": "array", "items": {"$ref": "#/$defs/operation"}}},
        "required": ["ops"],
        "anyOf": [{"required": ["ops"]}],
        "examples": [{"ops": [{"script": 'echo "hello"'}]}],
        "additionalProperties": False,
    }
    registry = ToolRegistry()
    registry.register("contract", "test", schema, lambda *_: None)
    published = registry.get_schemas()[0]["function"]["parameters"]
    assert published == schema
    assert validate_arguments(published, {"ops": [{"script": 'echo "hello"'}]}) is None
    assert "missing required fields: ops" in validate_arguments(published, {})
    assert "$.ops: expected array" in validate_arguments(published, {"ops": "[]"})
    assert "$.ops[0]: missing required fields: script" in validate_arguments(published, {"ops": [{}]})
    published["required"].clear()
    schema["$defs"].clear()
    assert registry.get_schemas()[0]["function"]["parameters"]["required"] == ["ops"]
    assert registry.get_schemas()[0]["function"]["parameters"]["$defs"]


def test_native_arrays_execute_but_quoted_arrays_do_not_mutate_workboard(tmp_path):
    settings.storage_root = tmp_path / "storage"
    session = AgentSession("contract_workboard")
    operation = {"op": "add_item", "title": 'Run echo "hello"', "status": "pending"}
    invalid = asyncio.run(tool_service.execute(session, "update_workboard", {"ops": json.dumps([operation])}))
    assert invalid.error.code == "TOOL_ARGUMENTS_INVALID"
    assert "$.ops" in invalid.error.message
    assert "不要将其序列化" in invalid.error.message
    assert runtime_state_service.get_workboard(session).items == []
    valid = asyncio.run(tool_service.execute(session, "update_workboard", {"ops": [operation]}))
    assert valid.status == "success"
    assert runtime_state_service.get_workboard(session).items[0].title == operation["title"]


def test_free_text_question_matches_published_schema_and_creates_pending_input(tmp_path):
    settings.storage_root = tmp_path / "storage"
    session = AgentSession("contract_free_text")
    arguments = {"title": "服务器信息", "questions": [{"header": "地址", "question": "请提供目标服务器地址。"}]}
    schema = next(s["function"]["parameters"] for s in tool_service.list_tool_schemas(session)
                  if s["function"]["name"] == "request_user_input")
    assert validate_arguments(schema, arguments) is None
    result = asyncio.run(tool_service.execute(session, "request_user_input", arguments))
    assert result.status == "success"
    assert result.control["type"] == "await_user_input"
    assert runtime_state_service.get_elicitation(session).pending.questions[0].options == []


@pytest.mark.parametrize("raw", ['"{}"', "[]", "true", "null", "'a string'"])
def test_outer_arguments_must_be_an_object(raw):
    with pytest.raises(ValueError, match="JSON 对象"):
        tool_service.parse_tool_arguments(raw)


def test_argument_errors_do_not_echo_large_invalid_payloads():
    value = "secret_script_contents" * 10000
    error = validate_arguments({"type": "string", "maxLength": 10}, value)
    assert error == "$: constraint maxLength=10 is not satisfied"


@pytest.mark.parametrize("operation,missing", [
    ({"op": "add_item"}, "title"),
    ({"op": "update_item", "status": "completed"}, "id"),
    ({"op": "reorder_items"}, "ordered_ids"),
    ({"op": "replace_all"}, "items"),
])
def test_workboard_schema_rejects_incomplete_actions_before_reporting_success(tmp_path, operation, missing):
    settings.storage_root = tmp_path / "storage"
    result = asyncio.run(tool_service.execute(AgentSession("missing_action_field"), "update_workboard", {"ops": [operation]}))
    assert result.status == "error"
    assert missing in result.error.message


def test_workboard_schema_accepts_status_only_updates(tmp_path):
    settings.storage_root = tmp_path / "storage"
    result = asyncio.run(tool_service.execute(AgentSession("status_only"), "update_workboard", {"status": "idle"}))
    assert result.status == "success"


@pytest.mark.parametrize("protocol", ["openai-compatible", "openai-responses", "anthropic-messages"])
def test_model_protocols_receive_the_complete_registered_contract(protocol):
    from dataclasses import replace
    from app.services.context.provider_adapters import native_request
    from test_llm_client import make_runtime_config
    registry = ToolRegistry()
    original = {
        "$defs": {"Value": {"type": "string"}},
        "properties": {"value": {"$ref": "#/$defs/Value"}},
        "required": ["value"],
        "anyOf": [{"required": ["value"]}],
    }
    registry.register("contract", "contract", original, lambda *_: None)
    tools = registry.get_schemas()
    config = replace(make_runtime_config(), api_format=protocol)
    request = native_request(config, [], {"tools": tools, "max_tokens": 100})
    tool = request["tools"][0]
    schema = (tool["function"]["parameters"] if protocol == "openai-compatible" else
              tool["parameters"] if protocol == "openai-responses" else tool["input_schema"])
    assert schema["$defs"] == original["$defs"]
    assert schema["required"] == ["value"]
    assert schema["anyOf"] == original["anyOf"]
    assert validate_arguments(schema, {"value": "text"}) is None
    assert validate_arguments(schema, {}) is not None
