from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from app.api import lifecycle as lifecycle_api
from app.services.lifecycle_service import LifecycleService, ServiceDraining


def test_drain_tracks_children_and_finalizers_and_rejects_new_work():
    service = LifecycleService()

    async def exercise():
        continue_parent = asyncio.Event()
        finish_child = asyncio.Event()
        child_started = asyncio.Event()

        async def child():
            try:
                child_started.set()
            finally:
                await finish_child.wait()

        async def parent():
            await continue_parent.wait()
            # 排空期间，已接纳任务仍可派生后续步骤；父任务结束不代表子任务已收尾。
            service.create_task(child(), label="children")

        parent_task = service.create_task(parent(), label="runs")
        assert service.begin_drain("operator")["active"] == {"runs": 1}
        with pytest.raises(ServiceDraining):
            with service.operation("new_request"):
                pytest.fail("new work must not start")
        continue_parent.set()
        await parent_task
        await child_started.wait()
        await asyncio.sleep(0)
        assert service.snapshot()["active"] == {"children": 1}
        assert not service.snapshot()["drained"]
        finish_child.set()
        for _ in range(3):
            await asyncio.sleep(0)
        assert service.snapshot()["drained"]

    asyncio.run(exercise())


def test_drain_lease_expires_and_different_operator_cannot_cancel(monkeypatch):
    service = LifecycleService()
    now = [100.0]
    monkeypatch.setattr("app.services.lifecycle_service.time.monotonic", lambda: now[0])
    service.begin_drain("first")
    with pytest.raises(ValueError):
        service.begin_drain("second")
    with pytest.raises(ValueError):
        service.cancel_drain("second")
    now[0] += service.LEASE_SECONDS + 1
    with service.operation("new_request"):
        assert not service.draining


def test_control_file_identifies_instance_and_does_not_remove_replacement(tmp_path, monkeypatch):
    path = tmp_path / "backend.control.json"
    monkeypatch.setenv("AETHERCORE_CONTROL_FILE", str(path))
    service = LifecycleService()
    service.start()
    control = json.loads(path.read_text())
    assert control["instance_id"] == service.instance_id
    assert control["token"] == service.token
    replacement = LifecycleService()
    replacement.start()
    service.stop()
    assert json.loads(path.read_text())["instance_id"] == replacement.instance_id
    replacement.stop()
    assert not path.exists()


def test_http_admission_control_auth_and_inflight_cancellation(monkeypatch):
    service = LifecycleService()
    service.ready = True
    monkeypatch.setattr(lifecycle_api, "lifecycle_service", service)
    app = FastAPI()
    app.add_middleware(lifecycle_api.AdmissionMiddleware)
    app.include_router(lifecycle_api.router)
    written = []

    @app.post("/write")
    async def write():
        written.append(True)
        return {"ok": True}

    @app.get("/history")
    async def history():
        return {"history": True}

    @app.post("/api/v1/agent/session/abort")
    async def abort():
        return {"cancelled": True}

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            url = "/api/v1/internal/lifecycle/drain"
            owner = {"owner": "first-operator-12345"}
            headers = {"X-AetherCore-Control": service.token}
            assert (await client.post(url, json=owner)).status_code == 403
            # 在独立 task 中发控制请求，不能继承业务请求的 admission context。
            started = asyncio.Event()
            finish = asyncio.Event()

            async def work():
                with service.operation("existing"):
                    started.set()
                    await finish.wait()

            task = asyncio.create_task(work())
            await started.wait()
            state = (await client.post(url, json=owner, headers=headers)).json()
            assert not state["drained"]
            rejected = await client.post("/write")
            assert rejected.status_code == 503
            assert rejected.json()["code"] == "service_restarting"
            assert rejected.headers["Retry-After"] == "3"
            assert not written
            assert (await client.get("/history")).status_code == 200
            assert (await client.post("/api/v1/agent/session/abort")).status_code == 200
            other = await client.request("DELETE", url, json={"owner": "second-operator-12345"}, headers=headers)
            assert other.status_code == 409
            finish.set()
            await task
            state = (await client.post(url, json=owner, headers=headers)).json()
            assert state["drained"]
            assert (await client.request("DELETE", url, json=owner, headers=headers)).status_code == 200
            assert (await client.post("/write")).status_code == 200

    asyncio.run(exercise())


def test_scheduler_pauses_claiming_but_finishes_admitted_execution(monkeypatch):
    from app.services.scheduling import scheduler as scheduler_module

    service = LifecycleService()
    scheduler = scheduler_module.ScheduleScheduler()
    monkeypatch.setattr(scheduler_module, "lifecycle_service", service)
    claimed = []

    def claim(**kwargs):
        claimed.append(True)
        return {"run_id": "scheduled"}

    monkeypatch.setattr(scheduler_module.store_service, "claim_next_schedule_run", claim)
    monkeypatch.setattr(scheduler_module.store_service, "materialize_due_schedule_tasks",
                        lambda **kwargs: pytest.fail("must not dispatch while draining"))

    async def exercise():
        started = asyncio.Event()
        finish = asyncio.Event()

        async def execute(run):
            started.set()
            await finish.wait()
            with service.operation("followup"):
                assert service.draining

        monkeypatch.setattr(scheduler._executor, "execute", execute)
        scheduler._running = True
        worker = asyncio.create_task(scheduler._worker(0))
        await started.wait()
        service.begin_drain("operator")
        assert service.snapshot()["active"] == {"schedule_runs": 1}
        assert await scheduler.tick_once() == 0
        scheduler._running = False
        finish.set()
        await worker
        assert service.snapshot()["drained"]
        assert len(claimed) == 1

    asyncio.run(exercise())
