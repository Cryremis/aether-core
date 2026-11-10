"""压缩后的模型窗口、完整用户历史与仅管理员可见的摘要必须分别走真实读取链路。"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.auth_service import auth_service
from app.services.context.message_adapter import context_message_adapter as adapter
from app.services.context.history_store import history_store
from app.services.context.summarizer import compaction_summarizer
from app.services.llm_client import llm_client
from app.services.session_service import session_service
from app.services.store import store_service
from app.services.timeline_service import timeline_service
from app.services.transcript_service import transcript_service
from test_auth_and_sessions import initialize_isolated_runtime
from test_context_compaction import FakeLlmRuntime, make_pipeline
from test_llm_client import make_runtime_config


def setup_session(tmp_path):
    initialize_isolated_runtime(tmp_path)
    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    session = session_service.get_or_create()
    row = store_service.create_conversation(session_id=session.session_id, title="压缩审计", host_name="test",
        owner_user_id=admin.user_id, platform_id=session.platform_id)
    session.owner_user_id = admin.user_id
    session.conversation_id = row["conversation_id"]
    for turn in range(1, 9):
        session.messages.extend([
            adapter.make_user_message(f"第{turn}轮真实任务 " + "abcde " * 140, turn_index=turn),
            adapter.make_assistant_message(content=f"第{turn}轮真实回答 " + "done " * 25, turn_index=turn),
        ])
    session_service.persist(session)
    pipeline = make_pipeline()
    pipeline.bind_persister(session_service)
    return session, row, pipeline


def headers():
    token = auth_service.login_with_password(settings.auth_system_admin_username, settings.auth_system_admin_password).token
    return {"Authorization": f"Bearer {token}"}


def test_workbench_pages_visible_history_and_large_tool_output(tmp_path):
    session, _, _ = setup_session(tmp_path)
    output = "日志" * 40_000
    latest = adapter.make_assistant_message(content=None, turn_index=9, blocks=[{
        "id": "large-output", "kind": "tool", "toolName": "sandbox_shell", "title": "执行命令",
        "outputText": output, "status": "done",
    }])
    session.messages.append(latest)
    session_service.persist(session)
    session_service._sessions.pop(session.session_id)
    client = TestClient(app)
    path = f"/api/v1/agent/sessions/{session.session_id}"
    auth = headers()
    assert client.get(f"{path}/transcript").status_code == 401
    result = client.get(path, params={"transcript_limit": 4}, headers=auth).json()["data"]
    assert "messages" not in result
    assert result["message_count"] == 17
    assert len(result["transcript"]) == 4
    block = result["transcript"][-1]["blocks"][0]
    assert block["detailAvailable"] and block["outputText"] == output[:2048]
    cursor = result["transcript_page"]["next_cursor"]
    older = client.get(f"{path}/transcript", params={"before": cursor, "limit": 4}, headers=auth).json()["data"]
    assert older["transcript_page"]["end"] == result["transcript_page"]["start"]
    assert not ({item["id"] for item in older["transcript"]} & {item["id"] for item in result["transcript"]})
    detail_path = f"{path}/transcript/{latest['message_id']}/tools/large-output"
    detail = client.get(detail_path, headers=auth).json()["data"]
    assert detail["text"] == output[:32768] and detail["next_offset"] == 32768
    last = client.get(detail_path, params={"offset": 65536}, headers=auth).json()["data"]
    assert last == {"text": output[65536:], "next_offset": None}
    restored = session_service.get_or_create(session.session_id)
    restored.context_state["timeline"] = {"revision": 1}
    assert client.get(f"{path}/transcript", params={"before": cursor}, headers=auth).status_code == 409
    legacy = client.get(path, headers=auth).json()["data"]
    assert len(legacy["transcript"]) == 17 and "messages" in legacy


def test_multiple_compactions_keep_full_user_history_and_audit_summaries_after_reload(tmp_path):
    session, _, pipeline = setup_session(tmp_path)
    original_users = [m["content"] for m in session.messages if m["role"] == "user"]
    for turn in (9, 10):
        asyncio.run(pipeline.compact_session_async(session=session, llm_runtime=FakeLlmRuntime(),
            messages=session.messages, turn_index=turn, max_output_tokens=1000))
        session.messages.extend([
            adapter.make_user_message(f"第{turn}轮新任务", turn_index=turn),
            adapter.make_assistant_message(content=f"第{turn}轮新回答", turn_index=turn),
        ])
        session_service.persist(session)
    session_id = session.session_id
    session_service._sessions.clear()
    client = TestClient(app)
    auth = headers()
    public = client.get(f"/api/v1/agent/sessions/{session_id}", headers=auth)
    assert public.status_code == 200
    public_data = public.json()["data"]
    displayed = [m["content"] for m in public_data["transcript"] if m["role"] == "user"]
    assert displayed[:8] == original_users
    assert len(displayed) == 10
    assert "历史任务交接" not in json.dumps(public_data, ensure_ascii=False)
    audit = client.get(f"/api/v1/admin/conversations/{session_id}", headers=auth)
    assert audit.status_code == 200
    transcript = audit.json()["data"]["transcript"]
    paged_audit = client.get(f"/api/v1/admin/conversations/{session_id}?limit=100", headers=auth)
    assert paged_audit.status_code == 200
    assert paged_audit.json()["data"]["transcript"] == transcript
    checkpoints = [m for m in transcript if m["role"] == "system_event"]
    assert len(checkpoints) == 2
    assert all("历史任务交接" in checkpoint["summary"] for checkpoint in checkpoints)
    first = transcript.index(checkpoints[0])
    assert transcript[first - 1]["role"] == "assistant"
    assert transcript[first + 1]["content"] == "第9轮新任务"
    restored = session_service.get_or_create(session_id)
    assert len(restored.compaction_audit) == 2
    assert len(restored.messages) < len(history_store.expand_history(session_id, restored.messages))
    assert all("历史任务交接" not in str(m) for m in adapter.to_api_messages(restored.messages)
               if m.get("role") == "system")


def test_skill_context_is_hidden_from_public_and_audit_but_keeps_model_order(tmp_path):
    session, _, _ = setup_session(tmp_path)
    secret = "SKILL_FULL_CONTENT_MUST_NOT_APPEAR_IN_AUDIT"
    injected = adapter.ensure_runtime_metadata({"role": "user", "content": secret,
        "kind": "skill_context", "visible_in_transcript": False}, turn_index=8)
    session.messages.append(injected)
    session.messages.append(adapter.make_assistant_message(content="读取技能之后的回答", turn_index=8))
    session_service.persist(session)
    wire = adapter.to_api_messages(session.messages)
    index = next(i for i, m in enumerate(wire) if m.get("content") == secret)
    assert index > 0 and wire[index + 1]["content"] == "读取技能之后的回答"
    assert wire[index - 1]["content"].startswith("第8轮真实回答")
    client = TestClient(app)
    auth = headers()
    for path in (f"/api/v1/agent/sessions/{session.session_id}", f"/api/v1/admin/conversations/{session.session_id}"):
        response = client.get(path, headers=auth)
        assert response.status_code == 200
        assert secret not in response.text
        assert "读取技能之后的回答" in response.text


def test_native_opaque_checkpoint_is_audited_without_exposing_protocol_blob(tmp_path, monkeypatch):
    session, _, pipeline = setup_session(tmp_path)

    async def compact(config, messages):
        from app.services.context.provider_adapters import binding
        return {"binding": binding(config), "items": [{"type": "compaction", "encrypted_content": "PRIVATE_OPAQUE_BLOB"}], "complete_window": True}

    monkeypatch.setattr(llm_client, "compact_native", compact)
    runtime = replace(make_runtime_config(), api_format="openai-responses", sampling={"native_compaction": True,
        "deployment_profile": {"context_window": 64000}})
    asyncio.run(pipeline.compact_session_async(session=session, llm_runtime=runtime, messages=session.messages,
        turn_index=9, max_output_tokens=1000))
    audit = transcript_service.build_audit_transcript(session)
    event = next(m for m in audit if m["role"] == "system_event")
    assert "未提供可读摘要" in event["summary"]
    assert "PRIVATE_OPAQUE_BLOB" not in json.dumps(audit)
    assert len([m for m in audit if m["role"] == "user"]) == 8


def test_no_progress_does_not_create_success_audit_event(tmp_path):
    from app.services.context.runtime_types import ContextOverflowError
    session, _, pipeline = setup_session(tmp_path)
    session.messages = [adapter.make_user_message("hello", turn_index=1)]
    with pytest.raises(ContextOverflowError, match="没有可进一步缩减"):
        asyncio.run(pipeline.compact_session_async(session=session, llm_runtime=FakeLlmRuntime(),
            messages=session.messages, turn_index=9, max_output_tokens=1000))
    assert session.compaction_audit == []


def test_fork_before_compaction_does_not_include_future_audit_event(tmp_path):
    session, row, pipeline = setup_session(tmp_path)
    original = list(session.messages)
    pipeline._record_compaction(session, original, before=100, after=50, cause="agent",
        strategy="llm_checkpoint", summary="不属于旧分支的未来摘要")
    result = timeline_service.fork_from_message(source_session=session, source_conversation=row,
        source_message_id=original[3]["message_id"])
    assert result.session.compaction_audit == []
    assert "未来摘要" not in str(transcript_service.build_audit_transcript(result.session))


def test_same_run_projection_is_not_duplicated_across_checkpoint_sources():
    from app.services.session_types import AgentSession
    session = AgentSession("dedup-projection", messages=[
        adapter.make_assistant_message(content="先前部分回答", turn_index=1, run_id="one-run"),
        adapter.make_assistant_message(content="完整回答", turn_index=1, run_id="one-run"),
    ])
    transcript = transcript_service.build_persisted_transcript(session.messages)
    assert len(transcript) == 1
    assert transcript[0]["blocks"][0]["content"] == "完整回答"


def test_skill_injection_waits_for_the_whole_tool_group_before_next_model_input():
    calls = [{"id": name, "type": "function", "function": {"name": name, "arguments": "{}"}}
             for name in ("invoke_skill", "inspect")]
    messages = [adapter.make_user_message("当前任务", turn_index=1),
        adapter.make_assistant_message(tool_calls=calls, turn_index=1),
        adapter.make_tool_message(tool_call_id="invoke_skill", tool_name="invoke_skill", content="已读取技能", turn_index=1),
        adapter.make_internal_user_message("当前读取的技能规则", turn_index=1, kind="skill_context"),
        adapter.make_tool_message(tool_call_id="inspect", tool_name="inspect", content="检查完成", turn_index=1)]
    wire = adapter.to_api_messages(messages)
    assert wire[-1]["content"] == "当前读取的技能规则"
    assert wire[-2]["tool_call_id"] == "inspect"
    from app.services.context.integrity import context_integrity_validator
    assert context_integrity_validator.validate(wire).ok
