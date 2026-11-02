from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.context.message_adapter import context_message_adapter as adapter
from app.services.session_service import session_service
from app.services.store import store_service
from app.services.token_service import token_service
from test_auth_and_sessions import initialize_isolated_runtime


@pytest.fixture
def audit(tmp_path):
    initialize_isolated_runtime(tmp_path)
    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    token, _ = token_service.create_user_token(admin.user_id, admin.role)
    return TestClient(app), {"Authorization": f"Bearer {token}"}, admin


def seed_conversations(count, owner_id, platform_id=None):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with store_service._connect() as conn:
        conn.executemany(
            """INSERT INTO conversations(conversation_id, session_id, platform_id, owner_user_id,
                title, host_name, created_at, updated_at, last_message_at, message_count, metadata_json)
                VALUES (?, ?, ?, ?, ?, 'test', ?, ?, ?, ?, ?)""",
            [(f"conv-{i:04}", f"sess-{i:04}", platform_id, owner_id, f"任务 {i}",
              (start + timedelta(days=i)).isoformat(), (start + timedelta(days=i)).isoformat(),
              (start + timedelta(days=count - i)).isoformat(), i,
              '{"external_user_name":"测试用户_100%"}') for i in range(count)],
        )


def test_paged_list_queries_only_metadata_and_has_stable_global_order(audit, monkeypatch):
    client, headers, admin = audit
    seed_conversations(650, admin.user_id)

    def unexpected(*args, **kwargs):
        pytest.fail("列表不能加载会话或逐条查询关联信息")

    monkeypatch.setattr(session_service, "get_or_create", unexpected)
    monkeypatch.setattr(store_service, "get_user_by_id", unexpected)
    monkeypatch.setattr(store_service, "get_platform_by_id", unexpected)
    # auth 通过 get_user_by_id 校验令牌，单独覆盖依赖以专测查询成本。
    from app.api.deps import AuthContext, require_admin
    app.dependency_overrides[require_admin] = lambda: AuthContext(kind="user", user=admin, role=admin.role)
    try:
        first = client.get("/api/v1/admin/conversations?limit=50", headers=headers)
        assert first.status_code == 200
        page = first.json()["data"]
        assert page["total"] == 650
        assert len(page["items"]) == 50
        assert page["items"][0]["session_id"] == "sess-0000"
        second = client.get("/api/v1/admin/conversations?limit=50&offset=50", headers=headers).json()["data"]
        assert not {r["session_id"] for r in page["items"]} & {r["session_id"] for r in second["items"]}
        created = client.get("/api/v1/admin/conversations?limit=2&sort_by=created_at", headers=headers).json()["data"]
        assert created["items"][0]["session_id"] == "sess-0649"
        assert created["items"][0]["owner_user_name"] == admin.full_name
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_search_dates_and_legacy_list_shape(audit):
    client, headers, admin = audit
    seed_conversations(12, admin.user_id)
    endpoint = "/api/v1/admin/conversations"
    for term in ("任务 1", "sess-0001", "测试用户_100%", admin.full_name):
        response = client.get(endpoint, params={"limit": 10, "q": term}, headers=headers)
        assert response.status_code == 200
        assert response.json()["data"]["total"] > 0
    # '%' 必须按字面匹配，不能当作匹配全部行的通配符。
    assert client.get(endpoint, params={"limit": 10, "q": "不存在%"}, headers=headers).json()["data"]["total"] == 0
    params = {"limit": 10, "time_field": "created_at", "time_from": "2026-01-02T08:00:00+08:00", "time_to": "2026-01-04T08:00:00+08:00", "sort_by": "message_count", "sort_order": "asc"}
    page = client.get(endpoint, params=params, headers=headers).json()["data"]
    assert [r["message_count"] for r in page["items"]] == [1, 2]
    assert isinstance(client.get(endpoint, headers=headers).json()["data"], list)
    assert client.get(endpoint, params={"limit": 10, "offset": 999}, headers=headers).json()["data"]["items"] == []


@pytest.mark.parametrize("params", [{"limit": 101}, {"offset": -1}, {"sort_by": "title;DROP TABLE users"}, {"time_field": "message_count"}, {"time_from": "bad"}, {"time_from": "2026-02-02", "time_to": "2026-01-01"}])
def test_invalid_audit_queries_are_rejected(audit, params):
    client, headers, _ = audit
    assert client.get("/api/v1/admin/conversations", params=params, headers=headers).status_code == 422


