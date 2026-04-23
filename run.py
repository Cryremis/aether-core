from __future__ import annotations

import argparse
import sys

from process_control import (
    BACKEND_ROOT,
    FRONTEND_DIST_ROOT,
    LOG_ROOT,
    PID_ROOT,
    ServiceSpec,
    check_health,
    ensure_runtime_dirs,
    follow_log_files,
    load_runtime_settings,
    port_is_open,
    resolve_service_pid,
    start_service,
    stop_service,
)

def backend_service() -> ServiceSpec:
    settings = load_runtime_settings()
    return ServiceSpec(
        name="backend",
        workdir=BACKEND_ROOT,
        pid_file=PID_ROOT / "backend.pid",
        stdout_log=LOG_ROOT / "backend.out.log",
        stderr_log=LOG_ROOT / "backend.err.log",
        port=settings.backend_port,
        command=[
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            settings.backend_host,
            "--port",
            str(settings.backend_port),
        ],
    )


def frontend_dist_ready() -> bool:
    return (FRONTEND_DIST_ROOT / "index.html").exists()


def show_status() -> None:
    settings = load_runtime_settings()
    backend = backend_service()
    backend_pid = resolve_service_pid(backend)
    print(f"backend: {'running' if port_is_open(settings.backend_port) else 'stopped'}")
    print(f"backend pid: {backend_pid or '-'}")
    print(f"backend bind: {settings.backend_host}:{settings.backend_port}")
    print(f"frontend dist: {'ready' if frontend_dist_ready() else 'missing'}")
    print(f"frontend dist path: {FRONTEND_DIST_ROOT}")
    print(f"backend stdout: {backend.stdout_log}")
    print(f"backend stderr: {backend.stderr_log}")


def show_logs(stream: str, *, lines: int, follow: bool) -> None:
    backend = backend_service()
    targets: list[tuple[object, str]] = []
    if stream in {"out", "stdout", "all"}:
        targets.append((backend.stdout_log, "backend stdout"))
    if stream in {"err", "stderr", "all"}:
        targets.append((backend.stderr_log, "backend stderr"))

    if follow:
        follow_log_files(targets, lines=lines)
        return

    from process_control import show_log_file

    for path, label in targets:
        show_log_file(path, lines=lines, label=label)


def main() -> int:
    parser = argparse.ArgumentParser(description="AetherCore production runtime script")
    parser.add_argument("action", choices=["start", "stop", "restart", "status", "health", "logs"], nargs="?", default="start")
    parser.add_argument("target", nargs="?", default="all")
    parser.add_argument("--lines", type=int, default=50)
    parser.add_argument("-f", "--follow", action="store_true")
    args = parser.parse_args()

    ensure_runtime_dirs()
    settings = load_runtime_settings()
    backend = backend_service()
    health_url = f"http://127.0.0.1:{settings.backend_port}/api/v1/health"

    if args.action == "start":
        if not frontend_dist_ready():
            print(f"warning: frontend dist is missing at {FRONTEND_DIST_ROOT}")
        return 0 if start_service(backend) else 1

    if args.action == "stop":
        return 0 if stop_service(backend) else 1

    if args.action == "restart":
        ok = stop_service(backend)
        if not frontend_dist_ready():
            print(f"warning: frontend dist is missing at {FRONTEND_DIST_ROOT}")
        ok = start_service(backend) and ok
        return 0 if ok else 1

    if args.action == "status":
        show_status()
        return 0

    if args.action == "health":
        return 0 if check_health(health_url) else 1

    if args.action == "logs":
        if args.target not in {"all", "out", "stdout", "err", "stderr"}:
            print("invalid log target, use: all | out | err")
            return 1
        show_logs(args.target, lines=max(args.lines, 1), follow=args.follow)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
