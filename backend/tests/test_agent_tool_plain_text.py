from app.runtime.engine import AgentEngine


def test_engine_preserves_string_public_output_without_json_encoding():
    result = {"status": "success", "public_output": "ID  NAME\n1   demo"}

    rendered = AgentEngine._render_tool_result_for_model(result, result["public_output"])

    assert rendered == "ID  NAME\n1   demo"


def test_engine_renders_tool_errors_as_recoverable_plain_text():
    result = {"error": "宿主工具 demo 调用失败（HTTP 400）：form_id 必须是正整数"}

    rendered = AgentEngine._render_tool_result_for_model(result, result)

    assert rendered.startswith("ERROR[TOOL_EXECUTION_FAILED] 宿主工具 demo 调用失败")
    assert "RETRYABLE: unknown" in rendered
    assert "NEXT:" in rendered
