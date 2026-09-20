from app.services.tool_result_service import ToolExecutionResult, tool_result_renderer


def test_renderer_outputs_cli_style_text_for_successful_results():
    result = ToolExecutionResult.success(
        "subagent.created",
        "子代理 数据分析 已创建",
        {
            "subagent_run_id": "run_sub_1",
            "child_session_id": "sess_child_1",
            "name": "数据分析",
            "status": "running",
            "task": "分析销售数据",
        },
    )

    rendered = tool_result_renderer.render(result)

    assert rendered.startswith("subagent.created: success")
    assert "summary: 子代理 数据分析 已创建" in rendered
    assert "subagent_run_id: run_sub_1" in rendered
    assert "child_session_id: sess_child_1" in rendered
    assert "{" not in rendered


def test_renderer_outputs_recoverable_error_protocol():
    result = ToolExecutionResult.failure(
        "host_tool.completed",
        "宿主工具调用失败",
        code="HOST_TOOL_HTTP_400",
        message="form_id 必须是正整数",
        retryable=True,
        next_action="修正 form_id 后重试。",
    )

    rendered = tool_result_renderer.render(result)
    payload = result.public_payload()

    assert rendered.startswith("host_tool.completed: error")
    assert "code: HOST_TOOL_HTTP_400" in rendered
    assert "retryable: yes" in rendered
    assert "next: 修正 form_id 后重试。" in rendered
    assert payload["error"]["retryable"] is True


def test_renderer_limits_large_content_without_silent_data_loss():
    result = ToolExecutionResult.success(
        "file.read",
        "读取文件",
        {"file_path": "/workspace/large.txt", "content": "x" * 10_000},
    )

    rendered = tool_result_renderer.render(result)

    assert "[truncated:" in rendered
    assert len(rendered) <= tool_result_renderer.max_output_chars + 100


def test_renderer_renders_dynamic_host_payload_as_structured_text():
    result = ToolExecutionResult.success(
        "host_tool.completed",
        "宿主工具 update_page 执行完成",
        {
            "page_id": "page_1",
            "sections": [{"id": "hero", "status": "updated"}],
        },
    )

    rendered = tool_result_renderer.render(result)

    assert "page_id: page_1" in rendered
    assert "sections:" in rendered
    assert "id: hero" in rendered
    assert "status: updated" in rendered
    assert "{" not in rendered


def test_renderer_redacts_sensitive_fields_for_model_context():
    result = ToolExecutionResult.success(
        "host_tool.completed",
        "宿主工具 login 执行完成",
        {"token": "secret-value", "nested": {"password": "secret-value"}},
    )

    rendered = tool_result_renderer.render(result)

    assert "token: [redacted]" in rendered
    assert "password: [redacted]" in rendered
    assert "secret-value" not in rendered
