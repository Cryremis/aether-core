from copy import deepcopy
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_platform_secret
from app.api.routes import host


@pytest.mark.parametrize("identifiers", [
    {"session_id": "session"},
    {"conversation_id": "conversation"},
    {"session_id": "session", "conversation_id": "conversation"},
])
@pytest.mark.parametrize("invalid", ["missing", "deleted", "platform", "user", "conversation"])
def test_explicit_bind_rejects_invalid_target_without_side_effects(monkeypatch, identifiers, invalid):
    row = {"session_id": "session", "platform_id": 1, "external_user_id": "7",
           "conversation_id": "conversation", "deleted_at": None}
    if invalid == "missing":
        row = None
    elif invalid == "deleted":
        row["deleted_at"] = "2026-09-22T00:00:00Z"
    elif invalid == "platform":
        row["platform_id"] = 2
    elif invalid == "user":
        row["external_user_id"] = "8"
    elif invalid == "conversation":
        if "conversation_id" not in identifiers:
            identifiers = {**identifiers, "conversation_id": "another"}
        else:
            row["conversation_id"] = "another"
    snapshot = deepcopy(row)
    monkeypatch.setattr(host.store_service, "get_conversation_by_session", lambda _: row)
    monkeypatch.setattr(host.store_service, "get_conversation", lambda _: row)
    side_effects = []
    for service, method in ((host.session_service, "get_or_create"), (host.session_service, "attach_host"),
                            (host.session_service, "persist"), (host.store_service, "create_conversation"),
                            (host.store_service, "backfill_conversation_metadata")):
        spy = Mock(side_effect=AssertionError(f"unexpected side effect: {method}"))
        monkeypatch.setattr(service, method, spy)
        side_effects.append(spy)
    app = FastAPI()
    app.include_router(host.router)
    app.dependency_overrides[require_platform_secret] = lambda: {"platform_id": 1, "platform_key": "test"}
    response = TestClient(app).post('/api/v1/host/bind', json={
        "platform_key": "test", "host_name": "host", **identifiers,
        "conversation_key": "stale-key", "context": {"user": {"id": "7"}, "auth": {"token": "replacement"}},
    })
    assert response.status_code == 400, response.text
    assert "不存在、已删除或" in response.json()["detail"]
    assert row == snapshot
    for spy in side_effects:
        spy.assert_not_called()


def test_explicit_session_wins_over_a_key_for_another_conversation(tmp_path):
    from test_auth_and_sessions import initialize_isolated_runtime
    from app.core.config import settings
    from app.main import app
    initialize_isolated_runtime(tmp_path)
    admin = host.store_service.get_user_by_username(settings.auth_system_admin_username)
    platform = host.store_service.create_platform(platform_key="bind-ownership", display_name="Bind", host_type="embedded",
                                                 description="test", owner_user_id=admin.user_id)
    client = TestClient(app)
    headers = {"X-Aether-Platform-Secret": platform["host_secret"]}
    body = {"platform_key": "bind-ownership", "host_name": "host", "context": {"user": {"id": "7"}}}
    first_response = client.post('/api/v1/host/bind', headers=headers, json={**body, "conversation_key": "first"})
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()["data"]
    second = client.post('/api/v1/host/bind', headers=headers, json={**body, "conversation_key": "second"}).json()["data"]
    assert first["session_id"] != second["session_id"]
    rebound = client.post('/api/v1/host/bind', headers=headers, json={**body,
        "session_id": first["session_id"], "conversation_key": "second"})
    assert rebound.status_code == 200, rebound.text
    assert rebound.json()["data"]["conversation_id"] == first["conversation_id"]
    by_conversation = client.post('/api/v1/host/bind', headers=headers, json={**body,
        "conversation_id": first["conversation_id"], "conversation_key": "second"})
    assert by_conversation.status_code == 200, by_conversation.text
    assert by_conversation.json()["data"]["session_id"] == first["session_id"]


def test_bind_requires_platform_authentication():
    app = FastAPI()
    app.include_router(host.router)
    response = TestClient(app).post('/api/v1/host/bind', json={"platform_key": "test", "host_name": "host"})
    assert response.status_code == 401
