from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.api.deps import AuthContext, get_auth_context
from app.core.config import settings
from app.main import app
from app.services.agent_run_service import agent_run_service
from app.services.context.message_adapter import context_message_adapter
from app.services.cross_chat_service import APPROVE, cross_chat_service
from app.services.cross_chat_store import cross_chat_store
from app.services.runtime_state import runtime_state_service
from app.services.session_service import session_service
from app.services.store import store_service
from app.services.tool_result_service import tool_result_renderer
from app.services.tool_service import tool_service


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(settings, "storage_root", storage)
    monkeypatch.setattr(store_service, "_db_path", storage / "test.db")
    monkeypatch.setattr(agent_run_service, "_lock", asyncio.Lock())
    monkeypatch.setattr(agent_run_service, "_runs", {})
    monkeypatch.setattr(agent_run_service, "_session_runs", {})
    monkeypatch.setattr(session_service, "_sessions", {})
    store_service.initialize()

    def create(name, *, owner=17, platform=None, external=None, org=None, visibility="normal"):
        conversation = store_service.create_conversation(
            session_id=f"sess_{name}", title=name, host_name="test", owner_user_id=owner,
            platform_id=platform, external_user_id=external, external_org_id=org, visibility=visibility,
        )
        session = session_service.get_or_create(conversation["session_id"])
        session_service.persist(session)
        return conversation, session

    app.dependency_overrides[get_auth_context] = lambda: AuthContext(kind="user", user=SimpleNamespace(user_id=17))
    try:
        yield create
    finally:
        app.dependency_overrides.pop(get_auth_context, None)


def test_cross_chat_reads_only_visible_peer_text_and_paginates(runtime):
    _, source = runtime("source")
    target, session = runtime("peer")
    other, _ = runtime("peer_other_user", owner=18)
    hidden, _ = runtime("peer_hidden", visibility="hidden")
    runtime("peer_second")
    user = context_message_adapter.make_user_message("a" * 1190 + "\n多行正文\n" + "b" * 1004, turn_index=1)
    session.messages.extend([
        context_message_adapter.make_internal_user_message("private runtime data", turn_index=1),
        user,
        context_message_adapter.make_assistant_message(
            content="public answer", turn_index=1, reasoning_content="private reasoning",
            blocks=[{"kind": "content", "id": "b1", "content": "public answer"},
                    {"kind": "tool", "id": "b2", "outputText": "private tool data"}],
        ),
    ])
    session_service.persist(session)

    async def exercise():
        first = await tool_service.execute(source, "chat_list", {"query": "peer", "limit": 1})
        second = await tool_service.execute(source, "chat_list", {"query": "peer", "cursor": first.data["next_cursor"]})
        assert {item["title"] for item in first.data["items"] + second.data["items"]} == {"peer", "peer_second"}
        for inaccessible in (other, hidden):
            rejected = await tool_service.execute(source, "chat_read", {"conversation_id": inaccessible["conversation_id"]})
            assert rejected.error.code == "CROSS_CHAT_DENIED"
        result = await tool_service.execute(source, "chat_read", {"conversation_id": target["conversation_id"], "limit": 1})
        assert result.data["items"][0]["text"] == "public answer"
        earlier = await tool_service.execute(source, "chat_read", {
            "conversation_id": target["conversation_id"], "before": result.data["next_before"], "limit": 1,
        })
        assert len(earlier.data["items"][0]["text"]) == 1200
        fragment = earlier.data["items"][0]["next_fragment"]
        rest = await tool_service.execute(source, "chat_read", {"conversation_id": target["conversation_id"], "fragment": fragment})
        assert "多行正文" in tool_result_renderer.render(earlier)
        assert "\n多行正文\n" in tool_result_renderer.render(earlier)
        assert rest.data["items"][0]["text"] == "b" * 1000
        assert "private" not in str([result.data, earlier.data, rest.data])

    asyncio.run(exercise())


