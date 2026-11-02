from pathlib import Path

import pytest

from app.services.runtime_state.models import WorkboardState
from app.services.runtime_state.store import RuntimeStateStore


@pytest.mark.parametrize("recover", [True, False])
def test_runtime_state_atomic_write_handles_transient_file_occupation(tmp_path, monkeypatch, recover):
    target = tmp_path / "workboard.json"
    target.write_text("original", encoding="utf-8")
    real_replace = Path.replace
    attempts = []

    def replace(source, destination):
        attempts.append(source)
        if not recover or len(attempts) <= 2:
            raise PermissionError("file temporarily occupied")
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr("app.services.runtime_state.store.time.sleep", lambda seconds: None)
    state = WorkboardState(session_id="session")
    store = RuntimeStateStore()
    if recover:
        store._write_unlocked(target, state)
        assert WorkboardState.model_validate_json(target.read_text(encoding="utf-8")) == state
        assert len(attempts) == 3
    else:
        with pytest.raises(PermissionError):
            store._write_unlocked(target, state)
        assert target.read_text(encoding="utf-8") == "original"
        assert len(attempts) == 20
    assert not list(tmp_path.glob("*.tmp"))
