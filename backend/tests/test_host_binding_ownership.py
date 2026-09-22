from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_platform_secret
from app.api.routes import host
from app.schemas.host import HostBindRequest


@pytest.mark.parametrize("platform_id,user,status", [(1, "7", 200), (2, "7", 404), (1, "8", 404)])
def test_binding_lookup_checks_persisted_platform_and_user(monkeypatch, platform_id, user, status):
    monkeypatch.setattr(host.store_service, "get_conversation_by_session", lambda _: {
        "platform_id": platform_id, "external_user_id": user, "conversation_id": "conversation",
    })
    monkeypatch.setattr(host.session_service, "get_or_create", lambda _: SimpleNamespace(host_context={
        "page": {"module": {"id": "home"}}, "auth": {"token": "must-not-leak"},
    }))
    app = FastAPI()
    app.include_router(host.router)
    app.dependency_overrides[require_platform_secret] = lambda: {"platform_id": 1, "platform_key": "aiclusterhub"}
    response = TestClient(app).get('/api/v1/host/sessions/session/binding', params={"external_user_id": "7"})
    assert response.status_code == status
    assert "must-not-leak" not in response.text
    if status == 200:
        assert response.json()["data"]["external_user_id"] == "7"
        assert response.json()["data"]["page_context"] == {"module": {"id": "home"}}


def test_binding_lookup_requires_platform_authentication():
    app = FastAPI()
    app.include_router(host.router)
    assert TestClient(app).get('/api/v1/host/sessions/session/binding', params={"external_user_id": "7"}).status_code in (401, 403)


@pytest.mark.parametrize("platform_id,user,conversation_id", [(2, "7", "conversation"), (1, "8", "conversation"), (1, "7", "wrong-conversation")])
def test_bind_cannot_claim_session_via_id_fallback(monkeypatch, platform_id, user, conversation_id):
    monkeypatch.setattr(host.store_service, "get_conversation_by_session", lambda _: {
        "platform_id": platform_id, "external_user_id": user, "conversation_id": "conversation",
    })
    request = HostBindRequest(platform_key="aiclusterhub", host_name="test", session_id="session",
                              conversation_id=conversation_id, context={"user": {"id": "7"}})
    with pytest.raises(ValueError, match="不匹配"):
        host.host_registry.bind(request, platform={"platform_id": 1, "platform_key": "aiclusterhub"})
