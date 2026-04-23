from __future__ import annotations

import argparse
import os
import sys

from process_control import (
    BACKEND_ROOT,
    FRONTEND_ROOT,
    LOG_ROOT,
    PID_ROOT,
    ServiceSpec,
    build_frontend,
    ensure_runtime_dirs,
    follow_log_files,
    load_runtime_settings,
    port_is_open,
    resolve_service_pid,
    show_log_file,
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


def frontend_service() -> ServiceSpec:
    settings = load_runtime_settings()
    vite_bin = FRONTEND_ROOT / "node_modules" / "vite" / "bin" / "vite.js"
    if vite_bin.exists():
        command = ["node", str(vite_bin), "--host", settings.frontend_host, "--port", str(settings.frontend_port)]
    else:
        npm_bin = "npm.cmd" if os.name == "nt" else "npm"
        command = [npm_bin, "run", "dev", "--", "--host", settings.frontend_host, "--port", str(settings.frontend_port)]

    return ServiceSpec(
        name="frontend",
        workdir=FRONTEND_ROOT,
        pid_file=PID_ROOT / "frontend.pid",
        stdout_log=LOG_ROOT / "frontend.out.log",
        stderr_log=LOG_ROOT / "frontend.err.log",
        port=settings.frontend_port,
        command=command,
    )


def show_status() -> None:
    settings = load_runtime_settings()
    backend = backend_service()
    frontend = frontend_service()
    backend_pid = resolve_service_pid(backend)
    frontend_pid = resolve_service_pid(frontend)
    print(f"backend: {'running' if port_is_open(settings.backend_port) else 'stopped'}")
    print(f"frontend: {'running' if port_is_open(settings.frontend_port) else 'stopped'}")
    print(f"backend pid: {backend_pid or '-'}")
    print(f"frontend pid: {frontend_pid or '-'}")
    print(f"backend bind: {settings.backend_host}:{settings.backend_port}")
    print(f"frontend bind: {settings.frontend_host}:{settings.frontend_port}")
    print(f"backend stdout: {backend.stdout_log}")
    print(f"backend stderr: {backend.stderr_log}")
    print(f"frontend stdout: {frontend.stdout_log}")
    print(f"frontend stderr: {frontend.stderr_log}")


def show_logs(target: str, *, lines: int, follow: bool) -> None:
    backend = backend_service()
    frontend = frontend_service()
    entries: list[tuple[object, str]] = []

    if target in {"backend", "all"}:
        entries.append((backend.stdout_log, "backend stdout"))
        entries.append((backend.stderr_log, "backend stderr"))
    if target in {"frontend", "all"}:
        entries.append((frontend.stdout_log, "frontend stdout"))
        entries.append((frontend.stderr_log, "frontend stderr"))

    if follow:
        follow_log_files(entries, lines=lines)
        return

    for path, label in entries:
        show_log_file(path, lines=lines, label=label)


def main() -> int:
    parser = argparse.ArgumentParser(description="AetherCore development runtime script")
    parser.add_argument("action", choices=["start", "stop", "restart", "status", "build", "logs"], nargs="?", default="start")
    parser.add_argument("target", choices=["all", "backend", "frontend"], nargs="?", default="all")
    parser.add_argument("--lines", type=int, default=50)
    parser.add_argument("-f", "--follow", action="store_true")
    args = parser.parse_args()

    ensure_runtime_dirs()
    backend = backend_service()
    frontend = frontend_service()

    if args.action == "start":
        if args.target in {"all", "backend"} and not start_service(backend):
            return 1
        if args.target in {"all", "frontend"} and not start_service(frontend):
            return 1
        return 0

    if args.action == "stop":
        ok = True
        if args.target in {"all", "frontend"}:
            ok = stop_service(frontend) and ok
        if args.target in {"all", "backend"}:
            ok = stop_service(backend) and ok
        return 0 if ok else 1

    if args.action == "restart":
        ok = True
        if args.target in {"all", "frontend"}:
            ok = stop_service(frontend) and ok
        if args.target in {"all", "backend"}:
            ok = stop_service(backend) and ok
        if args.target in {"all", "backend"}:
            ok = start_service(backend) and ok
        if args.target in {"all", "frontend"}:
            ok = start_service(frontend) and ok
        return 0 if ok else 1

    if args.action == "status":
        show_status()
        return 0

    if args.action == "build":
        if args.target in {"all", "frontend"}:
            build_frontend()
        return 0

    if args.action == "logs":
        show_logs(args.target, lines=max(args.lines, 1), follow=args.follow)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
