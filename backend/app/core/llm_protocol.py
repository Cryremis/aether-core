"""API 与实际客户端共享的运行时请求边界。"""

RUNTIME_CONTROL_FIELDS = frozenset({
    "model", "messages", "input", "stream", "tool_choice", "n", "previous_response_id",
    "system", "instructions", "conversation", "truncation", "context_management", "background",
})
