import asyncio
import io
import zipfile
from pathlib import Path

import pytest

from app.core.config import settings
from app.services.session_service import session_service
from app.services.store import store_service
from app.services.extension_store_service import extension_store_service as market
from app.services.skill_authoring_service import skill_authoring_service as authoring
from app.services.skill_package import install_package
from app.services.skill_tools import visible_schemas
from app.services.tool_service import tool_service


SKILL = b"---\nname: demo\ndescription: demo\n---\n\nDo the work.\n"


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
    monkeypatch.setattr(store_service, "_db_path", tmp_path / "test.db")
    store_service.initialize()
    session = session_service.get_or_create("skill_" + tmp_path.name)
    session.owner_user_id = 1
    session.platform_id = 7
    return session


def publish(platform=7, actor="user:1", **kwargs):
    return market.publish(kind="skill", name="demo", description="demo", version=kwargs.pop("version", "1.0.0"), submitter_user_id=1, raw_bytes=SKILL, filename="SKILL.md", platform_id=platform, author_key=actor, **kwargs)


def test_platform_filter_covers_inspect_and_install(session):
    entry = publish()
    assert market.list_entries(8) == []
    with pytest.raises(FileNotFoundError):
        market.inspect(entry["entry_id"], 8)
    with pytest.raises(FileNotFoundError):
        market.install(entry["entry_id"], platform_id=8, scope="user", identity={"user_id": 1})
    assert publish(platform=8)["entry_id"] != entry["entry_id"]
    with pytest.raises(ValueError, match="ID"):
        publish(entry_id="../../outside")


def test_creator_only_blocks_other_author_and_shared_updates_detect_conflict(session):
    private = publish()
    with pytest.raises(PermissionError):
        publish(actor="user:2", entry_id=private["entry_id"], version="1.1.0", expected_revision=1, base_version="1.0.0")
    shared = publish(actor="user:3", maintenance_policy="platform_members")
    updated = publish(actor="user:2", entry_id=shared["entry_id"], version="1.1.0", expected_revision=1, base_version="1.0.0")
    assert updated["revision"] == 2
    assert updated["author_key"] == "user:3"
    assert updated["maintenance_policy"] == "platform_members"
    creator_update = publish(actor="user:3", entry_id=shared["entry_id"], version="1.2.0", expected_revision=2, base_version="1.1.0")
    assert creator_update["maintenance_policy"] == "platform_members"
    with pytest.raises(ValueError, match="VERSION_CONFLICT"):
        publish(actor="user:4", entry_id=shared["entry_id"], version="1.2.0", expected_revision=1, base_version="1.0.0")
    assert market.inspect(shared["entry_id"], 7, "1.0.0")["version"] == "1.0.0"


def test_snapshot_is_frozen_and_cannot_be_used_by_another_session(session):
    draft = authoring.execute(session, "skill_init", {"name": "demo", "description": "demo", "content": "original"})
    snapshot = authoring.execute(session, "skill_validate", {"draft_id": draft["draft_id"]})
    (session.workspace.root / draft["path"]).write_text("changed", encoding="utf-8")
    authoring.execute(session, "skill_install", {"snapshot_id": snapshot["snapshot_id"]})
    from app.services.skill_service import skill_service
    assert "original" in (skill_service.session_skill_root(session) / "demo" / "SKILL.md").read_text("utf-8")
    other = session_service.get_or_create("other_" + session.session_id)
    other.owner_user_id = session.owner_user_id
    other.platform_id = session.platform_id
    with pytest.raises(FileNotFoundError):
        authoring.execute(other, "skill_install", {"snapshot_id": snapshot["snapshot_id"]})


def test_invalid_package_preserves_existing_install(session):
    target = session.workspace.skills_dir
    install_package(target, "demo.md", SKILL)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("demo/SKILL.md", SKILL)
        archive.writestr("../outside.txt", "bad")
    with pytest.raises(RuntimeError):
        install_package(target, "bad.zip", buffer.getvalue())
    assert (target / "demo" / "SKILL.md").read_bytes() == SKILL


def test_discovery_changes_next_request_and_respects_allowlist(session):
    async def run():
        schemas = tool_service._registry.get_schemas()
        assert "skill_init" not in {s["function"]["name"] for s in visible_schemas(session, schemas)}
        session.allowed_tools = ["discover_tools", "skill_init"]
        await tool_service.execute(session, "discover_tools", {"bundle_id": "skill_authoring"})
        assert session.loaded_skill_tools == ["skill_init"]
        assert "skill_init" in {s["function"]["name"] for s in visible_schemas(session, schemas)}
        session.allowed_tools = ["discover_tools"]
        denied = await tool_service.execute(session, "skill_init", {"name": "demo", "description": "demo"})
        assert denied.status != "success"
    asyncio.run(run())


