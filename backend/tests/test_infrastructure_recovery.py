"""基础设施故障注入：事务、事件发布、流恢复及取消都走实际生产入口。"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import settings
from app.core.model_errors import retryable_model_error, retryable_stream_error
from app.runtime.engine import agent_engine
from app.runtime.event_protocol import make_event
from app.services.agent_run_service import AgentRunService
from app.services.cross_chat_store import cross_chat_store
from app.services.durable_io import durable_io
from app.services.session_types import AgentSession
from app.services.sqlite_resilience import current_operation, retry_sqlite, retryable_sqlite_error
from app.services.store import store_service
from app.services.workspace_runtime_service import workspace_runtime_service
from test_agent_engine import build_session, collect_stream, initialize_store
from test_context_reliability_audit import chat_frame, setup_client, wire
from test_llm_client import FakeAsyncClient, FakeResponse, FakeStreamContext, make_runtime_config


class Fault:
    def __init__(self, count=2, *, stage="execute", code=sqlite3.SQLITE_IOERR_WRITE):
        self.remaining = count
        self.stage = stage
        self.code = code
        self.attempts = 0

    def hit(self):
        self.attempts += 1
        if self.remaining:
            self.remaining -= 1
            exc = sqlite3.OperationalError("disk I/O error")
            exc.sqlite_errorcode = self.code
            exc.sqlite_errorname = {sqlite3.SQLITE_IOERR_WRITE: "SQLITE_IOERR_WRITE",
                sqlite3.SQLITE_BUSY: "SQLITE_BUSY", sqlite3.SQLITE_CORRUPT: "SQLITE_CORRUPT"}.get(self.code, "SQLITE_IOERR")
            raise exc


def inject(monkeypatch, fault):
    class Connection(sqlite3.Connection):
        pending_event = False

        def execute(self, sql, parameters=()):
            operation = current_operation()
            if (fault.stage == "begin" and sql == "BEGIN IMMEDIATE" and operation is not None
                    and operation.name == "StoreService.append_agent_run_event"):
                fault.hit()
            target = "INSERT INTO run_events" in sql and len(parameters) > 2 and parameters[2] == "content_delta"
            if target:
                self.pending_event = True
                if fault.stage == "execute":
                    fault.hit()
            result = super().execute(sql, parameters)
            if target and fault.stage == "after_insert":
                fault.hit()
            return result

        def commit(self):
            if not self.pending_event:
                return super().commit()
            if fault.stage == "commit_before":
                fault.hit()
            result = super().commit()
            if fault.stage == "commit_after":
                fault.hit()
            self.pending_event = False
            return result

    store_service.close_thread_connection()
    def connect():
        conn = sqlite3.connect(store_service._db_path, timeout=5, factory=Connection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn
    monkeypatch.setattr(store_service, "_new_connection", connect)
    monkeypatch.setattr("app.services.sqlite_resilience.time.sleep", lambda delay: None)


@pytest.mark.parametrize("failures,stage", [(2, "execute"), (3, "execute"), (4, "execute"),
    (2, "begin"), (2, "after_insert"), (2, "commit_before"), (1, "commit_after")])
def test_run_event_io_recovery_is_ordered_and_exactly_once(monkeypatch, tmp_path, caplog, failures, stage):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    FakeAsyncClient.stream_responses = [wire(chat_frame("infra-sentinel-private"))]
    fault = Fault(failures, stage=stage)
    inject(monkeypatch, fault)
    service = AgentRunService()
    session = build_session(f"infra_{stage}_{failures}")

    async def run():
        run_id = await service.start_chat_run(session, "task")
        await service.wait_for_run(run_id)
        return run_id

    run_id = asyncio.run(run())
    rows = store_service.list_agent_run_events(run_id)
    assert [row["seq"] for row in rows] == list(range(1, len(rows) + 1))
    failed = failures == 4
    assert store_service.get_agent_run(run_id)["status"] == ("failed" if failed else "completed")
    assert sum(row["type"] == "error" for row in rows) == int(failed)
    assert sum(row["type"] == "content_delta" for row in rows) == int(not failed)
    diagnostics = [record.message for record in caplog.records if "SQLite operation failed" in record.message]
    assert len(diagnostics) == (1 if stage == "commit_after" else failures)
    assert all("sqlite_errorcode=778" in line and "sqlite_errorname=SQLITE_IOERR_WRITE" in line
        and "op=StoreService.append_agent_run_event" in line for line in diagnostics)
    assert all("infra-sentinel-private" not in line for line in diagnostics)


@pytest.mark.parametrize("code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_IOERR_SHMSIZE, sqlite3.SQLITE_IOERR])
def test_sqlite_error_whitelist_and_rollback(monkeypatch, tmp_path, code):
    initialize_store(tmp_path)
    store_service.create_agent_run(run_id="whitelist", session_id="whitelist")
    fault = Fault(code=code)
    inject(monkeypatch, fault)
    assert store_service.append_agent_run_event(run_id="whitelist", event_type="content_delta", payload={}) == 1
    assert fault.attempts == 3
    with store_service._connect() as conn:
        assert not conn.in_transaction
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_real_database_error_is_not_retried(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    store_service.create_agent_run(run_id="corrupt", session_id="corrupt")
    fault = Fault(4, code=sqlite3.SQLITE_CORRUPT)
    inject(monkeypatch, fault)
    with pytest.raises(sqlite3.OperationalError) as caught:
        store_service.append_agent_run_event(run_id="corrupt", event_type="content_delta", payload={})
    assert caught.value.sqlite_errorcode == sqlite3.SQLITE_CORRUPT
    assert fault.attempts == 1
    assert store_service.list_agent_run_events("corrupt") == []


def test_text_only_error_fallback_is_narrow():
    assert retryable_sqlite_error(sqlite3.OperationalError("disk I/O error"))
    assert retryable_sqlite_error(sqlite3.OperationalError("database is locked"))
    assert not retryable_sqlite_error(sqlite3.OperationalError("no such table: missing"))
    assert not retryable_sqlite_error(sqlite3.DatabaseError("database disk image is malformed"))


def test_retry_never_replays_a_previously_committed_transaction(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    monkeypatch.setattr("app.services.sqlite_resilience.time.sleep", lambda delay: None)
    calls = []
    @retry_sqlite
    def two_steps():
        calls.append(1)
        with store_service._connect() as conn:
            conn.execute("INSERT INTO cross_chat_deliveries (delivery_id,dispatch_key,source_conversation_id,source_run_id,target_conversation_id,mode,message,status,created_at,expires_at,next_attempt_at,updated_at) VALUES ('once','once','s','r','t','steer','x','queued','0','0','0','0')")
        with store_service._connect() as conn:
            raise sqlite3.OperationalError("disk I/O error")
    with pytest.raises(sqlite3.OperationalError):
        two_steps()
    assert calls == [1]
    assert cross_chat_store.get("once") is not None


def test_failed_rollback_discards_connection(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    original = store_service._new_connection
    closed = []
    class Connection(sqlite3.Connection):
        def rollback(self):
            raise sqlite3.OperationalError("disk I/O error")
        def close(self):
            closed.append(True)
            return super().close()
    store_service.close_thread_connection()
    monkeypatch.setattr(store_service, "_new_connection", lambda: sqlite3.connect(store_service._db_path, factory=Connection))
    with pytest.raises(sqlite3.OperationalError):
        with store_service._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            raise sqlite3.OperationalError("disk I/O error")
    assert closed == [True]
    monkeypatch.setattr(store_service, "_new_connection", original)
    with store_service._connect() as conn:
        assert not conn.in_transaction
        with ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises(sqlite3.ProgrammingError):
                executor.submit(conn.execute, "SELECT 1").result()


def test_nested_read_retries_only_after_outer_transaction_releases_lock(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    fault = Fault()
    delays = []
    def backoff(delay):
        assert not store_service._lock._is_owned()
        assert store_service._connections.holder.connection is None
        delays.append(delay)
    monkeypatch.setattr("app.services.sqlite_resilience.time.sleep", backoff)
    @retry_sqlite
    def read():
        with store_service._connect() as conn:
            fault.hit()
            return conn.execute("SELECT 42").fetchone()[0]
    @retry_sqlite
    def transaction():
        with store_service._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return read()
    assert transaction() == 42
    assert len(delays) == 2


@pytest.mark.parametrize("faults", [False, True])
def test_four_threads_share_no_connections_and_lose_no_events(tmp_path, monkeypatch, faults):
    initialize_store(tmp_path)
    store_service.create_agent_run(run_id="parallel", session_id="parallel")
    if faults:
        inject(monkeypatch, Fault(2, stage="after_insert"))
    barrier = threading.Barrier(4)
    def write(worker):
        barrier.wait(timeout=5)
        try:
            with store_service._connect(independent=True) as first:
                connection_id = id(first)
            barrier.wait(timeout=5)
            for index in range(100):
                store_service.append_agent_run_event(run_id="parallel", event_type="content_delta", payload={"worker": worker, "index": index})
            with store_service._connect(independent=True) as again:
                if not faults:
                    assert id(again) == connection_id
                assert not again.in_transaction
            return connection_id
        finally:
            store_service.close_thread_connection()
    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(write, range(4)))
    assert len(set(ids)) == 4
    rows = store_service.list_agent_run_events("parallel")
    assert [row["seq"] for row in rows] == list(range(1, 401))
    assert len({(row["payload"]["worker"], row["payload"]["index"]) for row in rows}) == 400


def test_cross_chat_read_recovers_without_restarting_worker(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    original = store_service._new_connection
    fault = Fault()
    class Connection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if "SELECT * FROM cross_chat_deliveries" in sql:
                fault.hit()
            return super().execute(sql, parameters)
    def connect():
        conn = sqlite3.connect(store_service._db_path, factory=Connection)
        conn.row_factory = sqlite3.Row
        return conn
    store_service.close_thread_connection()
    monkeypatch.setattr(store_service, "_new_connection", connect)
    monkeypatch.setattr("app.services.sqlite_resilience.time.sleep", lambda delay: None)
    assert cross_chat_store.due() == []
    assert fault.attempts == 3
    store_service.close_thread_connection()
    monkeypatch.setattr(store_service, "_new_connection", original)


@pytest.mark.parametrize("kind", ["connect", "fd", "timeout", "connect_timeout"])
@pytest.mark.parametrize("partial", [False, True])
def test_nested_transport_group_resumes_without_repeating_output_or_tools(monkeypatch, tmp_path, kind, partial):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    original_sleep = asyncio.sleep
    async def fast_sleep(delay):
        await original_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    exc = {"connect": httpx.ConnectError("temporary"), "fd": RuntimeError("File descriptor 12 is used by transport"),
        "timeout": httpx.ReadTimeout("temporary"), "connect_timeout": httpx.ConnectTimeout("temporary")}[kind]
    group = ExceptionGroup("outer", [ExceptionGroup("inner", [exc])])
    frames = wire(chat_frame("prefix")) if partial else FakeResponse()
    if partial:
        frames = wire(chat_frame("prefix", finish=None))
    frames._line_error = group
    FakeAsyncClient.stream_responses = [frames,
        wire(chat_frame(calls=[("only-call", "conversation_read", {})], finish="tool_calls")),
        wire(chat_frame("suffix"))]
    events = asyncio.run(collect_stream(build_session(f"transport_{kind}_{partial}"), "task"))
    assert events[-1]["payload"]["subtype"] == "success"
    assert sum(event["type"] == "stream_retry" for event in events) == 1
    assert [event["payload"]["delta"] for event in events if event["type"] == "content_delta"] == (["prefix", "suffix"] if partial else ["suffix"])
    assert [event["payload"]["id"] for event in events if event["type"] == "tool_finished"] == ["only-call"]


def test_mixed_exception_groups_and_cancel_are_not_retried():
    connect = httpx.ConnectError("temporary")
    assert retryable_model_error(ExceptionGroup("nested", [connect]))
    assert not retryable_stream_error(ExceptionGroup("mixed", [connect, ValueError("bug")]))
    assert not retryable_stream_error(RuntimeError("other programmer error"))
    assert not retryable_model_error(BaseExceptionGroup("cancel", [connect, asyncio.CancelledError()]))


def test_completed_response_is_not_replayed_when_http_cleanup_raises_group(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    first = wire(chat_frame(calls=[("once", "conversation_read", {})], finish="tool_calls"))
    first.cleanup_error = ExceptionGroup("cleanup", [RuntimeError("File descriptor 12 is used by transport")])
    async def exit_stream(self, *args):
        if hasattr(self._response, "cleanup_error"):
            raise self._response.cleanup_error
        return False
    monkeypatch.setattr(FakeStreamContext, "__aexit__", exit_stream)
    FakeAsyncClient.stream_responses = [first, wire(chat_frame("done"))]
    events = asyncio.run(collect_stream(build_session("http_tail_group"), "task"))
    assert events[-1]["payload"]["subtype"] == "success"
    assert not any(event["type"] == "stream_retry" for event in events)
    assert [event["payload"]["id"] for event in events if event["type"] == "tool_finished"] == ["once"]


@pytest.mark.parametrize("kind", ["exhausted", "mixed", "cancelled"])
def test_transport_group_terminal_paths_preserve_existing_status(monkeypatch, tmp_path, kind):
    initialize_store(tmp_path)
    setup_client(monkeypatch, make_runtime_config())
    monkeypatch.setattr(settings, "agent_model_retry_attempts", 3)
    original_sleep = asyncio.sleep
    async def fast_sleep(delay):
        await original_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    exc = httpx.ConnectError("temporary")
    if kind == "mixed":
        group = ExceptionGroup("mixed", [exc, ValueError("programmer error")])
    elif kind == "cancelled":
        group = BaseExceptionGroup("cancelled", [exc, asyncio.CancelledError()])
    else:
        group = ExceptionGroup("transport", [exc])
    responses = [FakeResponse() for _ in range(4 if kind == "exhausted" else 1)]
    for response in responses:
        response._line_error = group
    FakeAsyncClient.stream_responses = responses
    service = AgentRunService()
    session = build_session("group_terminal_" + kind)
    async def run():
        run_id = await service.start_chat_run(session, "task")
        await service.wait_for_run(run_id)
        return run_id
    run_id = asyncio.run(run())
    assert store_service.get_agent_run(run_id)["status"] == ("cancelled" if kind == "cancelled" else "failed")
    events = store_service.list_agent_run_events(run_id)
    assert sum(event["type"] == "stream_retry" for event in events) == (3 if kind == "exhausted" else 0)
    errors = [event for event in events if event["type"] == "error"]
    assert len(errors) == int(kind != "cancelled")
    if errors:
        assert errors[0]["payload"]["error_code"] == "RUN_FAILED"


def test_cancel_waits_for_commit_and_reuses_a_clean_connection(monkeypatch, tmp_path):
    initialize_store(tmp_path)
    store_service.create_agent_run(run_id="cancel-commit", session_id="cancel-commit")
    started, release = threading.Event(), threading.Event()
    original = store_service._new_connection
    seen = []
    class Connection(sqlite3.Connection):
        def commit(self):
            if self.in_transaction:
                started.set()
                assert release.wait(timeout=10)
            return super().commit()
    def connect():
        conn = sqlite3.connect(store_service._db_path, factory=Connection)
        conn.row_factory = sqlite3.Row
        seen.append(conn)
        return conn
    monkeypatch.setattr(store_service, "_new_connection", connect)
    service = AgentRunService()
    async def run():
        task = asyncio.create_task(service.publish_event("cancel-commit", make_event(AgentSession("cancel-commit"), "content_delta", delta="once")))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return await durable_io(store_service.get_agent_run, "cancel-commit")
    row = asyncio.run(run())
    assert row["event_cursor"] == 1
    store_service.close_thread_connection()
    monkeypatch.setattr(store_service, "_new_connection", original)
    assert len(store_service.list_agent_run_events("cancel-commit")) == 1


def test_completed_and_cancelled_pty_cleanup_does_not_close_reused_fd():
    fd = os.open(os.devnull, os.O_RDONLY)
    pipe = os.fdopen(fd, "rb", buffering=0)
    calls = []
    class Transport:
        def close(self):
            calls.append("close")
            pipe.close()
    process = SimpleNamespace(_pty_transport=Transport(), returncode=0)
    workspace_runtime_service._close_exec_pty(process)
    replacement = os.open(os.devnull, os.O_RDONLY)
    try:
        workspace_runtime_service._close_exec_pty(process)
        asyncio.run(workspace_runtime_service._terminate_process(process))
        os.fstat(replacement)
        assert calls == ["close"]
    finally:
        os.close(replacement)


@pytest.mark.skipif(os.name == "nt", reason="requires Linux PTY and selector transports")
def test_real_linux_pty_command_and_cancellation_release_transport(monkeypatch):
    import sys
    create_process = asyncio.create_subprocess_exec
    monkeypatch.setattr(workspace_runtime_service, "_require_docker_binary", lambda: "unused")
    monkeypatch.setattr(workspace_runtime_service, "_extract_workspace_id_from_container_name", lambda _: None)
    monkeypatch.setattr(workspace_runtime_service, "_build_exec_args", lambda *args: ["unused"])
    monkeypatch.setattr("app.services.workspace_runtime_service.platform_sandbox_proxy_service.resolve_for_platform", lambda _: None)
    async def run():
        for cancel in (False, True):
            async def create(*args, **kwargs):
                return await create_process(sys.executable, "-c",
                    "import time; print('ready', flush=True); time.sleep(60)" if cancel else "print('ready')", **kwargs)
            monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
            process = await workspace_runtime_service._create_exec_process("infra-test", "bash", "test")
            transport = process._pty_transport
            task = asyncio.create_task(workspace_runtime_service._collect_stream_output(process, output_callback=None))
            if cancel:
                await asyncio.sleep(0.05)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await workspace_runtime_service._terminate_process(process)
            else:
                output, _ = await task
                assert b"ready" in output
            await asyncio.sleep(0)
            assert transport.is_closing() and process._pty_transport is None
            assert process.returncode is not None
    asyncio.run(run())
