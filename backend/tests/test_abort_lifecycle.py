from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from app.api.routes import agent
from app.api.deps import get_auth_context
from app.services.agent_run_service import AgentRunService
from app.services.runtime_state import runtime_state_service
from app.services.session_service import session_service
from app.services.store import store_service
from test_agent_engine import initialize_store


@pytest.mark.parametrize("partial", [False, True])
def test_abort_interrupts_model_wait_settles_workboard_and_allows_next_message(tmp_path, monkeypatch, partial):
    initialize_store(tmp_path)
    session = session_service.get_or_create(f"abort-model-{partial}")
    runtime_state_service.update_workboard(session, {"items": [
        {"id": "done", "title": "读取配置", "status": "completed"},
        {"id": "active", "title": "修正配置", "status": "in_progress"},
        {"id": "todo", "title": "测评", "status": "pending"},
    ]})

    async def scenario():
        entered, closed = asyncio.Event(), asyncio.Event()
        async def model(*args, **kwargs):
            messages = args[1]
            if next(m["content"] for m in reversed(messages) if m["role"] == "user") == "继续":
                yield {"choices": [{"delta": {"content": "可以继续"}, "finish_reason": "stop"}]}
                return
            try:
                if partial:
                    yield {"choices": [{"delta": {"content": "已开始分析"}, "finish_reason": None}]}
                entered.set()
                await asyncio.Future()
            finally:
                closed.set()
        monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", model)
        monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
        service = AgentRunService()
        run_id = await service.start_chat_run(session, "执行任务清单")
        queue = await service.subscribe(run_id)
        await asyncio.wait_for(entered.wait(), 5)
        # 连续点击停止不能打断同一运行的清理过程。
        first, second = await asyncio.wait_for(asyncio.gather(service.abort_session(session), service.abort_session(session)), 2)
        assert first["status"] == second["status"] == "stopped"
        assert closed.is_set()
        assert store_service.get_agent_run(run_id)["status"] == "cancelled"
        assert service.get_session_run_id(session.session_id) is None
        assert session.active_run is None and session.active_run_view is None
        assert {item["id"]: item["status"] for item in first["workboard"]["items"]} == {
            "done": "completed", "active": "cancelled", "todo": "pending",
        }
        events = []
        while (event := await asyncio.wait_for(queue.get(), 1)) is not None:
            events.append(event)
        kinds = [event.type for event in events]
        assert kinds.count("completed") == 1
        assert kinds.index("workboard_updated") < kinds.index("completed")
        assert events[-1].payload["subtype"] == "aborted"
        if partial:
            assert any(m.get("content") == "已开始分析" for m in session.messages if m.get("role") == "assistant")
        next_run = await service.start_chat_run(session, "继续")
        await asyncio.wait_for(service.wait_for_run(next_run), 5)
        assert store_service.get_agent_run(next_run)["status"] == "completed"
    asyncio.run(scenario())


def test_abort_endpoint_clears_blocking_question_and_waiting_run(tmp_path, monkeypatch):
    initialize_store(tmp_path)
    session = session_service.get_or_create("abort-waiting-question")
    runtime_state_service.request_user_input(session, {"title": "确认", "blocking": True, "questions": [
        {"id": "q", "header": "确认", "question": "是否继续？"},
    ]})
    store_service.create_agent_run(run_id="run-waiting", session_id=session.session_id,
                                   conversation_id=None, status="waiting_input")
    monkeypatch.setattr(agent, "_ensure_session_access", lambda *args: session)
    monkeypatch.setattr(agent, "agent_run_service", AgentRunService())
    app = FastAPI()
    app.include_router(agent.router)
    app.dependency_overrides[get_auth_context] = lambda: None
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/v1/agent/{session.session_id}/abort")
            assert response.status_code == 200, response.text
            assert response.json()["elicitation"]["pending"] is None
            assert store_service.get_active_agent_run_for_session(session.session_id) is None
            assert store_service.get_agent_run("run-waiting")["status"] == "cancelled"
            assert response.json()["elicitation"]["history"][-1]["status"] == "cancelled"
    asyncio.run(scenario())


def test_abort_during_catalog_load_does_not_report_success_or_leak_run(tmp_path, monkeypatch):
    initialize_store(tmp_path)
    session = session_service.get_or_create("abort-catalog-load")
    session.session_mcp = [{"id": "test"}]
    async def scenario():
        entered = asyncio.Event()
        async def stalled_catalog(*args):
            entered.set()
            await asyncio.Future()
        monkeypatch.setattr("app.services.mcp_runtime_service.mcp_runtime_service.ensure_catalog", stalled_catalog)
        service = AgentRunService()
        service._ABORT_GRACE_SECONDS = .05
        run_id = await service.start_chat_run(session, "执行")
        await asyncio.wait_for(entered.wait(), 5)
        result = await asyncio.wait_for(service.abort_session(session), 1)
        assert result["status"] == "stopped"
        assert store_service.get_agent_run(run_id)["status"] == "cancelled"
        assert service.get_session_run_id(session.session_id) is None
        assert session.active_run is None
    asyncio.run(scenario())


