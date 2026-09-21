from app.services.tool_result_service import ToolExecutionResult, tool_result_renderer


def test_renderer_outputs_only_actionable_subagent_handle():
    result = ToolExecutionResult.success(
        "subagent.created",
        "子代理 数据分析 已创建",
        {
            "subagent_id": "subagent_1",
            "child_session_id": "sess_child_1",
            "name": "数据分析",
            "status": "running",
            "task": "分析销售数据",
        },
    )

    rendered = tool_result_renderer.render(result)

    assert rendered == "subagent_id: subagent_1"


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

    assert rendered.splitlines() == [
        "error: form_id 必须是正整数",
        "retryable: yes",
        "next: 修正 form_id 后重试。",
    ]


def test_renderer_outputs_file_content_without_protocol_decorations():
    result = ToolExecutionResult.success(
        "file.read",
        "已读取 /workspace/a.txt",
        {
            "file_path": "/workspace/a.txt",
            "content": "1  hello\n2  world",
            "start_line": 1,
            "end_line": 2,
            "total_lines": 2,
            "truncated": False,
            "size": 18,
        },
    )

    rendered = tool_result_renderer.render(result)

    assert rendered == "1  hello\n2  world"
    assert "file.read" not in rendered
    assert "summary:" not in rendered
    assert "truncated: no" not in rendered


def test_renderer_reports_file_truncation_with_line_range():
    result = ToolExecutionResult.success(
        "file.read",
        "已读取 /workspace/large.txt",
        {
            "file_path": "/workspace/large.txt",
            "content": "1  first",
            "start_line": 1,
            "end_line": 10,
            "total_lines": 100,
            "truncated": True,
        },
    )

    rendered = tool_result_renderer.render(result)

    assert rendered.endswith("[truncated: lines 1-10 of 100]")


def test_renderer_outputs_search_results_without_argument_echo():
    glob_result = ToolExecutionResult.success(
        "search.glob",
        "匹配到 2 个文件",
        {
            "pattern": "**/*.py",
            "files": ["a.py", "b.py"],
            "num_files": 2,
            "truncated": False,
            "duration_ms": 12,
        },
    )
    grep_result = ToolExecutionResult.success(
        "search.grep",
        "匹配到 1 行",
        {
            "mode": "content",
            "content": "a.py:12:hello",
            "num_lines": 1,
            "num_matches": 1,
            "num_files": 1,
            "truncated": False,
            "duration_ms": 8,
        },
    )

    assert tool_result_renderer.render(glob_result) == "a.py\nb.py"
    assert tool_result_renderer.render(grep_result) == "a.py:12:hello"


def test_renderer_outputs_directory_entries_without_summary():
    result = ToolExecutionResult.success(
        "workspace.listed",
        "/workspace 下共有 2 个条目",
        {
            "path": "/workspace",
            "item_count": 2,
            "items": [
                {"name": "src", "type": "dir"},
                {"name": "a.py", "type": "file", "size": 2048},
            ],
        },
    )

    rendered = tool_result_renderer.render(result)

    assert rendered == "dir src\nfile a.py  2.0KB"


def test_renderer_outputs_shell_content_and_nonzero_exit_code():
    result = ToolExecutionResult.partial(
        "shell.executed",
        "命令退出码为 1",
        {
            "shell": "bash",
            "executor": "container",
            "exit_code": 1,
            "stdout": "built",
            "stderr": "warning",
            "duration_ms": 30,
            "log_path": "/logs/a.log",
        },
    )

    rendered = tool_result_renderer.render(result)

    assert rendered == "built\nstderr:\n  warning\nexit_code: 1"


def test_renderer_outputs_empty_shell_result_explicitly():
    result = ToolExecutionResult.success(
        "shell.executed",
        "命令执行成功",
        {"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 3},
    )

    assert tool_result_renderer.render(result) == "(no output)"


def test_renderer_keeps_dynamic_host_payload_but_drops_timing():
    result = ToolExecutionResult.success(
        "host_tool.completed",
        "宿主工具 update_page 执行完成",
        {
            "page_id": "page_1",
            "duration_ms": 20,
            "sections": [{"id": "hero", "status": "updated"}],
        },
    )

    rendered = tool_result_renderer.render(result)

    assert "page_id: page_1" in rendered
    assert "sections:" in rendered
    assert "id: hero" in rendered
    assert "duration_ms" not in rendered
    assert "{" not in rendered


def test_renderer_limits_large_content_without_silent_data_loss():
    result = ToolExecutionResult.success(
        "file.read",
        "读取文件",
        {"file_path": "/workspace/large.txt", "content": "x" * 10_000},
    )

    rendered = tool_result_renderer.render(result)

    assert "[truncated:" in rendered
    assert len(rendered) <= tool_result_renderer.max_output_chars + 100


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
