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


def test_conversations_are_isolated_for_admins_and_host_users(tmp_path):
    initialize_isolated_runtime(tmp_path)

    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    debug_user = store_service.get_user_by_username(settings.auth_debug_username)
    assert admin is not None
    assert debug_user is not None

    admin_session = conversation_service.bootstrap_admin_workbench(admin)
    debug_session = conversation_service.bootstrap_admin_workbench(debug_user)

    admin_items = conversation_service.list_for_admin(admin)
    debug_items = conversation_service.list_for_admin(debug_user)

    assert [item.session_id for item in admin_items] == [admin_session.session_id]
    assert [item.session_id for item in debug_items] == [debug_session.session_id]

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


def test_w3_whitelist_supports_account_or_employee_number(tmp_path, monkeypatch):
    initialize_isolated_runtime(tmp_path)

    store_service.upsert_admin_whitelist(
        provider="w3",
        provider_user_id="A1234567",
        full_name="A1234567",
        email=None,
        role="platform_admin",
    )

    async def fake_exchange_code_for_token(code: str, redirect_uri: str):
        return {"access_token": "fake-token"}

    async def fake_get_user_info(access_token: str):
        return {
            "uuid": "uuid-001",
            "uid": "A1234567",
            "employeeNumber": "1234567",
            "displayNameCn": "Zhang San",
            "email": "zhangsan@example.com",
        }

    monkeypatch.setattr("app.services.auth_service.oauth_service.exchange_code_for_token", fake_exchange_code_for_token)
    monkeypatch.setattr("app.services.auth_service.oauth_service.get_user_info", fake_get_user_info)

    result = asyncio.run(auth_service.login_with_w3("demo-code", "http://localhost/callback"))

    assert result.user.provider == "w3"
    assert result.user.full_name == "Zhang San"
    assert result.user.role == "platform_admin"


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
