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


def test_engine_renders_dict_result_as_git_style_text():
    result = {"summary": "任务清单已更新，共 3 项", "revision": 5, "status": "active"}

    rendered = AgentEngine._render_tool_result_for_model(result, result)

    assert "{" not in rendered and "}" not in rendered
    assert "summary: 任务清单已更新，共 3 项" in rendered
    assert "revision: 5" in rendered
    assert "status: active" in rendered


def test_engine_renders_list_of_dicts_as_bullets():
    result = {
        "items": [
            {"path": "/workspace", "name": "foo", "type": "file"},
            {"path": "/workspace/bar", "name": "bar", "type": "dir"},
        ]
    }

    rendered = AgentEngine._to_text(result)

    assert rendered == (
        "items:\n"
        "  - path: /workspace\n"
        "    name: foo\n"
        "    type: file\n"
        "  - path: /workspace/bar\n"
        "    name: bar\n"
        "    type: dir"
    )


def test_engine_renders_multiline_strings_and_scalar_lists():
    rendered = AgentEngine._to_text(
        {"exit_code": 0, "stdout": "hello\nworld", "stderr": "", "tags": ["a", "b", "c"]}
    )

    assert rendered == (
        "exit_code: 0\n"
        "stdout:\n"
        "  hello\n"
        "  world\n"
        "stderr:\n"
        "tags: a, b, c"
    )

