from __future__ import annotations

from app.services.transcript_service import transcript_service


def test_transcript_service_merges_tool_result_into_assistant_tool_block():
    messages = [
        {
            "role": "user",
            "content": "run command",
            "message_id": "m_user",
            "visible_in_transcript": True,
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "sandbox_shell",
                        "arguments": '{"command":"echo hi"}',
                    },
                }
            ],
            "blocks": [
                {
                    "id": "call_1",
                    "kind": "tool",
                    "title": "sandbox_shell",
                    "meta": "bash",
                    "argumentsText": '{"command":"echo hi"}',
                    "outputText": "",
                    "status": "running",
                }
            ],
            "visible_in_transcript": False,
            "message_id": "m_assistant_hidden",
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "tool_name": "sandbox_shell",
            "content": '{"stdout":"hi\\n","aborted":false}',
            "message_id": "m_tool",
            "visible_in_transcript": True,
        },
        {
            "role": "assistant",
            "content": "done",
            "blocks": [
                {"id": "content_1", "kind": "content", "content": "done", "status": "done"},
                {"id": "elapsed_1", "kind": "elapsed", "elapsed_ms": 120},
            ],
            "message_id": "m_assistant_final",
            "visible_in_transcript": True,
        },
    ]

    transcript = transcript_service.build_chat_transcript(messages)

    assert len(transcript) == 3
    assert transcript[0]["role"] == "user"
    assert transcript[1]["role"] == "assistant"
    assert transcript[1]["blocks"][0]["kind"] == "tool"
    assert transcript[1]["blocks"][0]["status"] == "done"
    assert "stdout" in transcript[1]["blocks"][0]["outputText"]
    assert transcript[2]["role"] == "assistant"
    assert transcript[2]["blocks"][0]["kind"] == "content"
    assert transcript[2]["elapsedMs"] == 120


def test_transcript_service_uses_canonical_display_for_sandbox_shell_alias():
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "sandboxshell",
                        "arguments": '{"command":"npm run build","shell":"bash"}',
                    },
                }
            ],
            "message_id": "m_assistant_hidden",
            "visible_in_transcript": True,
        }
    ]

    transcript = transcript_service.build_chat_transcript(messages)
    assert len(transcript) == 1
    block = transcript[0]["blocks"][0]
    assert block["kind"] == "tool"
    assert block["title"] == "npm"
    assert block["meta"] == "bash"


def test_transcript_service_preserves_runtime_notice_block():
    messages = [
        {
            "role": "assistant",
            "content": "runtime settled",
            "blocks": [
                {
                    "id": "notice_1",
                    "kind": "runtime_notice",
                    "eventType": "runtime_recreated",
                    "title": "沙箱已重建",
                    "detail": "配置变化",
                },
                {"id": "content_1", "kind": "content", "content": "runtime settled", "status": "done"},
            ],
            "message_id": "m_runtime",
            "visible_in_transcript": True,
        }
    ]

    transcript = transcript_service.build_chat_transcript(messages)
    assert len(transcript) == 1
    assert transcript[0]["role"] == "assistant"
    assert transcript[0]["blocks"][0]["kind"] == "runtime_notice"
    assert transcript[0]["blocks"][0]["eventType"] == "runtime_recreated"
