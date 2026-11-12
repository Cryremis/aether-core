"""SQLite 操作级恢复。只重放数据库操作，不重放工具、文件写入或模型调用。"""
from __future__ import annotations

import logging
import random
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from functools import wraps


logger = logging.getLogger(__name__)
_local = threading.local()
RETRY_DELAYS = (0.2, 0.5, 1.0)


@dataclass
class Operation:
    name: str
    committed: bool = False
    uncertain_commit: bool = False
    memo: dict = field(default_factory=dict)


def current_operation() -> Operation | None:
    stack = getattr(_local, "operations", ())
    return stack[-1] if stack else None


def note_commit() -> None:
    for operation in getattr(_local, "operations", ()):
        operation.committed = True


def retryable_sqlite_error(exc: sqlite3.Error) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        if code & 0xff == sqlite3.SQLITE_BUSY:
            return True
        # 不把数据损坏、内存不足或原子写协议故障当成短暂 I/O 抖动。
        return code in {sqlite3.SQLITE_IOERR, *(
            getattr(sqlite3, "SQLITE_IOERR_" + suffix, -1)
            for suffix in ("READ", "WRITE", "FSYNC", "DIR_FSYNC", "TRUNCATE", "FSTAT",
                           "UNLOCK", "RDLOCK", "DELETE", "ACCESS", "CHECKRESERVEDLOCK",
                           "LOCK", "CLOSE", "DIR_CLOSE", "SHMOPEN", "SHMSIZE", "SHMMAP", "SEEK")
        )}
    return isinstance(exc, sqlite3.OperationalError) and str(exc).strip().lower() in {
        "disk i/o error", "database is locked", "database is busy",
    }


def retry_sqlite(function=None, *, reconcile_commit: bool = False):
    """包围完整数据库方法。已提交的多步操作及未知提交结果默认禁止重放。"""
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            operation = Operation(function.__qualname__)
            stack = getattr(_local, "operations", None)
            if stack is None:
                stack = _local.operations = []
            stack.append(operation)
            try:
                for attempt in range(4):
                    try:
                        return function(*args, **kwargs)
                    except sqlite3.Error as exc:
                        if getattr(exc, "_retry_defer", False):
                            # 嵌套读取失败交给外层事务先回滚，不能带着外层写锁退避。
                            raise
                        safe = getattr(exc, "_retry_rolled_back", False)
                        unknown_commit = getattr(exc, "_retry_phase", None) == "commit_unknown"
                        operation.uncertain_commit |= unknown_commit
                        retry = (attempt < 3 and not operation.committed
                            and not getattr(exc, "_retry_exhausted", False)
                            and retryable_sqlite_error(exc)
                            and (safe or (unknown_commit and reconcile_commit)))
                        logger.warning(
                            "SQLite operation failed op=%s attempt=%s phase=%s sqlite_errorcode=%s sqlite_errorname=%s retry=%s",
                            operation.name, attempt + 1, getattr(exc, "_retry_phase", "operation"),
                            getattr(exc, "sqlite_errorcode", None), getattr(exc, "sqlite_errorname", None), retry,
                        )
                        if not retry:
                            exc._retry_exhausted = True
                            raise
                        # _connect 已完成 rollback/释放连接及锁，退避不持有事务。
                        time.sleep(RETRY_DELAYS[attempt] + random.uniform(0, 0.02))
            finally:
                stack.pop()
        return run
    return decorate(function) if function is not None else decorate


class ThreadConnection:
    """仅由创建它的线程持有；线程结束时随 threading.local 一起释放。"""
    def __init__(self, key, connection):
        self.key = key
        self.connection = connection
        self.depth = 0

    def close(self):
        if self.connection is not None:
            connection, self.connection = self.connection, None
            try:
                connection.close()
            except sqlite3.Error:
                # 已失效连接不可复用，也不能让清理异常覆盖最初的诊断。
                logger.warning("SQLite connection discard failed", exc_info=False)

    def __del__(self):
        self.close()
