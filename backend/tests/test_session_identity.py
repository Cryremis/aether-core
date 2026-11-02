from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.context.history_store import history_store
from app.services.session_service import session_service
from app.services.store import store_service
from app.services.subagent_service import subagent_service
from app.services.scheduling.service import schedule_service
from app.services.scheduling.executor import ScheduleExecutor
from app.services.skill_authoring_service import identity
from test_host_agent_orchestration import initialize_isolated_runtime, create_platform


def bind(client, platform, **overrides):
    response = client.post("/api/v1/host/bind", headers={"X-Aether-Platform-Secret": platform["host_secret"]},
        json={"platform_key": platform["platform_key"], "host_name": "极光平台", "conversation_key": "welink-user-27",
              "context": {"user": {"id": "27", "name": "test"}}, **overrides})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def legacy_state(session_id, **fields):
    payload = history_store.snapshot(session_id)
    payload.update(fields)
    payload["ledger_revision"] = history_store.capture(session_id, payload, expected_revision=payload["ledger_revision"])
    path = session_service._metadata_path(session_id)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    session_service._sessions.pop(session_id, None)
    return payload


def test_bind_new_and_old_session_extensions_and_snapshot_heal(tmp_path):
    initialize_isolated_runtime(tmp_path)
    platform = create_platform()
    client = TestClient(app)
    data = bind(client, platform)
    sid = data["session_id"]
    headers = {"Authorization": f"Bearer {data['token']}"}
    for cold in [False, True]:
        if cold:
            legacy_state(sid, platform_id=None, external_user_id=None)
            rebound = bind(client, platform, session_id=sid)
            assert rebound["session_id"] == sid
        response = client.get("/api/v1/extensions", params={"session_id": sid}, headers=headers)
        assert response.status_code == 200, response.text
        for payload in [history_store.snapshot(sid), json.loads(session_service._metadata_path(sid).read_text(encoding="utf-8"))]:
            assert payload["platform_id"] == platform["platform_id"]
            assert payload["external_user_id"] == "27"


def test_cached_old_identity_heals_before_read_and_write(tmp_path):
    initialize_isolated_runtime(tmp_path)
    platform = create_platform()
    data = bind(TestClient(app), platform)
    session = session_service.get_or_create(data["session_id"])
    for operation in [lambda: session_service.get_or_create(session.session_id), lambda: session_service.persist(session)]:
        session.platform_id = session.external_user_id = None
        operation()
        assert session.platform_id == platform["platform_id"] and session.external_user_id == "27"
        assert history_store.snapshot(session.session_id)["platform_id"] == platform["platform_id"]


def test_concurrent_cold_load_returns_one_identity_and_keeps_history(tmp_path):
    initialize_isolated_runtime(tmp_path)
    platform = create_platform()
    data = bind(TestClient(app), platform)
    sid = data["session_id"]
    original = legacy_state(sid, platform_id=None, external_user_id=None,
        compaction_audit=[{"id": "audit-1", "summary": "保留原文"}])
    with ThreadPoolExecutor(max_workers=8) as pool:
        sessions = list(pool.map(lambda _: session_service.get_or_create(sid), range(16)))
    assert len({id(s) for s in sessions}) == 1
    payload = history_store.snapshot(sid)
    assert payload["platform_id"] == platform["platform_id"]
    assert payload["compaction_audit"] == original["compaction_audit"]
    revision = payload["ledger_revision"]
    session_service.get_or_create(sid)
    assert history_store.snapshot(sid)["ledger_revision"] == revision


@pytest.mark.parametrize("field,value", [("platform_id", 999), ("external_user_id", "other"), ("owner_user_id", 1)])
def test_conflicting_identity_is_not_overwritten_or_used_for_access(tmp_path, field, value):
    initialize_isolated_runtime(tmp_path)
    platform = create_platform()
    client = TestClient(app)
    data = bind(client, platform)
    sid = data["session_id"]
    legacy_state(sid, **{field: value})
    before = session_service._metadata_path(sid).read_bytes()
    response = client.get("/api/v1/extensions", params={"session_id": sid}, headers={"Authorization": f"Bearer {data['token']}"})
    assert response.status_code == 409
    assert session_service._metadata_path(sid).read_bytes() == before
    assert history_store.snapshot(sid)[field] == value
    assert sid not in session_service._sessions


