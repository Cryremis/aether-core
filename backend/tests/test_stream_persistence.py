from __future__ import annotations

import asyncio
import threading

import pytest

from app.core.config import settings
from app.schemas.agent import AgentEvent
from app.services.agent_run_service import AgentRunService, LiveRunState
from app.services.context.history_store import history_store
from app.services.session_service import SessionService
from app.services.store import store_service
from app.services.tool_execution_service import ToolExecutionService


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(store_service, "_db_path", tmp_path / "stream.db")
    store_service.initialize()
    return SessionService()


def delta(session, text, *, attempt_id=None):
    return AgentEvent(type="content_delta", session_id=session.session_id,
                      attempt_id=attempt_id, payload={"delta": text})


def test_concurrent_event_replay_and_tool_output_do_not_capture_history(sessions, monkeypatch):
    service = AgentRunService()
    executions = ToolExecutionService()
    prepared = []
    for i in range(10):
        session = sessions.get_or_create(f"stream_{i}")
        session.messages = [{"message_id": str(j), "role": "user", "content": "历史" * 2000} for j in range(245)]
        sessions.persist(session)
        run_id = f"run_{i}"
        store_service.create_agent_run(run_id=run_id, session_id=session.session_id, status="running")
        session.active_run_view = {"run_id": run_id, "assistant": {"blocks": []}}
        prepared.append((session, run_id))

    async def exercise():
        tool_sessions = [session for session, _ in prepared[:2]]
        records = [
            await executions.begin_execution(session, tool_call_id="call", tool_name="shell")
            for session in tool_sessions
        ]
        original_capture = history_store.capture
        monkeypatch.setattr(history_store, "capture", lambda *a, **k: pytest.fail("流式增量不能保存整份会话"))

        async def stream(session, run_id):
            for i in range(100):
                await service._publish(run_id, delta(session, str(i)), session=session)
            view = service.get_active_run_view(session)
            assert view["last_seq"] == 100
            assert view["assistant"]["blocks"][0]["content"] == "".join(map(str, range(100)))
            replay = service.list_events_after(run_id, 36)
            assert [event.seq for event in replay] == list(range(37, 101))

        async def output(tool_session):
            for i in range(30):
                fragment = await executions.append_output(tool_session, tool_call_id="call",
                    tool_name="shell", stream="stdout", text="日志\n")
                assert fragment.seq == i + 1

        await asyncio.gather(*(output(session) for session in tool_sessions), *(stream(*item) for item in prepared))
        for session, record in zip(tool_sessions, records):
            assert (session.workspace.root / record.output_path).read_text(encoding="utf-8") == "日志\n" * 30
        monkeypatch.setattr(history_store, "capture", original_capture)
        for session in tool_sessions:
            result = await executions.finish_execution(session, tool_call_id="call", exit_code=0, final_output="done")
            assert result["preview_text"] == "日志\n" * 30

    asyncio.run(exercise())


def test_publish_waits_for_commit_and_cancellation_preserves_sequence(sessions, monkeypatch):
    service = AgentRunService()
    session = sessions.get_or_create("ordered")
    store_service.create_agent_run(run_id="run", session_id=session.session_id, status="running")
    session.active_run_view = {"run_id": "run", "assistant": {"blocks": []}}
    append = store_service.append_agent_run_event

    async def exercise():
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        def paused(**kwargs):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return append(**kwargs)

        monkeypatch.setattr(store_service, "append_agent_run_event", paused)
        state = LiveRunState("run", session.session_id, asyncio.current_task())
        service._runs["run"] = state
        queue = await service.subscribe("run")
        publishing = asyncio.create_task(service._publish("run", delta(session, "首字"), session=session))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert queue.empty()
            assert service.get_active_run_view(session)["assistant"]["blocks"] == []
            assert store_service.get_agent_run("run")["event_cursor"] == 0
            publishing.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await publishing
        assert (await queue.get()).seq == 1
        assert service.get_active_run_view(session)["last_seq"] == 1

        def failed(**kwargs):
            raise OSError("commit failed")

        monkeypatch.setattr(store_service, "append_agent_run_event", failed)
        with pytest.raises(OSError, match="commit failed"):
            await service._publish("run", delta(session, "丢弃"), session=session)
        assert service.get_active_run_view(session)["last_seq"] == 1
        assert queue.empty()
        monkeypatch.setattr(store_service, "append_agent_run_event", append)
        assert await service._publish("run", delta(session, "第二段"), session=session) == 2
        assert (await queue.get()).seq == 2

    asyncio.run(exercise())


def test_checkpoint_reuses_unchanged_versions_and_restores_partial_run(sessions, monkeypatch):
    session = sessions.get_or_create("incremental")
    session.messages = [
        {"message_id": "a", "role": "user", "content": "保留"},
        {"message_id": "b", "role": "assistant", "content": "旧版本"},
    ]
    sessions.persist(session)
    import app.services.context.history_store as history_module
    dumps = history_module.json.dumps
    encoded = []

    def count(value, *args, **kwargs):
        if isinstance(value, dict) and "message_id" in value:
            encoded.append(value["message_id"])
        return dumps(value, *args, **kwargs)

    monkeypatch.setattr(history_module.json, "dumps", count)
    session.messages[1]["content"] = "新版本"
    session.messages.reverse()
    asyncio.run(sessions.persist_async(session))
    assert encoded == ["b"]
    assert [message["content"] for message in history_store.snapshot(session.session_id)["messages"]] == ["新版本", "保留"]
    assert history_store.read(session.session_id, message_id="b")["items"][0]["content"] == "新版本"
    with store_service._connect() as conn:
        assert conn.execute("SELECT count(*) FROM context_records WHERE session_id=?", (session.session_id,)).fetchone()[0] == 3

    store_service.create_agent_run(run_id="interrupted", session_id=session.session_id, status="running")
    history_store.start_attempt(session.session_id, "attempt", {"run_id": "interrupted", "turn_index": 3})
    service = AgentRunService()
    asyncio.run(service._publish("interrupted", delta(session, "已提交的片段", attempt_id="attempt"), session=session))
    store_service.recover_interrupted_agent_runs()
    restored = SessionService().get_or_create(session.session_id)
    assert restored.messages[-1]["content"] == "已提交的片段"
    assert restored.active_run_view is None
    assert [event.seq for event in service.list_events_after("interrupted", 0)] == [1, 2, 3]


def test_final_checkpoint_failure_cannot_publish_success(sessions, monkeypatch):
    from app.runtime.engine import agent_engine
    from app.services.session_service import session_service

    session = sessions.get_or_create("failed_checkpoint")
    service = AgentRunService()
    persist = session_service.persist_async
    writes = 0

    async def persist_until_terminal(current):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("checkpoint unavailable")
        await persist(current)

    async def model(*args, **kwargs):
        yield AgentEvent(type="completed", session_id=session.session_id, payload={"subtype": "success"})

    monkeypatch.setattr(session_service, "persist_async", persist_until_terminal)
    monkeypatch.setattr(agent_engine, "stream_chat", model)

    async def exercise():
        run_id = await service.start_chat_run(session, "test")
        await service.wait_for_run(run_id)
        assert store_service.get_agent_run(run_id)["status"] == "failed"
        assert service.list_events_after(run_id, 0)[-1].payload["subtype"] == "error_cleanup"

    asyncio.run(exercise())
