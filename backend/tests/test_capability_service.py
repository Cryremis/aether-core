from pathlib import Path

import pytest

from app.core.config import settings
from app.services.capability_service import CapabilityValidationError, capability_service
from app.services.extension_store_service import extension_store_service
from app.services.session_service import session_service
from app.services.session_types import AgentSession
from app.services.store import store_service
from app.services.secret_store_service import secret_store_service


def initialize(tmp_path: Path) -> None:
    settings.storage_root = tmp_path / "storage"
    store_service._db_path = settings.resolved_storage_root / "test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    session_service._sessions.clear()
    store_service.initialize()


SKILL = b"---\nname: persistent-skill\ndescription: persisted\n---\n\nDo the work.\n"


def test_user_skill_is_cross_session_and_hard_deleted(tmp_path):
    initialize(tmp_path)
    capability_service.install_skill(raw_bytes=SKILL, filename="persistent.md", user_id=42)
    assert [item["name"] for item in capability_service.list_user_skills(user_id=42)] == ["persistent-skill"]
    assert capability_service.delete_skill("persistent-skill", user_id=42)
    assert capability_service.list_user_skills(user_id=42) == []


def test_mcp_scope_merge_and_browser_disable_override(tmp_path):
    initialize(tmp_path)
    platform = store_service.get_platform_by_key("standalone")
    assert platform
    user = capability_service.upsert_mcp({"name": "docs", "transport": "streamable_http", "url": "https://example.com/mcp"}, user_id=1)
    session = AgentSession(session_id="sess_test", owner_user_id=1, platform_id=platform["platform_id"])
    session.session_mcp = [dict(capability_service._validate_mcp({"name": "local", "transport": "stdio", "command": ["node", "server.js"]}), scope="session")]
    assert {item["name"] for item in capability_service.list_effective_mcp(session)} == {"docs", "local"}
    session.disabled_capability_ids = [user["id"], "mcp:local"]
    assert capability_service.list_effective_mcp(session) == []


def test_mcp_rejects_insecure_remote_and_shell_string(tmp_path):
    initialize(tmp_path)
    with pytest.raises(CapabilityValidationError):
        capability_service.upsert_mcp({"name": "bad", "transport": "streamable_http", "url": "http://example.com"}, user_id=1)
    with pytest.raises(CapabilityValidationError):
        capability_service.upsert_mcp({"name": "bad", "transport": "stdio", "command": "node server.js"}, user_id=1)
    with pytest.raises(CapabilityValidationError, match="环境变量名称"):
        capability_service.upsert_mcp({"name": "bad", "transport": "stdio", "command": ["node"], "env": {"--privileged": "1"}}, user_id=1)


def test_store_publish_and_install_increments_usage(tmp_path):
    initialize(tmp_path)
    entry = extension_store_service.publish(kind="skill", name="store-skill", description="demo", version="1.0.0", submitter_user_id=1, raw_bytes=SKILL, filename="SKILL.md")
    extension_store_service.install(entry["entry_id"], scope="user", identity={"user_id": 7})
    refreshed = next(item for item in extension_store_service.list_entries() if item["entry_id"] == entry["entry_id"])
    assert refreshed["usage_count"] == 1
    assert capability_service.list_user_skills(user_id=7)[0]["name"] == "persistent-skill"


def test_mcp_secrets_are_encrypted_and_never_returned(tmp_path):
    initialize(tmp_path)
    item = capability_service.upsert_mcp(
        {
            "name": "private-docs",
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer super-secret-token"},
        },
        user_id=1,
    )

    assert item["has_secrets"] is True
    assert "headers" not in item
    config_text = (settings.resolved_storage_root / "users" / "user_1" / "mcp.json").read_text(encoding="utf-8")
    assert "super-secret-token" not in config_text
    with store_service._connect() as conn:
        ciphertext = conn.execute("SELECT ciphertext FROM capability_secrets").fetchone()[0]
    assert "super-secret-token" not in ciphertext

    session = AgentSession(session_id="secret-session", owner_user_id=1)
    hydrated = capability_service.list_effective_mcp(session, include_secrets=True)[0]
    assert hydrated["headers"]["Authorization"] == "Bearer super-secret-token"
    secret_ref = hydrated["secret_ref"]
    assert capability_service.delete_mcp("private-docs", user_id=1)
    assert secret_store_service.get(secret_ref) == {}


def test_store_rejects_mcp_credentials_and_invalid_semver(tmp_path):
    initialize(tmp_path)
    secret_artifact = b'{"name":"unsafe","transport":"streamable_http","url":"https://example.com/mcp","headers":{"Authorization":"token"}}'
    with pytest.raises(ValueError, match="不能包含"):
        extension_store_service.publish(
            kind="mcp", name="unsafe", description="", version="1.0.0",
            submitter_user_id=1, raw_bytes=secret_artifact, filename="mcp.json",
        )
    with pytest.raises(ValueError, match="SemVer"):
        extension_store_service.publish(
            kind="skill", name="bad-version", description="", version="latest",
            submitter_user_id=1, raw_bytes=SKILL, filename="SKILL.md",
        )


def test_store_normalizes_standard_mcp_artifact(tmp_path):
    initialize(tmp_path)
    artifact = b'{"mcpServers":{"docs":{"url":"https://example.com/mcp"}}}'
    entry = extension_store_service.publish(
        kind="mcp", name="docs", description="", version="1.2.3",
        submitter_user_id=1, raw_bytes=artifact, filename="mcp.json",
    )
    extension_store_service.install(entry["entry_id"], scope="user", identity={"user_id": 9})
    installed = capability_service.list_mcp(user_id=9)[0]
    assert installed["name"] == "docs"
    assert installed["transport"] == "streamable_http"
