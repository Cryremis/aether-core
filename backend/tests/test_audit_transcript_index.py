import json

import pytest

from app.core.config import settings
from app.services.audit_transcript_service import AuditTranscriptService
from app.services.context.history_store import history_store
from app.services.context.message_adapter import context_message_adapter as adapter
from app.services.session_service import session_service
from app.services.store import store_service
from app.services.transcript_service import transcript_service
from test_admin_audit_pagination import audit


def conversation(admin, count=305):
    session = session_service.get_or_create()
    row = store_service.create_conversation(session_id=session.session_id, title="索引测试", host_name="test",
        owner_user_id=admin.user_id, platform_id=session.platform_id)
    session.conversation_id, session.owner_user_id = row["conversation_id"], admin.user_id
    session.messages = [adapter.make_user_message(f"消息 {i}", turn_index=i + 1) for i in range(count)]
    session_service.persist(session)
    return session, store_service.get_conversation_by_session(session.session_id)


def test_reopen_refresh_and_paging_decode_only_requested_rows_without_history(audit, monkeypatch):
    client, headers, admin = audit
    session, row = conversation(admin)
    endpoint = f"/api/v1/admin/conversations/{session.session_id}"
    assert client.get(endpoint, params={"limit": 30}, headers=headers).status_code == 200
    session_service._sessions.clear()

    def unexpected(*args, **kwargs):
        pytest.fail("索引命中后不允许读取快照或展开历史")

    monkeypatch.setattr(history_store, "expand_history", unexpected)
    monkeypatch.setattr(history_store, "snapshot", unexpected)
    monkeypatch.setattr(session_service, "read_for_audit", unexpected)
    monkeypatch.setattr(session_service, "_metadata_lock", unexpected)
    monkeypatch.setattr(transcript_service, "build_audit_transcript", unexpected)
    original_decode = history_store._decode
    decoded = []

    def decode(payload):
        decoded.append(payload)
        return original_decode(payload)

    monkeypatch.setattr(history_store, "_decode", decode)
    for before in (None, 275, 125, None):
        decoded.clear()
        # 每次实例化读取器模拟进程重启，不能依赖 Python 对象缓存。
        page = AuditTranscriptService().read_page(row, limit=30, before=before)
        assert len(page["transcript"]) == 30
        assert len(decoded) == 31  # 小元数据一份、当前页消息 30 份。
    response = client.get(endpoint, params={"limit": 30, "before": 275}, headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["transcript"][0]["content"] == "消息 245"


def test_access_and_config_changes_reuse_index_but_messages_and_branch_edits_rebuild(audit, monkeypatch):
    _, _, admin = audit
    session, row = conversation(admin, count=45)
    reader = AuditTranscriptService()
    original_build = transcript_service.build_audit_transcript
    builds = []

    def build(view):
        builds.append(view.session_id)
        return original_build(view)

    monkeypatch.setattr(transcript_service, "build_audit_transcript", build)
    assert reader.read_page(row, limit=30)["transcript_page"]["total"] == 45
    first_revision = session.ledger_revision
    session.allow_network = False
    session.host_name = "新宿主"
    session_service.persist(session)
    assert session.ledger_revision > first_revision
    page = reader.read_page(row, limit=30)
    assert page["metadata"]["host_name"] == "新宿主"
    assert page["metadata"]["allow_network"] is False
    assert len(builds) == 1
    session.messages.append(adapter.make_user_message("新消息", turn_index=46))
    session_service.persist(session)
    assert reader.read_page(row, limit=30)["transcript"][-1]["content"] == "新消息"
    assert len(builds) == 2
    session.messages = session.messages[:2] + [adapter.make_user_message("编辑后的新分支", turn_index=3)]
    session_service.persist(session)
    page = reader.read_page(row, limit=30)
    assert [m["content"] for m in page["transcript"]] == ["消息 0", "消息 1", "编辑后的新分支"]
    assert len(builds) == 3


def test_existing_database_backfills_index_once_without_rewriting_snapshot(audit, monkeypatch):
    _, _, admin = audit
    session, row = conversation(admin, count=80)
    original = history_store.snapshot(session.session_id)
    with store_service._connect() as conn:
        for table in ("audit_transcript_items", "audit_transcript_heads", "audit_transcript_sources", "audit_attempt_versions"):
            conn.execute(f"DROP TABLE {table}")
    reader = AuditTranscriptService()
    assert reader.read_page(row, limit=30)["transcript_page"]["total"] == 80
    assert history_store.snapshot(session.session_id) == original
    monkeypatch.setattr(history_store, "expand_history", lambda *args: pytest.fail("旧会话索引应持久复用"))
    assert reader.read_page(row, limit=30, before=50)["transcript_page"]["end"] == 50


def test_legacy_json_index_detects_file_change_without_creating_snapshot(audit):
    _, _, admin = audit
    session, row = conversation(admin, count=2)
    with store_service._connect() as conn:
        conn.execute("DELETE FROM context_snapshots WHERE session_id=?", (session.session_id,))
    reader = AuditTranscriptService()
    assert reader.read_page(row, limit=30)["transcript_page"]["total"] == 2
    path = session_service._metadata_path(session.session_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["messages"][1]["content"] = "旧 JSON 已更新"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert reader.read_page(row, limit=30)["transcript"][-1]["content"] == "旧 JSON 已更新"
    assert history_store.snapshot(session.session_id) is None


def test_encrypted_history_never_writes_plaintext_audit_messages(audit, monkeypatch):
    _, _, admin = audit
    monkeypatch.setattr(settings, "capability_secret_encryption_key", "audit-index-encryption-test")
    session, row = conversation(admin, count=2)
    assert AuditTranscriptService().read_page(row, limit=30)["transcript_page"]["total"] == 2
    with store_service._connect() as conn:
        rows = conn.execute("SELECT payload FROM audit_transcript_items WHERE session_id=?", (session.session_id,)).fetchall()
    assert all(item[0].startswith("encrypted:") and "消息" not in item[0] for item in rows)


def test_recoverable_attempt_versions_and_run_status_invalidate_index(audit, monkeypatch):
    _, _, admin = audit
    session, row = conversation(admin, count=1)
    reader = AuditTranscriptService()
    reader.read_page(row, limit=30)
    store_service.create_agent_run(run_id="audit-running", session_id=session.session_id, conversation_id=row["conversation_id"])
    history_store.start_attempt(session.session_id, "audit-attempt", {"run_id": "audit-running", "turn_index": 2, "api_format": "openai-compatible", "message_id": "recovered-answer"})
    history_store.save_attempt(session.session_id, "audit-attempt", {"content": "部分回答"})
    original_build = transcript_service.build_audit_transcript
    monkeypatch.setattr(transcript_service, "build_audit_transcript", lambda *args: pytest.fail("活跃 token 不应使审计索引失效"))
    assert reader.read_page(row, limit=30)["transcript_page"]["total"] == 1
    history_store.save_attempt(session.session_id, "audit-attempt", {"content": "更新中的部分回答"})
    assert reader.read_page(row, limit=30)["transcript_page"]["total"] == 1
    monkeypatch.setattr(transcript_service, "build_audit_transcript", original_build)
    store_service.fail_agent_run("audit-running", message="运行中断")
    recovered = reader.read_page(row, limit=30)
    assert recovered["transcript_page"]["total"] == 2
    assert recovered["transcript"][-1]["blocks"][0]["content"] == "更新中的部分回答"
    history_store.save_attempt(session.session_id, "audit-attempt", {"content": "恢复后的回答"}, "completed")
    recovered = reader.read_page(row, limit=30)
    assert recovered["transcript"][-1]["blocks"][0]["content"] == "恢复后的回答"


def test_history_deletion_removes_derived_index_and_attempt_versions(audit):
    _, _, admin = audit
    session, row = conversation(admin, count=2)
    AuditTranscriptService().read_page(row, limit=30)
    history_store.delete(session.session_id)
    with store_service._connect() as conn:
        for table in ("audit_transcript_items", "audit_transcript_heads", "audit_transcript_sources", "audit_attempt_versions"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id=?", (session.session_id,)).fetchone()[0] == 0


def test_stale_projection_build_cannot_overwrite_newer_history(audit, monkeypatch):
    _, _, admin = audit
    session, row = conversation(admin, count=1)
    original_build = transcript_service.build_audit_transcript
    builds = []

    def build(view):
        result = original_build(view)
        if not builds:
            session.messages.append(adapter.make_user_message("并发新增的消息", turn_index=2))
            session_service.persist(session)
        builds.append(view.session_id)
        return result

    monkeypatch.setattr(transcript_service, "build_audit_transcript", build)
    page = AuditTranscriptService().read_page(row, limit=30)
    assert page["transcript_page"]["total"] == 2
    assert page["transcript"][-1]["content"] == "并发新增的消息"
    assert len(builds) == 2


def test_reused_checkpoint_detects_new_versions_of_archived_messages(audit):
    _, _, admin = audit
    session, row = conversation(admin, count=2)
    originals = list(session.messages)
    checkpoint = adapter.make_user_message("检查点", turn_index=3)
    checkpoint["compression_meta"] = {"source_message_ids": [message["message_id"] for message in originals]}
    session.messages = [checkpoint]
    session_service.persist(session)
    reader = AuditTranscriptService()
    assert reader.read_page(row, limit=30)["transcript"][0]["content"] == "消息 0"
    # 中间状态没有被审计读取；恢复相同检查点时也不能复用原来的旧展示索引。
    session.messages = [{**originals[0], "content": "归档消息的新版本"}, originals[1]]
    session_service.persist(session)
    session.messages = [checkpoint]
    session_service.persist(session)
    assert reader.read_page(row, limit=30)["transcript"][0]["content"] == "归档消息的新版本"
