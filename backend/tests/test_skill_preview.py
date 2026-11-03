import io
import json
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app.main import app
from app.services.session_service import session_service
from app.services.store import store_service
from test_host_agent_orchestration import create_platform, initialize_isolated_runtime
from test_session_identity import bind


def package(body: str) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("demo/SKILL.md", f"---\nname: demo\ndescription: Example\n---\n{body}")
        archive.writestr("demo/references/usage.txt", "Example input")
        archive.writestr("demo/assets/binary.bin", b"\0\x01")
    return buffer.getvalue()


def test_skill_preview_reads_selected_version_and_installs_same_version(tmp_path):
    initialize_isolated_runtime(tmp_path)
    client = TestClient(app)
    platform = create_platform()
    bound = bind(client, platform, context={"user": {"id": "a:27", "name": "发布用户", "username": "acct27"}})
    sid = bound["session_id"]
    headers = {"Authorization": f"Bearer {bound['token']}"}
    params = {"session_id": sid}
    published = client.post("/api/v1/extensions", params=params, headers=headers,
        data={"kind": "skill", "name": "demo"}, files={"artifact": ("demo.zip", package("Version one"))})
    assert published.status_code == 200, published.text
    item = published.json()["data"]
    assert item["submitter_name"] == "发布用户"
    assert item["submitter_account"] == "acct27"
    assert item["submitter_user_id"] is None
    assert "author_key" not in item
    updated = client.post("/api/v1/extensions", params=params, headers=headers, files={"artifact": ("demo.zip", package("Version two"))},
        data={"kind": "skill", "name": "demo", "entry_id": item["entry_id"], "version": "2.0.0", "expected_revision": 1, "base_version": "1.0.0"})
    assert updated.status_code == 200, updated.text
    endpoint = f"/api/v1/extensions/{item['entry_id']}"
    old = {**params, "version": "1.0.0"}
    assert client.get(f"{endpoint}/preview", params=old).status_code == 401
    detail = client.get(f"{endpoint}/preview", params=old, headers=headers).json()["data"]
    assert "Version one" in detail["file"]["text"]
    assert {row["version"] for row in detail["versions"]} == {"1.0.0", "2.0.0"}
    assert len(detail["files"]) == 3
    def read_file(path):
        return client.get(f"{endpoint}/preview", params={**old, "file_path": path}, headers=headers)
    assert read_file("demo/references/usage.txt").json()["data"]["file"]["text"] == "Example input"
    assert read_file("demo/assets/binary.bin").json()["data"]["file"]["text"] is None
    assert read_file("../SKILL.md").status_code == 404
    install = client.post(f"{endpoint}/install", params={**old, "scope": "session"}, headers=headers)
    assert install.status_code == 200, install.text
    session_service._sessions.pop(sid)
    preview_params = {**params, "name": "demo", "source": "upload"}
    preview = client.get("/api/v1/agent/skills/preview", params=preview_params, headers=headers)
    assert preview.status_code == 200, preview.text
    assert "Version one" in preview.json()["data"]["file"]["text"]
    assert sid not in session_service._sessions
    assert client.get("/api/v1/agent/skills/preview", params={**preview_params, "file_path": "../session.json"}, headers=headers).status_code == 404

    bind(client, platform, session_id=sid, context={"user": {"id": "a:27", "name": "改名后的用户"}})
    listing = client.get("/api/v1/extensions", params=params, headers=headers).json()["data"]
    assert listing[0]["submitter_name"] == "改名后的用户"
    assert listing[0]["submitter_account"] == "acct27"
    with store_service._connect() as conn:
        event = conn.execute("SELECT actor,detail_json FROM extension_audit_events WHERE entry_id=? ORDER BY revision", (item["entry_id"],)).fetchone()
    assert event["actor"] == f"embed:{platform['platform_id']}:a:27"
    assert json.loads(event["detail_json"])["publisher"]["display_name"] == "发布用户"


def test_existing_embed_entry_migration_uses_same_platform_identity(tmp_path):
    initialize_isolated_runtime(tmp_path)
    client = TestClient(app)
    platform = create_platform()
    platform_id = platform["platform_id"]
    bind(client, platform, context={"user": {"id": "97", "name": "历史发布者"}})
    other = create_platform("other")
    bind(client, other, context={"user": {"id": "97", "name": "其他平台用户"}})
    with store_service._connect() as conn:
        conn.execute("DROP TABLE extension_entries")
        conn.execute("""CREATE TABLE extension_entries (entry_id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
            description TEXT NOT NULL, submitter_user_id INTEGER NOT NULL, visibility TEXT NOT NULL,
            current_version TEXT NOT NULL, usage_count INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            platform_id INTEGER, author_key TEXT, UNIQUE(platform_id, author_key, kind, name))""")
        conn.execute("INSERT INTO extension_entries VALUES ('legacy','skill','demo','',0,'platform','1.0.0',0,'2026','2026',?,?)",
                     (platform_id, f"embed:{platform_id}:97"))
        conn.execute("DELETE FROM platform_external_users")
    store_service.initialize()
    from app.services.extension_store_service import extension_store_service
    migrated = extension_store_service.list_entries(platform_id)[0]
    assert migrated["submitter_user_id"] is None
    assert migrated["submitter_external_user_id"] == "97"
    assert migrated["submitter_name"] == "历史发布者"
    assert migrated["submitter_display_name_snapshot"] is None