def test_all_creation_paths_and_inherited_identity(tmp_path):
    initialize_isolated_runtime(tmp_path)
    platform = create_platform()
    client = TestClient(app)
    response = client.post("/api/v1/host/conversations", headers={"X-Aether-Platform-Secret": platform["host_secret"]}, json={"external_user_id": "27"})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    session = session_service.get_or_create(data["session_id"])
    assert identity(session) == {"platform_id": platform["platform_id"], "external_user_id": "27"}
    assert not schedule_service.identity_from_session(session).is_empty
    child = subagent_service._create_child_session(session, name="child", allowed_tools=[])
    assert child.platform_id == session.platform_id and child.external_user_id == "27"
    created = client.post("/api/v1/agent/sessions/bootstrap", headers={"Authorization": f"Bearer {data['token']}"})
    assert created.status_code == 200, created.text
    embedded = session_service.get_or_create(created.json()["data"]["session_id"])
    assert embedded.platform_id == session.platform_id and embedded.external_user_id == "27"
    scheduled = ScheduleExecutor()._prepare_session({"target_mode": "new_session_per_run", "title": "test",
        "platform_id": platform["platform_id"], "external_user_id": "27"})
    assert scheduled.platform_id == session.platform_id and scheduled.external_user_id == "27"
    assert store_service.get_conversation_by_session(scheduled.session_id)["workspace_id"] == scheduled.workspace_id


def test_legacy_json_without_ledger_is_healed_and_archived(tmp_path):
    initialize_isolated_runtime(tmp_path)
    platform = create_platform()
    data = bind(TestClient(app), platform)
    sid = data["session_id"]
    original = legacy_state(sid, platform_id=None, external_user_id=None)
    original["message_schema_version"] = 2
    original.pop("ledger_revision", None)
    path = session_service._metadata_path(sid)
    path.write_text(json.dumps(original), encoding="utf-8")
    with store_service._connect() as conn:
        conn.execute("DELETE FROM context_snapshots WHERE session_id=?", (sid,))
    session = session_service.get_or_create(sid)
    assert session.platform_id == platform["platform_id"] and session.external_user_id == "27"
    assert history_store.snapshot(sid)["platform_id"] == platform["platform_id"]
    assert list(path.parent.glob("session.pre-context-v3.*.json"))


def test_engine_auto_creation_preserves_explicit_session_identity(tmp_path, monkeypatch):
    import asyncio
    from test_agent_engine import collect_stream
    initialize_isolated_runtime(tmp_path)
    platform = create_platform()
    session = session_service.get_or_create()
    session.platform_id, session.external_user_id = platform["platform_id"], "27"
    async def stream(*args, **kwargs):
        yield {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}
    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", stream)
    events = asyncio.run(collect_stream(session, "test"))
    row = store_service.get_conversation_by_session(session.session_id)
    assert row["platform_id"] == platform["platform_id"] and row["external_user_id"] == "27"
    assert events[-1]["payload"]["subtype"] == "success"


def test_platform_admin_skill_discovery_uses_healed_platform_without_escalation(tmp_path):
    import asyncio
    from app.services.tool_service import tool_service
    initialize_isolated_runtime(tmp_path)
    platform = create_platform()
    user = store_service.create_or_update_oauth_user(provider="test", provider_user_id="admin-member", full_name="member", email=None)
    store_service.add_platform_admin(platform_id=platform["platform_id"], user_id=user.user_id)
    session = session_service.get_or_create()
    store_service.create_conversation(session_id=session.session_id, title="test", host_name="test",
        platform_id=platform["platform_id"], owner_user_id=user.user_id)
    session_service.persist(session)
    legacy_state(session.session_id, platform_id=None)
    session = session_service.get_or_create(session.session_id)
    discover = tool_service._registry.get_handler("discover_tools")
    result = asyncio.run(discover(session, {"bundle_id": "skill_management"}))
    assert result.data["loaded"]
    store_service.remove_platform_admin(platform_id=platform["platform_id"], user_id=user.user_id)
    session.loaded_skill_tools = []
    result = asyncio.run(discover(session, {"bundle_id": "skill_management"}))
    assert result.data["loaded"] == []


def test_conflicting_json_projection_is_not_overwritten_by_valid_ledger(tmp_path):
    from app.services.session_identity import SessionIdentityConflict
    initialize_isolated_runtime(tmp_path)
    platform = create_platform()
    sid = bind(TestClient(app), platform)["session_id"]
    path = session_service._metadata_path(sid)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["platform_id"] = 999
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()
    session_service._sessions.pop(sid)
    with pytest.raises(SessionIdentityConflict):
        session_service.get_or_create(sid)
    assert path.read_bytes() == before
    assert history_store.snapshot(sid)["platform_id"] == platform["platform_id"]