def test_pagination_retains_platform_authorization(audit):
    client, _, admin = audit
    user = store_service.create_or_update_oauth_user(provider="test", provider_user_id="auditor", full_name="平台管理员", email=None)
    platform = store_service.create_platform(platform_key="audit", display_name="Audit", host_type="embedded", description="", owner_user_id=admin.user_id)
    store_service.add_platform_admin(platform_id=platform["platform_id"], user_id=user.user_id, assigned_by=admin.user_id)
    seed_conversations(3, admin.user_id, platform["platform_id"])
    token, _ = token_service.create_user_token(user.user_id, user.role)
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/v1/admin/conversations?limit=1", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 3
    assert client.get("/api/v1/admin/conversations?limit=1&platform_id=99999", headers=headers).status_code == 403
    user2 = store_service.create_or_update_oauth_user(provider="test", provider_user_id="other", full_name="其他平台", email=None)
    platform2 = store_service.create_platform(platform_key="other", display_name="Other", host_type="embedded", description="", owner_user_id=user2.user_id)
    row = store_service.create_conversation(session_id="other-session", title="其他会话", host_name="test", platform_id=platform2["platform_id"], owner_user_id=user2.user_id)
    assert client.get(f"/api/v1/admin/conversations/{row['session_id']}?limit=30", headers=headers).status_code == 403
    assert client.get("/api/v1/admin/conversations?limit=1", headers=headers).json()["data"]["total"] == 3


def test_lightweight_detail_does_not_activate_or_scan_workspace(audit, monkeypatch):
    client, headers, admin = audit
    session = session_service.get_or_create()
    row = store_service.create_conversation(session_id=session.session_id, title="大对话", host_name="test", owner_user_id=admin.user_id, platform_id=session.platform_id)
    session.conversation_id = row["conversation_id"]
    session.owner_user_id = admin.user_id
    session.messages = [adapter.make_user_message(f"消息 {i}", turn_index=i + 1) for i in range(205)]
    hidden = adapter.make_user_message("隐藏的技能内容", turn_index=206)
    hidden["visible_in_transcript"] = False
    session.messages.append(hidden)
    session_service.persist(session)
    metadata = session_service._metadata_path(session.session_id)
    original = metadata.read_bytes()
    session_service._sessions.clear()

    def unexpected(*args, **kwargs):
        pytest.fail("轻量详情不能激活会话、扫描文件或写回状态")

    monkeypatch.setattr(session_service, "get_or_create", unexpected)
    monkeypatch.setattr(session_service, "_write_metadata", unexpected)
    monkeypatch.setattr("app.services.file_service.file_service.list_sidebar_files", unexpected)
    monkeypatch.setattr("app.services.skill_service.skill_service.list_for_session", unexpected)
    endpoint = f"/api/v1/admin/conversations/{session.session_id}"
    latest = client.get(endpoint, params={"limit": 30}, headers=headers)
    assert latest.status_code == 200
    data = latest.json()["data"]
    assert data["message_count"] == 205
    assert data["messages"] == []
    assert data["transcript_page"] == {"total": 205, "start": 175, "end": 205, "has_more": True}
    assert data["transcript"][0]["content"] == "消息 175"
    assert "隐藏的技能内容" not in latest.text
    older = client.get(endpoint, params={"limit": 30, "before": 175}, headers=headers).json()["data"]
    assert older["transcript_page"]["end"] == 175
    assert older["transcript"][-1]["content"] == "消息 174"
    assert metadata.read_bytes() == original
    assert session.session_id not in session_service._sessions


def test_audit_read_keeps_live_session_and_missing_history_unchanged(audit):
    client, headers, admin = audit
    session = session_service.get_or_create()
    row = store_service.create_conversation(session_id=session.session_id, title="活跃会话", host_name="test", owner_user_id=admin.user_id, platform_id=session.platform_id)
    session.conversation_id = row["conversation_id"]
    session.owner_user_id = admin.user_id
    session.messages = [adapter.make_user_message("正在查看", turn_index=1)]
    session_service.persist(session)
    session.last_access = 123
    response = client.get(f"/api/v1/admin/conversations/{session.session_id}?limit=30", headers=headers)
    assert response.status_code == 200
    assert session.last_access == 123
    assert session_service._sessions[session.session_id] is session
    assert response.json()["data"]["transcript"][0]["content"] == "正在查看"
    store_service.create_conversation(session_id="missing-history", title="仅剩元数据", host_name="test", owner_user_id=admin.user_id)
    response = client.get("/api/v1/admin/conversations/missing-history?limit=30", headers=headers)
    assert response.status_code == 404
    assert "missing-history" not in session_service._sessions