def test_store_migration_is_repeatable_and_retains_entries(session):
    entry = publish()
    store_service.initialize()
    store_service.initialize()
    assert market.inspect(entry["entry_id"], 7)["name"] == "demo"


def test_shared_member_cannot_change_policy_and_creator_can_unlist(session):
    entry = publish(maintenance_policy="platform_members")
    with pytest.raises(PermissionError):
        market.manage(entry["entry_id"], 7, "user:2", 1, maintenance_policy="creator_only")
    market.manage(entry["entry_id"], 7, "user:1", 1, status="unlisted")
    assert market.list_entries(7) == []
    market.manage(entry["entry_id"], 7, "user:1", 2, status="active")
    assert len(market.list_entries(7)) == 1


def test_publish_retry_is_idempotent(session):
    entry = publish(idempotency_key="request-1")
    retry = publish(idempotency_key="request-1")
    assert retry["entry_id"] == entry["entry_id"]
    assert retry["idempotent"] is True


def test_session_install_does_not_leak_through_shared_workspace(session):
    from app.services.skill_service import skill_service
    draft = authoring.execute(session, "skill_init", {"name": "isolated", "description": "demo"})
    snapshot = authoring.execute(session, "skill_validate", {"draft_id": draft["draft_id"]})
    authoring.execute(session, "skill_install", {"snapshot_id": snapshot["snapshot_id"]})
    other = session_service.get_or_create("shared_workspace_" + session.session_id)
    other.workspace = session.workspace
    assert "isolated" in {item.name for item in skill_service.list_for_session(session)}
    assert "isolated" not in {item.name for item in skill_service.list_for_session(other)}


def test_embed_market_routes_enforce_platform_and_author(session):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.deps import AuthContext, get_auth_context
    from app.api.routes.extensions import router
    app = FastAPI()
    app.include_router(router)
    auth = AuthContext(kind="embed", platform_id=7, external_user_id="alice")
    app.dependency_overrides[get_auth_context] = lambda: auth
    with TestClient(app) as client:
        response = client.post("/api/v1/extensions", data={"kind": "skill", "name": "demo"}, files={"artifact": ("SKILL.md", SKILL)})
        assert response.status_code == 200
        entry = response.json()["data"]
        assert entry["author_key"] == "embed:7:alice"
        auth.platform_id = 8
        assert client.get("/api/v1/extensions").json()["data"] == []
        assert client.get(f"/api/v1/extensions/{entry['entry_id']}").status_code == 404
        auth.platform_id = 7
        auth.external_user_id = "bob"
        assert client.patch(f"/api/v1/extensions/{entry['entry_id']}", json={"expected_revision": 1, "maintenance_policy": "platform_members"}).status_code == 403


def test_engine_loads_tools_on_next_round_with_static_catalog(session, monkeypatch):
    import json
    from app.runtime.engine import agent_engine
    session.tool_refresh_policy = "static_run"
    observed = []

    async def stream(config, messages, tools, **kwargs):
        observed.append({schema["function"]["name"] for schema in tools})
        if len(observed) == 1:
            delta = {"tool_calls": [{"index": 0, "id": "load_skills", "type": "function", "function": {"name": "discover_tools", "arguments": json.dumps({"bundle_id": "skill_authoring"})}}]}
            reason = "tool_calls"
        else:
            delta, reason = {"content": "done"}, "stop"
        yield {"choices": [{"delta": delta, "finish_reason": reason}]}

    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", stream)
    async def run():
        return [event async for event in agent_engine.stream_chat(session, "制作技能")]
    asyncio.run(run())
    assert len(observed) == 2
    assert "skill_init" not in observed[0]
    assert "skill_init" in observed[1]


def test_user_skill_resources_are_available_inside_sandbox(session):
    from app.services.capability_service import capability_service
    from app.services.skill_service import skill_service
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("resource-demo/SKILL.md", "---\nname: resource-demo\ndescription: resources\n---\nRead ${AETHER_SKILL_DIR}/references/example.txt")
        archive.writestr("resource-demo/references/example.txt", "sample data")
    capability_service.install_skill(raw_bytes=buffer.getvalue(), filename="demo.zip", user_id=session.owner_user_id)
    loaded = skill_service.invoke_skill(session, "resource-demo")
    content = loaded["injected_messages"][0]["content"]
    assert "/workspace/skills/.available/" in content
    resources = list((session.workspace.skills_dir / ".available").rglob("example.txt"))
    assert resources[0].read_text("utf-8") == "sample data"
