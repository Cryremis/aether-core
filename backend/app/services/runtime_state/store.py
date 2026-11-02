from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, TypeVar

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

    def update(self, session: AgentSession, name: str, model_type: type[T], default_factory, updater: Callable[[T], T]) -> T:
        path = self.path_for(session, name)
        lock = self._get_lock(session.session_id, name)
        with lock:
            if not path.exists():
                state = default_factory()
            else:
                try:
                    state = model_type.model_validate_json(path.read_text(encoding="utf-8"))
                except Exception:
                    broken_path = path.with_suffix(f".broken-{path.stat().st_mtime_ns}.json")
                    shutil.move(str(path), str(broken_path))
                    state = default_factory()

            next_state = updater(state)
            self._write_unlocked(path, next_state)
            return next_state

    def _write_unlocked(self, path: Path, state: BaseModel) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(
                json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # 与会话快照一致：Windows 索引/杀毒短暂占用不能打断状态提交。
            for attempt in range(20):
                try:
                    temp_path.replace(path)
                    return
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.05)
        finally:
            temp_path.unlink(missing_ok=True)


runtime_state_store = RuntimeStateStore()
