from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import socket
import textwrap
import threading
import time
from urllib.request import Request, urlopen

import pytest


# 生产脚本不属于后端安装包，直接加载仓库入口，不改变测试进程的 sys.path。
spec = importlib.util.spec_from_file_location("aethercore_process_control", Path(__file__).resolve().parents[2] / "process_control.py")
control = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = control
spec.loader.exec_module(control)


@pytest.fixture
def backend(tmp_path, monkeypatch):
    service = control.ServiceSpec("backend", tmp_path, tmp_path / "backend.pid", tmp_path / "out.log", tmp_path / "err.log", 12345, ["python"])
    monkeypatch.setattr(control, "resolve_service_pid", lambda _: 42)
    monkeypatch.setattr(control, "_read_control", lambda *_: {"pid": 42, "instance_id": "instance", "token": "private"})
    return service


def test_timeout_never_stops_backend_and_releases_maintenance(backend, monkeypatch):
    calls = []

    def request(*args, method="GET", **kwargs):
        calls.append(method)
        return {"ready": True, "drained": False, "active": {"agent_runs": 1}}

    monkeypatch.setattr(control, "_lifecycle_request", request)
    monkeypatch.setattr(control, "stop_service", lambda _: pytest.fail("must not stop active backend"))
    assert not control.restart_service(backend, timeout_seconds=0)
    assert calls == ["POST", "DELETE"]


def test_restart_waits_for_drain_then_stops_and_starts(backend, monkeypatch):
    calls = []

    def request(*args, method="GET", **kwargs):
        calls.append(method)
        return {"ready": True, "drained": calls.count("POST") == 2, "active": {"agent_runs": 1}}

    monkeypatch.setattr(control, "_lifecycle_request", request)
    monkeypatch.setattr(control.time, "sleep", lambda _: None)
    monkeypatch.setattr(control, "stop_service", lambda _: calls.append("stop") or True)
    monkeypatch.setattr(control, "start_service", lambda _: calls.append("start") or True)
    assert control.restart_service(backend)
    assert calls == ["POST", "POST", "stop", "start", "DELETE"]


def test_missing_handshake_fails_closed_but_force_is_explicit(backend, monkeypatch):
    calls = []

    def missing(*args):
        raise FileNotFoundError("control file missing")

    monkeypatch.setattr(control, "_read_control", missing)
    monkeypatch.setattr(control, "stop_service", lambda _: calls.append("stop") or True)
    monkeypatch.setattr(control, "start_service", lambda _: calls.append("start") or True)
    assert not control.restart_service(backend)
    assert calls == []
    assert control.restart_service(backend, force=True)
    assert calls == ["stop", "start"]


def test_failed_stop_does_not_start_second_process(backend, monkeypatch):
    monkeypatch.setattr(control, "stop_service", lambda _: False)
    monkeypatch.setattr(control, "start_service", lambda _: pytest.fail("must not start second backend"))
    assert not control.restart_service(backend, force=True)


def test_caller_exception_releases_drain_and_is_not_swallowed(backend, monkeypatch):
    calls = []

    def request(*args, method="GET", **kwargs):
        calls.append(method)
        return {"ready": True, "drained": True}

    monkeypatch.setattr(control, "_lifecycle_request", request)
    with pytest.raises(OSError, match="stop failed"):
        with control.restart_guard(backend) as ready:
            assert ready
            raise OSError("stop failed")
    assert calls == ["POST", "DELETE"]


def test_real_process_restart_preserves_work_and_checks_new_instance_readiness(tmp_path, monkeypatch):
    """真实 Uvicorn 进程握手、排空、停止与启动；仅使用临时应用、端口和文件。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app_file = tmp_path / "restart_test_app.py"
    app_file.write_text(textwrap.dedent('''
        import asyncio
        from contextlib import asynccontextmanager
        from pathlib import Path
        from fastapi import FastAPI
        from app.api.lifecycle import AdmissionMiddleware, router
        from app.services.lifecycle_service import lifecycle_service

        @asynccontextmanager
        async def lifespan(app):
            lifecycle_service.start()
            try:
                yield
            finally:
                lifecycle_service.stop()

        app = FastAPI(lifespan=lifespan)
        app.add_middleware(AdmissionMiddleware)
        app.include_router(router)

        @app.post('/work')
        async def work():
            async def execute():
                while not Path('release').exists():
                    await asyncio.sleep(0.02)
                await asyncio.sleep(0.05)
                Path('finished').write_text('result persisted')
            lifecycle_service.create_task(execute(), label='agent_runs')
            return {'accepted': True}
    '''), encoding="utf-8")
    backend_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", str(backend_root))
    service = control.ServiceSpec("backend", tmp_path, tmp_path / "backend.pid", tmp_path / "out.log", tmp_path / "err.log", port,
                                  [sys.executable, "-m", "uvicorn", "restart_test_app:app", "--host", "127.0.0.1", "--port", str(port),
                                   "--timeout-graceful-shutdown", "2"])
    release_thread = None
    try:
        assert control.start_service(service)
        original_pid = control.read_pid(service.pid_file)
        original_control = control._read_control(service, original_pid)
        with urlopen(Request(f"http://127.0.0.1:{port}/work", data=b"", method="POST"), timeout=3) as response:
            assert response.status == 200
        assert not control.restart_service(service, timeout_seconds=0)
        assert control.read_pid(service.pid_file) == original_pid
        assert not control._lifecycle_request(service, original_control)["draining"]
        assert not (tmp_path / "finished").exists()

        def release_when_draining():
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if control._lifecycle_request(service, original_control)["draining"]:
                    (tmp_path / "release").touch()
                    return
                time.sleep(0.05)

        release_thread = threading.Thread(target=release_when_draining, daemon=True)
        release_thread.start()
        assert control.restart_service(service, timeout_seconds=15)
        assert (tmp_path / "finished").read_text() == "result persisted"
        new_pid = control.read_pid(service.pid_file)
        new_control = control._read_control(service, new_pid)
        assert new_control["instance_id"] != original_control["instance_id"]
        status = control._lifecycle_request(service, new_control)
        assert status["ready"] and not status["draining"]
    finally:
        if release_thread is not None:
            release_thread.join(timeout=1)
        control.stop_service(service)
