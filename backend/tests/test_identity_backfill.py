from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.services.context.history_store import history_store
from app.services.session_service import session_service
from app.services.store import store_service
from test_host_agent_orchestration import initialize_isolated_runtime, create_platform
from test_session_identity import bind, legacy_state

spec = importlib.util.spec_from_file_location("identity_backfill", Path(__file__).resolve().parents[2] / "ops" / "backfill_session_identity.py")
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


def run(**kwargs):
    return backfill.audit_and_backfill(store_service._db_path, settings.sessions_root, **kwargs)


@pytest.mark.parametrize("encrypted", [False, True])
def test_backfill_dry_run_idempotent_and_preserves_history(tmp_path, monkeypatch, encrypted):
    initialize_isolated_runtime(tmp_path)
    platform = create_platform()
    data = bind(TestClient(app), platform)
    if encrypted:
        monkeypatch.setattr(settings, "capability_secret_encryption_key", "identity-test-key")
        monkeypatch.setenv("CAPABILITY_SECRET_ENCRYPTION_KEY", "identity-test-key")
    sid = data["session_id"]
    original = legacy_state(sid, platform_id=None, external_user_id=None,
        messages=[{"role": "user", "content": "retain this", "message_id": "keep-me"}],
        compaction_audit=[{"id": "audit", "summary": "preserve"}])
    path = session_service._metadata_path(sid)
    before = path.read_bytes()
    report = run()
    assert report["eligible"] == 1 and report["repaired"] == 0
    assert report["sources"]["json"]["platform_null"] == 1
    assert path.read_bytes() == before
    assert history_store.snapshot(sid) == original
    with pytest.raises(ValueError, match="offline"):
        run(apply=True)
    assert run(apply=True, offline=True)["repaired"] == 1
    for payload in [json.loads(path.read_text(encoding="utf-8")), history_store.snapshot(sid)]:
        assert payload["platform_id"] == platform["platform_id"]
        assert payload["external_user_id"] == "27"
        for key in ("messages", "compaction_audit", "host_context"):
            assert payload[key] == original[key]
    assert run(apply=True, offline=True)["repaired"] == 0
    assert len(list(path.parent.glob("*.backup"))) == 2
    assert session_service.get_or_create(sid).platform_id == platform["platform_id"]
    session_service.persist(session_service.get_or_create(sid))  # 校验快照 revision/digest 与写入服务兼容。


def test_backfill_reports_conflicts_and_skips_active_runs(tmp_path):
    initialize_isolated_runtime(tmp_path)
    platform = create_platform()
    client = TestClient(app)
    bad = bind(client, platform, conversation_key="conflict")["session_id"]
    active = bind(client, platform, conversation_key="active")["session_id"]
    legacy_state(bad, platform_id=999, external_user_id=None)
    legacy_state(active, platform_id=None, external_user_id=None)
    store_service.create_agent_run(run_id="run_identity", session_id=active)
    files = {sid: session_service._metadata_path(sid).read_bytes() for sid in [bad, active]}
    report = run(apply=True, offline=True)
    assert report["conflicts"] == 1 and report["repaired"] == 0
    assert report["sources"]["json"]["platform_mismatch"] == 1
    assert {item["status"] for item in report["items"]} == {"conflict", "active_run_skipped"}
    assert all(session_service._metadata_path(sid).read_bytes() == raw for sid, raw in files.items())


def test_interrupted_json_projection_can_be_repaired_by_rerun(tmp_path, monkeypatch):
    initialize_isolated_runtime(tmp_path)
    platform = create_platform()
    sid = bind(TestClient(app), platform)["session_id"]
    legacy_state(sid, platform_id=None, external_user_id=None)
    replace = backfill._replace
    def fail(*args):
        raise OSError("simulated interrupted file projection")
    monkeypatch.setattr(backfill, "_replace", fail)
    assert run(apply=True, offline=True)["items"][0]["status"] == "error"
    assert history_store.snapshot(sid)["platform_id"] == platform["platform_id"]
    monkeypatch.setattr(backfill, "_replace", replace)
    assert run(apply=True, offline=True)["repaired"] == 1
    assert json.loads(session_service._metadata_path(sid).read_text(encoding="utf-8"))["platform_id"] == platform["platform_id"]