def test_abort_does_not_clear_an_unowned_live_run(tmp_path):
    initialize_store(tmp_path)
    session = session_service.get_or_create("abort-other-process")
    runtime_state_service.update_workboard(session, {"items": [{"id": "active", "title": "执行中", "status": "in_progress"}]})
    store_service.create_agent_run(run_id="run-other-process", session_id=session.session_id, conversation_id=None, status="running")
    async def scenario():
        with pytest.raises(RuntimeError, match="不在此进程"):
            await AgentRunService().abort_session(session)
        assert store_service.get_agent_run("run-other-process")["status"] == "running"
        assert runtime_state_service.get_workboard(session).items[0].status == "in_progress"
    asyncio.run(scenario())


def test_abort_during_host_tool_reclaims_waiters_and_allows_next_message(tmp_path, monkeypatch):
    initialize_store(tmp_path)
    session = session_service.get_or_create("abort-host-callback")

    async def scenario():
        entered, cleaned = asyncio.Event(), asyncio.Event()
        initial_tasks = asyncio.all_tasks()

        async def model(*args, **kwargs):
            if next(m["content"] for m in reversed(args[1]) if m["role"] == "user") == "继续":
                yield {"choices": [{"delta": {"content": "可以继续"}, "finish_reason": "stop"}]}
            else:
                yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-host",
                    "function": {"name": "host_pending", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}

        async def execute(session, tool_name, arguments):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaned.set()

        monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", model)
        monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
        monkeypatch.setattr("app.runtime.engine.tool_service.execute", execute)
        service = AgentRunService()
        await service.start_chat_run(session, "执行")
        await asyncio.wait_for(entered.wait(), 5)
        assert (await service.abort_session(session))["status"] == "stopped"
        await asyncio.wait_for(cleaned.wait(), 1)
        await asyncio.sleep(.01)
        assert not (asyncio.all_tasks() - initial_tasks), "停止后不得遗留输出队列或进度等待任务"
        run_id = await service.start_chat_run(session, "继续")
        await asyncio.wait_for(service.wait_for_run(run_id), 5)
        assert store_service.get_agent_run(run_id)["status"] == "completed"
    asyncio.run(scenario())


def test_abort_keeps_database_lock_until_runtime_state_cleanup_finishes(tmp_path, monkeypatch):
    initialize_store(tmp_path)
    session = session_service.get_or_create("abort-cleanup-lock")

    async def scenario():
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def model(*args, **kwargs):
            entered.set()
            await asyncio.Future()
            yield {}
        monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", model)
        monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
        service = AgentRunService()
        publish = service._publish
        async def paused_publish(run_id, event):
            if event.type == "workboard_updated":
                cleaning.set()
                await release.wait()
            await publish(run_id, event)
        monkeypatch.setattr(service, "_publish", paused_publish)
        run_id = await service.start_chat_run(session, "执行")
        await asyncio.wait_for(entered.wait(), 5)
        stopping = asyncio.create_task(service.abort_session(session))
        try:
            await asyncio.wait_for(cleaning.wait(), 5)
            assert store_service.get_active_agent_run_for_session(session.session_id)["run_id"] == run_id
            with pytest.raises(RuntimeError, match="已有执行中的任务"):
                await AgentRunService().start_chat_run(session, "继续")
        finally:
            release.set()
            await asyncio.wait_for(stopping, 5)
        assert store_service.get_active_agent_run_for_session(session.session_id) is None
    asyncio.run(scenario())


def test_abort_reports_runtime_state_cleanup_failure(tmp_path, monkeypatch):
    initialize_store(tmp_path)
    session = session_service.get_or_create("abort-cleanup-failure")

    async def scenario():
        entered = asyncio.Event()
        async def model(*args, **kwargs):
            entered.set()
            await asyncio.Future()
            yield {}
        monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", model)
        monkeypatch.setattr("app.runtime.engine.tool_service.list_tool_schemas", lambda session: [])
        def fail_settle(session):
            raise RuntimeError("storage failure")
        monkeypatch.setattr(runtime_state_service, "settle_aborted_run", fail_settle)
        service = AgentRunService()
        run_id = await service.start_chat_run(session, "执行")
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(RuntimeError, match="收尾失败"):
            await service.abort_session(session)
        assert store_service.get_agent_run(run_id)["status"] == "failed"
        assert service.get_session_run_id(session.session_id) is None
    asyncio.run(scenario())
