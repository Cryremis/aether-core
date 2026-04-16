# backend/tests/test_auth_and_sessions.py
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.auth_service import auth_service
from app.services.conversation_service import conversation_service
from app.services.session_service import session_service
from app.services.store import store_service


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
        description="测试平台",
        owner_user_id=admin.user_id,
    )

    first_session, _ = conversation_service.bootstrap_host_workbench(
        platform_key=platform["platform_key"],
        external_user_id="user-a",
        external_user_name="用户 A",
        external_org_id="org-a",
        conversation_id=None,
        conversation_key="conversation-a",
        host_name="Dash",
        host_type="embedded",
    )
    second_session, _ = conversation_service.bootstrap_host_workbench(
        platform_key=platform["platform_key"],
        external_user_id="user-b",
        external_user_name="用户 B",
        external_org_id="org-a",
        conversation_id=None,
        conversation_key="conversation-b",
        host_name="Dash",
        host_type="embedded",
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
        description="嵌入测试平台",
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
            "host_type": "embedded",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "平台密钥与目标平台不匹配"


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
            "displayNameCn": "张三",
            "email": "zhangsan@example.com",
        }

    monkeypatch.setattr("app.services.auth_service.oauth_service.exchange_code_for_token", fake_exchange_code_for_token)
    monkeypatch.setattr("app.services.auth_service.oauth_service.get_user_info", fake_get_user_info)

    result = asyncio.run(auth_service.login_with_w3("demo-code", "http://localhost/callback"))

    assert result.user.provider == "w3"
    assert result.user.full_name == "张三"
    assert result.user.role == "platform_admin"
