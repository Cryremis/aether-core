from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

import app.services.session_service as session_service_module
from app.services.session_service import SessionService
from app.services.session_types import AgentSession


def make_service(metadata_path: Path) -> SessionService:
    service = SessionService()
    service._metadata_path = lambda session_id: metadata_path  # noqa: SLF001
    return service


def test_metadata_write_retries_transient_windows_permission_error(monkeypatch, tmp_path):
    metadata_path = tmp_path / "session.json"
    service = make_service(metadata_path)
    session = AgentSession(session_id="sess_persist", workspace_id="ws_persist")
    real_replace = os.replace
    replace_attempts = 0

    def flaky_replace(source, target):
        nonlocal replace_attempts
        replace_attempts += 1
        if replace_attempts == 1:
            raise PermissionError("simulated transient lock")
        real_replace(source, target)

    monkeypatch.setattr(session_service_module.os, "replace", flaky_replace)
    monkeypatch.setattr(session_service_module.time, "sleep", lambda _seconds: None)

    service._write_metadata(session)  # noqa: SLF001

    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["session_id"] == "sess_persist"
    assert replace_attempts == 2
    assert not list(tmp_path.glob("session.json.*.tmp"))


def test_metadata_writes_are_serialized_per_session(tmp_path):
    metadata_path = tmp_path / "session.json"
    service = make_service(metadata_path)
    session = AgentSession(session_id="sess_persist", workspace_id="ws_persist")

    def write_once(index: int) -> None:
        session.messages = [{"role": "user", "content": str(index)}]
        service._write_metadata(session)  # noqa: SLF001

    threads = [threading.Thread(target=write_once, args=(index,)) for index in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["session_id"] == "sess_persist"
    assert payload["messages"][0]["content"].isdigit()
    assert not list(tmp_path.glob("session.json.*.tmp"))


def test_metadata_write_cleans_temporary_file_on_persistent_error(monkeypatch, tmp_path):
    metadata_path = tmp_path / "session.json"
    service = make_service(metadata_path)
    session = AgentSession(session_id="sess_persist", workspace_id="ws_persist")

    def refused_replace(_source, _target):
        raise PermissionError("simulated persistent lock")

    monkeypatch.setattr(session_service_module.os, "replace", refused_replace)
    monkeypatch.setattr(session_service_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(PermissionError):
        service._write_metadata(session)  # noqa: SLF001

    assert not list(tmp_path.glob("session.json.*.tmp"))
