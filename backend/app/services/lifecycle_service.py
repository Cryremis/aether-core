from __future__ import annotations

import asyncio
import functools
import json
import os
import secrets
import time
from collections import Counter
from contextvars import ContextVar
from pathlib import Path
from typing import Any


class ServiceDraining(RuntimeError):
    def __init__(self) -> None:
        super().__init__("服务正在更新，暂不接收新任务，请稍后重试。")


class _Operation:
    def __init__(self, service: LifecycleService, label: str, continuation: bool) -> None:
        self.service = service
        self.label = label
        self.continuation = continuation

    def __enter__(self) -> None:
        self.key = self.service._acquire(self.label, continuation=self.continuation)
        self.context = self.service._current.set(self.key)

    def __exit__(self, exc_type, exc, traceback) -> None:
        # 不改写异常对象：沙箱错误包含 frozen dataclass，生成器式 contextmanager 会改写其 traceback。
        self.service._current.reset(self.context)
        self.service._work.pop(self.key, None)


class LifecycleService:
    """单进程业务排空；租约过期自动恢复接单，已接纳工作的后续步骤继续执行。"""

    LEASE_SECONDS = 30

    def __init__(self) -> None:
        self._work: dict[str, str] = {}
        self._current: ContextVar[str | None] = ContextVar("lifecycle_work", default=None)
        self._owner: str | None = None
        self._expires_at = 0.0
        self.ready = False
        self.token = secrets.token_urlsafe(32)
        self.instance_id = secrets.token_hex(16)
        self._control_file: Path | None = None

    @property
    def draining(self) -> bool:
        if self._owner is not None and time.monotonic() >= self._expires_at:
            self._owner = None
        return self._owner is not None

    def _acquire(self, label: str, *, continuation: bool = False) -> str:
        if self.draining and self._current.get() not in self._work and not (continuation and self._work):
            raise ServiceDraining()
        key = secrets.token_hex(12)
        self._work[key] = label
        return key

    def operation(self, label: str, *, continuation: bool = False) -> _Operation:
        return _Operation(self, label, continuation)

    def admitted(self, label: str):
        def decorate(function):
            @functools.wraps(function)
            async def wrapped(*args, **kwargs):
                with self.operation(label):
                    return await function(*args, **kwargs)
            return wrapped
        return decorate

    def create_task(self, coroutine, *, label: str) -> asyncio.Task:
        # 创建时登记，直到 task 完全退出才释放，覆盖首次调度前和 finally 收尾的窗口。
        try:
            key = self._acquire(label)
        except BaseException:
            coroutine.close()
            raise
        context = self._current.set(key)
        try:
            task = asyncio.create_task(coroutine)
        except BaseException:
            self._work.pop(key, None)
            coroutine.close()
            raise
        finally:
            self._current.reset(context)
        task.add_done_callback(lambda _: self._work.pop(key, None))
        return task

    def begin_drain(self, owner: str) -> dict[str, Any]:
        if self.draining and self._owner != owner:
            raise ValueError("已有另一个重启操作正在排空服务")
        self._owner = owner
        self._expires_at = time.monotonic() + self.LEASE_SECONDS
        return self.snapshot()

    def cancel_drain(self, owner: str) -> None:
        if self.draining and self._owner != owner:
            raise ValueError("不能取消其他重启操作")
        self._owner = None

    def snapshot(self) -> dict[str, Any]:
        draining = self.draining
        return {
            "pid": os.getpid(),
            "instance_id": self.instance_id,
            "ready": self.ready,
            "draining": draining,
            "drained": draining and not self._work,
            "active": dict(Counter(self._work.values())),
        }

    def start(self) -> None:
        self.ready = True
        self._owner = None
        control_file = os.environ.get("AETHERCORE_CONTROL_FILE")
        if not control_file:
            return
        self._control_file = Path(control_file)
        self._control_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._control_file.with_suffix(f".{self.instance_id}.tmp")
        try:
            with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "instance_id": self.instance_id, "token": self.token}, handle)
            os.replace(temporary, self._control_file)
        finally:
            temporary.unlink(missing_ok=True)

    def stop(self) -> None:
        self.ready = False
        if self._control_file is not None:
            try:
                current = json.loads(self._control_file.read_text(encoding="utf-8"))
                if current.get("instance_id") == self.instance_id:
                    self._control_file.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass


lifecycle_service = LifecycleService()
