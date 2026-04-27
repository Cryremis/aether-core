# backend/tests/test_auth_and_sessions.py
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.auth_service import auth_service
from app.services.conversation_service import conversation_service
from app.services.session_service import session_service
from app.services.session_types import AgentSession
from app.services.store import store_service
from app.services.tool_service import tool_service


def initialize_isolated_runtime(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    store_service._db_path = storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    session_service._sessions.clear()
    settings.storage_root = storage_root
    store_service.initialize()


def test_password_login_uses_seeded_admin_account(tmp_path, monkeypatch):
    initialize_isolated_runtime(tmp_path)

    result = auth_service.login_with_password(
        settings.auth_system_admin_username,
        settings.auth_system_admin_password,
    )

    assert result.user.username == settings.auth_system_admin_username
    assert result.user.role == "system_admin"
    assert result.token
    assert result.expires_in > 0


def test_current_user_endpoint_allows_regular_internal_users(tmp_path):
    initialize_isolated_runtime(tmp_path)

    store_service.create_or_update_oauth_user(
        provider="corp-sso",
        provider_user_id="user-001",
        full_name="Regular User",
        email="regular@example.com",
    )
    user = store_service.get_user_by_provider("corp-sso", "user-001")
    assert user is not None

    from app.services.token_service import token_service

    user_token, _ = token_service.create_user_token(user.user_id, user.role)
    client = TestClient(app)
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {user_token}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["full_name"] == "Regular User"
    assert payload["role"] == "user"
    assert payload["can_manage_platforms"] is False


def test_conversations_are_isolated_for_internal_users_and_host_users(tmp_path):
    initialize_isolated_runtime(tmp_path)

    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    regular_user = store_service.create_or_update_oauth_user(
        provider="corp-sso",
        provider_user_id="regular-user-001",
        full_name="Regular User",
        email="regular@example.com",
    )
    assert admin is not None

    admin_session = conversation_service.bootstrap_admin_workbench(admin)
    regular_session = conversation_service.bootstrap_admin_workbench(regular_user)

    admin_items = conversation_service.list_for_admin(admin)
    regular_items = conversation_service.list_for_admin(regular_user)

    assert [item.session_id for item in admin_items] == [admin_session.session_id]
    assert [item.session_id for item in regular_items] == [regular_session.session_id]

    platform = store_service.create_platform(
        platform_key="dash-test",
        display_name="Dash Test",
        host_type="embedded",
        description="test platform",
        owner_user_id=admin.user_id,
    )

    first_session, _ = conversation_service.bootstrap_host_workbench(
        platform_key=platform["platform_key"],
        external_user_id="user-a",
        external_user_name="User A",
        external_org_id="org-a",
        conversation_id=None,
        conversation_key="conversation-a",
        host_name="Dash",
    )
    second_session, _ = conversation_service.bootstrap_host_workbench(
        platform_key=platform["platform_key"],
        external_user_id="user-b",
        external_user_name="User B",
        external_org_id="org-a",
        conversation_id=None,
        conversation_key="conversation-b",
        host_name="Dash",
    )

    first_user_items = conversation_service.list_for_host_user(
        platform_id=platform["platform_id"],
        external_user_id="user-a",
    )
    second_user_items = conversation_service.list_for_host_user(
        platform_id=platform["platform_id"],
        external_user_id="user-b",
    )

    assert [item.session_id for item in first_user_items] == [first_session.session_id]
    assert [item.session_id for item in second_user_items] == [second_session.session_id]


def test_embed_bootstrap_rejects_platform_key_mismatch(tmp_path):
    initialize_isolated_runtime(tmp_path)

    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None

    platform = store_service.create_platform(
        platform_key="dash-embed",
        display_name="Dash Embed",
        host_type="embedded",
        description="embed test platform",
        owner_user_id=admin.user_id,
    )

    client = TestClient(app)
    response = client.post(
        "/api/v1/platforms/embed/bootstrap",
        headers={"X-Aether-Platform-Secret": platform["host_secret"]},
        json={
            "platform_key": "standalone",
            "external_user_id": "embed-user",
            "external_user_name": "Embed User",
            "external_org_id": "org-embed",
            "conversation_id": None,
            "conversation_key": "embed-conversation",
            "host_name": "Dash",
        },
    )

    assert response.status_code == 403


def test_platform_integration_guide_returns_expected_snippets(tmp_path):
    initialize_isolated_runtime(tmp_path)

    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None

    platform = store_service.create_platform(
        platform_key="guide-demo",
        display_name="Guide Demo",
        host_type="embedded",
        description="integration guide test platform",
        owner_user_id=admin.user_id,
    )

    login = auth_service.login_with_password(
        settings.auth_system_admin_username,
        settings.auth_system_admin_password,
    )

    client = TestClient(app)
    response = client.get(
        f"/api/v1/platforms/{platform['platform_id']}/integration-guide",
        headers={"Authorization": f"Bearer {login.token}"},
    )

    assert response.status_code == 200
    payload = response.json()["data"]

    assert payload["platform_key"] == "guide-demo"
    assert payload["display_name"] == "Guide Demo"
    assert payload["bind_api_path"] == "/api/v1/aethercore/embed/bind"
    assert payload["frontend_script_path"] == "/static/aethercore-embed.js"
    assert 'platformKey: "guide-demo"' in payload["snippets"]["frontend"]
    assert "AETHERCORE_PLATFORM_KEY=guide-demo" in payload["snippets"]["backend_env"]
    assert f"AETHERCORE_PLATFORM_SECRET={platform['host_secret']}" in payload["snippets"]["backend_env"]
    assert '@router.post("/api/v1/aethercore/embed/bind")' in payload["snippets"]["backend_fastapi"]
    assert "settings.AETHERCORE_PLATFORM_SECRET" in payload["snippets"]["backend_fastapi"]


def test_platform_registration_approval_creates_platform_and_assigns_applicant(tmp_path):
    initialize_isolated_runtime(tmp_path)

    admin_login = auth_service.login_with_password(
        settings.auth_system_admin_username,
        settings.auth_system_admin_password,
    )
    applicant = store_service.create_or_update_oauth_user(
        provider="corp-sso",
        provider_user_id="applicant-001",
        full_name="Applicant User",
        email="applicant@example.com",
    )
    from app.services.token_service import token_service

    applicant_token, _ = token_service.create_user_token(applicant.user_id, applicant.role)

    client = TestClient(app)
    create_response = client.post(
        "/api/v1/platforms/registration-requests",
        headers={"Authorization": f"Bearer {applicant_token}"},
        json={
            "platform_key": "growth-hub",
            "display_name": "Growth Hub",
            "description": "Growth team workspace",
            "justification": "Need dedicated LLM and baseline assets",
        },
    )

    assert create_response.status_code == 200
    request_id = create_response.json()["data"]["request_id"]

    approve_response = client.post(
        f"/api/v1/platforms/registration-requests/{request_id}/approve",
        headers={"Authorization": f"Bearer {admin_login.token}"},
        json={"review_comment": "Approved"},
    )

    assert approve_response.status_code == 200
    payload = approve_response.json()["data"]
    assert payload["status"] == "approved"
    assert payload["approved_platform_id"] is not None

    platform = store_service.get_platform_by_key("growth-hub")
    assert platform is not None
    assert platform["owner_user_id"] == applicant.user_id
    assert store_service.is_platform_admin(platform_id=platform["platform_id"], user_id=applicant.user_id)


def test_oauth_login_creates_regular_user_without_manual_preauthorization(tmp_path, monkeypatch):
    initialize_isolated_runtime(tmp_path)

    monkeypatch.setattr(settings, "auth_oauth_providers", "corp-sso")
    monkeypatch.setenv("AUTH_OAUTH_CORP_SSO_DISPLAY_NAME", "Company SSO")
    monkeypatch.setenv("AUTH_OAUTH_CORP_SSO_CLIENT_ID", "client-id")
    monkeypatch.setenv("AUTH_OAUTH_CORP_SSO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("AUTH_OAUTH_CORP_SSO_AUTHORIZE_URL", "https://sso.example.com/oauth2/authorize")
    monkeypatch.setenv("AUTH_OAUTH_CORP_SSO_TOKEN_URL", "https://sso.example.com/oauth2/token")
    monkeypatch.setenv("AUTH_OAUTH_CORP_SSO_USERINFO_URL", "https://sso.example.com/oauth2/userinfo")
    monkeypatch.setenv("AUTH_OAUTH_CORP_SSO_USER_ID_FIELDS", "uuid")
    monkeypatch.setenv("AUTH_OAUTH_CORP_SSO_USER_NAME_FIELDS", "displayNameCn")
    monkeypatch.setenv("AUTH_OAUTH_CORP_SSO_USER_EMAIL_FIELDS", "email")
    from app.services.oauth_service import oauth_service

    oauth_service.reload()

    async def fake_exchange_code_for_token(provider_key: str, code: str, redirect_uri: str):
        return {"access_token": "fake-token"}

    async def fake_get_user_info(provider_key: str, access_token: str):
        return {
            "uuid": "uuid-001",
            "uid": "A1234567",
            "employeeNumber": "1234567",
            "displayNameCn": "Zhang San",
            "email": "zhangsan@example.com",
        }

    monkeypatch.setattr("app.services.auth_service.oauth_service.exchange_code_for_token", fake_exchange_code_for_token)
    monkeypatch.setattr("app.services.auth_service.oauth_service.get_user_info", fake_get_user_info)

    result = asyncio.run(auth_service.login_with_oauth("corp-sso", "demo-code", "http://localhost/callback"))

    assert result.user.provider == "corp-sso"
    assert result.user.full_name == "Zhang San"
    assert result.user.role == "user"


def test_oauth_provider_list_logs_incomplete_provider_configuration(tmp_path, monkeypatch, caplog):
    initialize_isolated_runtime(tmp_path)

    monkeypatch.setattr(settings, "auth_oauth_providers", "corp-sso")
    monkeypatch.setenv("AUTH_OAUTH_CORP_SSO_CLIENT_ID", "client-id")
    from app.services.oauth_service import oauth_service

    oauth_service.reload()

    with caplog.at_level("WARNING"):
        providers = oauth_service.list_enabled_providers()

    assert providers == []
    assert "OAuth provider 'corp-sso' is declared but incomplete" in caplog.text
    assert "AUTH_OAUTH_CORP_SSO_CLIENT_SECRET" in caplog.text
    assert "AUTH_OAUTH_CORP_SSO_AUTHORIZE_URL" in caplog.text
    assert "AUTH_OAUTH_CORP_SSO_TOKEN_URL" in caplog.text
    assert "AUTH_OAUTH_CORP_SSO_USERINFO_URL" in caplog.text


def test_oauth_provider_list_reads_provider_details_from_env_file(monkeypatch):
    monkeypatch.setattr(settings, "auth_oauth_providers", "corp-sso")
    from app.services.oauth_service import oauth_service
    from app.services import oauth_service as oauth_module

    oauth_service.reload()
    monkeypatch.setattr(
        oauth_module,
        "_load_backend_env_file",
        lambda: {
            "AUTH_OAUTH_CORP_SSO_DISPLAY_NAME": "Company SSO",
            "AUTH_OAUTH_CORP_SSO_CLIENT_ID": "client-id",
            "AUTH_OAUTH_CORP_SSO_CLIENT_SECRET": "client-secret",
            "AUTH_OAUTH_CORP_SSO_AUTHORIZE_URL": "https://sso.example.com/oauth2/authorize",
            "AUTH_OAUTH_CORP_SSO_TOKEN_URL": "https://sso.example.com/oauth2/token",
            "AUTH_OAUTH_CORP_SSO_USERINFO_URL": "https://sso.example.com/oauth2/userinfo",
            "AUTH_OAUTH_CORP_SSO_SCOPE": "base.profile",
        },
    )

    providers = oauth_service.list_enabled_providers()

    assert len(providers) == 1
    assert providers[0]["provider_key"] == "corp-sso"
    assert providers[0]["display_name"] == "Company SSO"
    assert "client_id=client-id" in providers[0]["authorize_url_template"]


def test_host_bind_requires_platform_secret_and_matching_platform_key(tmp_path):
    initialize_isolated_runtime(tmp_path)

    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None

    platform = store_service.create_platform(
        platform_key="poc-host",
        display_name="POC Host",
        host_type="embedded",
        description="host bind test platform",
        owner_user_id=admin.user_id,
    )

    payload = {
        "platform_key": platform["platform_key"],
        "host_name": "POC",
        "context": {
            "user": {"id": "user-1", "name": "User 1"},
            "extras": {"host_callback_base_url": "http://localhost:8000"},
        },
        "tools": [],
        "skills": [],
        "apis": [],
    }

    client = TestClient(app)
    unauthorized = client.post("/api/v1/host/bind", json=payload)
    assert unauthorized.status_code == 401

    forbidden = client.post(
        "/api/v1/host/bind",
        headers={"X-Aether-Platform-Secret": platform["host_secret"]},
        json={**payload, "platform_key": "standalone"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "平台密钥与目标平台不匹配"


def test_host_bind_uses_conversation_key_to_control_reuse(tmp_path):
    initialize_isolated_runtime(tmp_path)

    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None

    platform = store_service.create_platform(
        platform_key="poc-conversation",
        display_name="POC Conversation",
        host_type="embedded",
        description="host conversation test platform",
        owner_user_id=admin.user_id,
    )

    client = TestClient(app)
    headers = {"X-Aether-Platform-Secret": platform["host_secret"]}

    def bind(conversation_key: str):
        response = client.post(
            "/api/v1/host/bind",
            headers=headers,
            json={
                "platform_key": platform["platform_key"],
                "host_name": "POC",
                "conversation_key": conversation_key,
                "context": {
                    "user": {"id": "user-1", "name": "User 1"},
                    "extras": {"host_callback_base_url": "http://localhost:8000"},
                },
                "tools": [],
                "skills": [],
                "apis": [],
            },
        )
        assert response.status_code == 200
        return response.json()["data"]

    first = bind("thread-a")
    second = bind("thread-a")
    third = bind("thread-b")

    assert first["conversation_id"] == second["conversation_id"]
    assert first["session_id"] == second["session_id"]
    assert first["conversation_key"] == "thread-a"
    assert third["conversation_id"] != first["conversation_id"]
    assert third["session_id"] != first["session_id"]
    assert third["conversation_key"] == "thread-b"


def test_host_tool_requires_auth_when_injection_is_enabled():
    session = AgentSession(
        session_id="sess-host-auth",
        host_context={"extras": {"host_callback_base_url": "http://localhost:8000"}},
        host_tools=[
            {
                "name": "secure_tool",
                "description": "secure tool",
                "endpoint": "/api/tool",
                "requires_auth": True,
                "auth_inject": True,
            }
        ],
    )

    with pytest.raises(RuntimeError, match="未提供 host auth"):
        asyncio.run(tool_service.execute(session, "secure_tool", {}))


def test_session_workboard_supports_manual_crud(tmp_path):
    initialize_isolated_runtime(tmp_path)

    login = auth_service.login_with_password(
        settings.auth_system_admin_username,
        settings.auth_system_admin_password,
    )
    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None
    session = conversation_service.bootstrap_admin_workbench(admin)

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {login.token}"}

    update_response = client.patch(
        f"/api/v1/agent/sessions/{session.session_id}/workboard",
        headers=headers,
        json={
            "ops": [
                {
                    "op": "add_item",
                    "id": "item-a",
                    "title": "Review TODO UX",
                    "notes": "Need direct edit controls",
                    "priority": "high",
                    "status": "pending",
                    "source": "user",
                    "owner": "user",
                },
                {
                    "op": "update_item",
                    "id": "item-a",
                    "status": "in_progress",
                    "notes": "Editing in progress",
                },
            ]
        },
    )

    assert update_response.status_code == 200
    payload = update_response.json()["data"]
    assert payload["items"][0]["id"] == "item-a"
    assert payload["items"][0]["status"] == "in_progress"
    assert payload["items"][0]["source"] == "user"

    get_response = client.get(
        f"/api/v1/agent/sessions/{session.session_id}/workboard",
        headers=headers,
    )
    assert get_response.status_code == 200
    assert get_response.json()["data"]["items"][0]["notes"] == "Editing in progress"

    delete_response = client.patch(
        f"/api/v1/agent/sessions/{session.session_id}/workboard",
        headers=headers,
        json={"ops": [{"op": "remove_item", "id": "item-a"}]},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["data"]["items"] == []
