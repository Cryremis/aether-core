from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from app.core.config import settings
from app.services.session_types import AgentSession


T = TypeVar("T", bound=BaseModel)


class RuntimeStateStore:
    def __init__(self) -> None:
        self._locks: dict[tuple[str, str], threading.RLock] = {}
        self._global_lock = threading.Lock()

    def _get_lock(self, session_id: str, name: str) -> threading.RLock:
        key = (session_id, name)
        with self._global_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
            return lock

    def path_for(self, session: AgentSession, name: str) -> Path:
        if session.workspace is not None:
            return Path(session.workspace.metadata_dir) / f"{name}.json"
        return settings.sessions_root / session.session_id / "sandbox" / "metadata" / f"{name}.json"

    def load(self, session: AgentSession, name: str, model_type: type[T], default_factory) -> T:
        path = self.path_for(session, name)
        lock = self._get_lock(session.session_id, name)
        with lock:
            if not path.exists():
                state = default_factory()
                self._write_unlocked(path, state)
                return state
            try:
                return model_type.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                broken_path = path.with_suffix(f".broken-{path.stat().st_mtime_ns}.json")
                shutil.move(str(path), str(broken_path))
                state = default_factory()
                self._write_unlocked(path, state)
                return state

    def save(self, session: AgentSession, name: str, state: BaseModel) -> None:
        path = self.path_for(session, name)
        lock = self._get_lock(session.session_id, name)
        with lock:
            self._write_unlocked(path, state)

    def _write_unlocked(self, path: Path, state: BaseModel) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)


runtime_state_store = RuntimeStateStore()