def test_host_chats_are_scoped_to_platform_and_external_identity(runtime):
    def platform(key):
        admin = store_service.get_user_by_username(settings.auth_system_admin_username)
        return store_service.create_platform(platform_key=key, display_name=key, host_type="embedded",
                                             description="", owner_user_id=admin.user_id)["platform_id"]

    host, other = platform("host-one"), platform("host-two")
    _, source = runtime("host_source", owner=None, platform=host, external="u", org="org-1")
    expected, _ = runtime("same_user", owner=None, platform=host, external="u", org="org-1")
    runtime("other_host", owner=None, platform=other, external="u", org="org-1")
    runtime("other_member", owner=None, platform=host, external="v", org="org-1")
    _, other_org = runtime("other_org", owner=None, platform=host, external="u", org="org-2")
    _, orgless = runtime("orgless", owner=None, platform=host, external="u")

    async def exercise():
        result = await tool_service.execute(source, "chat_list", {})
        assert [item["conversation_id"] for item in result.data["items"]] == [expected["conversation_id"]]
        # 旧 token 未声明组织，仍只能按签发时绑定的会话归属访问。
        app.dependency_overrides[get_auth_context] = lambda: AuthContext(
            kind="embed", platform_id=host, external_user_id="u", conversation_id=source.conversation_id,
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            listed = await client.get("/api/v1/agent/sessions")
            assert listed.status_code == 200, listed.text
            assert {item["session_id"] for item in listed.json()["data"]} == {source.session_id, expected["session_id"]}
            for foreign in (other_org, orgless):
                assert (await client.get(f"/api/v1/agent/{foreign.session_id}/cross-chat/deliveries")).status_code == 403
                assert (await client.post("/api/v1/agent/chat", json={"session_id": foreign.session_id, "message": "test"})).status_code == 403

    asyncio.run(exercise())


async def draft(source, target, mode="queue", expected=None, message="请继续处理这条跨会话任务"):
    source_run_id = f"run_{source.session_id}"
    store_service.create_agent_run(run_id=source_run_id, session_id=source.session_id,
                                   conversation_id=source.conversation_id, status="running")
    source.begin_run(source_run_id)
    args = {"conversation_id": target["conversation_id"], "message": message, "mode": mode}
    if expected is not None:
        args["expected_run_id"] = expected
    result = await tool_service.execute(source, "chat_send", args)
    assert result.status == "success", result
    assert message in result.runtime_events[0]["payload"]["request"]["questions"][0]["question"]
    delivery_id = result.data["delivery_id"]
    source.finish_run(source_run_id)
    store_service.append_agent_run_event(
        run_id=source_run_id, event_type="completed", status="waiting_input",
        payload={"subtype": "awaiting_user_input", "request_id": delivery_id},
    )
    return source_run_id, delivery_id


async def answer(client, source, delivery_id, choice=APPROVE):
    return await client.post(
        f"/api/v1/agent/{source.session_id}/elicitation/{delivery_id}/respond",
        json={"responses": [{"question_id": "delivery", "selected_options": [choice]}]},
    )


def test_background_worker_never_treats_a_workspace_snapshot_as_authorization(runtime):
    _, source = runtime("untrusted_snapshot")
    target, receiver = runtime("safe_target")

    async def exercise():
        run_id, delivery_id = await draft(source, target)
        snapshot, _ = runtime_state_service.resolve_elicitation(source, delivery_id, [
            {"question_id": "delivery", "selected_options": [APPROVE]},
        ])
        snapshot.history[-1].resume_run_id = run_id
        runtime_state_service.save_elicitation(source, snapshot)
        await cross_chat_service.process(cross_chat_store.get(delivery_id))
        assert cross_chat_store.get(delivery_id)["status"] == "awaiting_approval"
        assert store_service.get_active_agent_run_for_session(receiver.session_id) is None

    asyncio.run(exercise())


@pytest.mark.parametrize("approved", [False, True])
def test_http_approval_queue_and_restart_idempotency(runtime, monkeypatch, approved):
    _, source = runtime("sender")
    target, receiver = runtime("receiver")
    model_inputs = []

    async def model(config, messages, tools, **kwargs):
        model_inputs.append(messages)
        yield {"choices": [{"delta": {"content": "收到任务"}, "finish_reason": None}]}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", model)

    async def exercise():
        original = "run_receiver_busy"
        _, delivery_id = await draft(source, target)
        store_service.create_agent_run(run_id=original, session_id=receiver.session_id,
                                       conversation_id=receiver.conversation_id, status="running",
                                       idempotency_key=f"cross-chat:{delivery_id}")
        await cross_chat_service.process(cross_chat_store.get(delivery_id))
        assert cross_chat_store.get(delivery_id)["status"] == "awaiting_approval"
        assert not model_inputs
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            if approved:
                response, duplicate = await asyncio.gather(
                    answer(client, source, delivery_id), answer(client, source, delivery_id),
                )
                assert duplicate.status_code == 200, duplicate.text
            else:
                response = await answer(client, source, delivery_id, "不发送")
                assert (await answer(client, source, delivery_id)).status_code == 200
            assert response.status_code == 200, response.text
            if not approved:
                assert cross_chat_store.get(delivery_id)["status"] == "declined"
                assert len(model_inputs) == 1
                return
            queued = cross_chat_store.get(delivery_id)
            assert queued["status"] == "queued"
            await cross_chat_service.process(queued)
            assert cross_chat_store.get(delivery_id)["status"] == "queued"
            source_list = await client.get(f"/api/v1/agent/{source.session_id}/cross-chat/deliveries")
            target_list = await client.get(f"/api/v1/agent/{receiver.session_id}/cross-chat/deliveries")
            assert source_list.json()["data"]["items"][0]["status"] == "queued"
            assert not target_list.json()["data"]["items"]
            denied = await client.post(f"/api/v1/agent/{receiver.session_id}/cross-chat/deliveries/{delivery_id}/cancel")
            assert denied.status_code == 404
            store_service.append_agent_run_event(
                run_id=original, event_type="completed", status="completed", payload={"subtype": "success"},
            )
            await cross_chat_service.start()
            try:
                async with asyncio.timeout(6):
                    while cross_chat_store.get(delivery_id)["status"] == "queued":
                        await asyncio.sleep(0.05)
                    while cross_chat_store.get(delivery_id)["status"] == "dispatching":
                        await asyncio.sleep(0.05)
            finally:
                await cross_chat_service.stop()
            delivered = cross_chat_store.get(delivery_id)
            run_id = delivered["target_run_id"]
            assert delivered["status"] == "delivered" and run_id != original
            await agent_run_service.wait_for_run(run_id)
            # 模拟目标 run 已创建、投递回执更新之前进程停止。
            cross_chat_store.transition(delivery_id, "delivered", "dispatching")
            await cross_chat_service.process(cross_chat_store.get(delivery_id))
            assert cross_chat_store.get(delivery_id)["target_run_id"] == run_id
            assert sum("用户确认的跨会话消息" in str(messages) for messages in model_inputs) == 1
            status = await tool_service.execute(source, "chat_wait", {"delivery_id": delivery_id, "wait_seconds": 1})
            assert status.data["run_status"] == "completed" and status.data["reply_preview"] == "收到任务"
            inbox = await client.get(f"/api/v1/agent/{receiver.session_id}/cross-chat/inbox")
            assert inbox.json()["data"]["cursor"] == delivery_id
            received = await client.get(f"/api/v1/agent/{receiver.session_id}/cross-chat/deliveries")
            assert received.json()["data"]["items"][0]["direction"] == "received"

    asyncio.run(exercise())


def test_steer_is_consumed_by_the_original_running_agent(runtime, monkeypatch):
    _, source = runtime("steering_sender")
    target, receiver = runtime("steering_receiver")

    async def exercise():
        started = asyncio.Event()
        release = asyncio.Event()
        target_calls = []

        async def model(config, messages, tools, **kwargs):
            serialized = str(messages)
            if "原始目标任务" in serialized:
                target_calls.append(serialized)
                if len(target_calls) == 1:
                    started.set()
                    await release.wait()
            yield {"choices": [{"delta": {"content": "处理完成"}, "finish_reason": "stop"}]}

        monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", model)
        run_id = await agent_run_service.start_chat_run(receiver, "原始目标任务", emit_started=True)
        await asyncio.wait_for(started.wait(), timeout=5)
        _, delivery_id = await draft(source, target, "steer", run_id, "现在优先检查最终输出")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await answer(client, source, delivery_id)
            assert response.status_code == 200, response.text
        release.set()
        await asyncio.wait_for(agent_run_service.wait_for_run(run_id), timeout=5)
        receipt = cross_chat_store.get(delivery_id)
        assert receipt["status"] == "delivered" and receipt["target_run_id"] == run_id
        assert len(target_calls) == 2 and "现在优先检查最终输出" in target_calls[1]
        history = store_service.list_agent_run_events(run_id)
        assert len([event for event in history if event["type"] == "completed"]) == 1
        assert any(event["type"] == "message_committed" and event["payload"]["message"]["id"] == f"msg_{delivery_id}"
                   for event in history)

    asyncio.run(exercise())


def test_cancelled_queue_and_stale_steer_cannot_reach_another_run(runtime, monkeypatch):
    _, source = runtime("cancel_sender")
    _, steering_source = runtime("stale_sender")
    target, receiver = runtime("cancel_target")

    async def model(*args, **kwargs):
        yield {"choices": [{"delta": {"content": "确认已处理"}, "finish_reason": "stop"}]}

    monkeypatch.setattr("app.runtime.engine.llm_client.stream_chat_completion", model)

    async def exercise():
        original = "run_old"
        store_service.create_agent_run(run_id=original, session_id=receiver.session_id,
                                       conversation_id=receiver.conversation_id, status="running")
        _, queued_id = await draft(source, target)
        _, steer_id = await draft(steering_source, target, "steer", original)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await answer(client, source, queued_id)).status_code == 200
            assert (await answer(client, steering_source, steer_id)).status_code == 200
            path = f"/api/v1/agent/{source.session_id}/cross-chat/deliveries/{queued_id}/cancel"
            assert (await client.post(path)).status_code == 200
            assert (await client.post(path)).status_code == 409
        assert cross_chat_store.get(queued_id)["status"] == "cancelled"
        store_service.append_agent_run_event(
            run_id=original, event_type="completed", status="completed", payload={"subtype": "success"},
        )
        current = "run_current"
        store_service.create_agent_run(run_id=current, session_id=receiver.session_id,
                                       conversation_id=receiver.conversation_id, status="running")
        await cross_chat_service.process(cross_chat_store.get(steer_id))
        assert cross_chat_store.get(steer_id)["status"] == "failed"
        assert cross_chat_store.get(steer_id)["target_run_id"] is None
        assert cross_chat_store.steering(receiver.conversation_id, current) == []
        assert store_service.get_active_agent_run_for_session(receiver.session_id)["run_id"] == current

    asyncio.run(exercise())
