# manage.py
from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = PROJECT_ROOT / ".runtime"
PID_ROOT = RUNTIME_ROOT / "pids"
LOG_ROOT = RUNTIME_ROOT / "logs"
BACKEND_ROOT = PROJECT_ROOT / "backend"
FRONTEND_ROOT = PROJECT_ROOT / "frontend"

BACKEND_PID_FILE = PID_ROOT / "backend.pid"
FRONTEND_PID_FILE = PID_ROOT / "frontend.pid"
BACKEND_OUT_LOG = LOG_ROOT / "backend.out.log"
BACKEND_ERR_LOG = LOG_ROOT / "backend.err.log"
FRONTEND_OUT_LOG = LOG_ROOT / "frontend.out.log"
FRONTEND_ERR_LOG = LOG_ROOT / "frontend.err.log"
BACKEND_PORT = 8100
FRONTEND_PORT = 5178


def ensure_dirs() -> None:
    PID_ROOT.mkdir(parents=True, exist_ok=True)
    LOG_ROOT.mkdir(parents=True, exist_ok=True)


def read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8").strip()
    return int(content) if content else None


def is_running(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def pid_by_port(port: int) -> int | None:
    if os.name != "nt":
        return None
    result = subprocess.run(
        ["netstat", "-ano"],
        capture_output=True,
        text=True,
        check=False,
    )
    target = f"127.0.0.1:{port}"
    for line in result.stdout.splitlines():
        if "LISTENING" not in line or target not in line:
            continue
        parts = line.split()
        if parts:
            try:
                return int(parts[-1])
            except ValueError:
                return None
    return None


def write_pid(path: Path, pid: int) -> None:
    path.write_text(str(pid), encoding="utf-8")


def remove_pid(path: Path) -> None:
    if path.exists():
        path.unlink()


def start_backend() -> None:
    pid = read_pid(BACKEND_PID_FILE) or pid_by_port(BACKEND_PORT)
    if is_running(pid) or port_is_open(BACKEND_PORT):
        print(f"backend 已在运行，PID={pid}")
        return
    with BACKEND_OUT_LOG.open("ab") as stdout, BACKEND_ERR_LOG.open("ab") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8100"],
            cwd=BACKEND_ROOT,
            stdout=stdout,
            stderr=stderr,
        )
    write_pid(BACKEND_PID_FILE, process.pid)
    print(f"backend 已启动，PID={process.pid}")


def start_frontend() -> None:
    pid = read_pid(FRONTEND_PID_FILE) or pid_by_port(FRONTEND_PORT)
    if is_running(pid) or port_is_open(FRONTEND_PORT):
        print(f"frontend 已在运行，PID={pid}")
        return
    with FRONTEND_OUT_LOG.open("ab") as stdout, FRONTEND_ERR_LOG.open("ab") as stderr:
        process = subprocess.Popen(
            ["npm.cmd", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5178"],
            cwd=FRONTEND_ROOT,
            stdout=stdout,
            stderr=stderr,
            shell=False,
        )
    write_pid(FRONTEND_PID_FILE, process.pid)
    print(f"frontend 已启动，PID={process.pid}")


def stop_process(name: str, pid_file: Path) -> None:
    pid = read_pid(pid_file)
    port = BACKEND_PORT if name == "backend" else FRONTEND_PORT
    if not pid:
        pid = pid_by_port(port)
    if not pid:
        print(f"{name} 未运行")
        return
    if not is_running(pid):
        remove_pid(pid_file)
        print(f"{name} 进程不存在，已清理 PID")
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True, text=True)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    for _ in range(30):
        if not is_running(pid) and not port_is_open(port):
            break
        time.sleep(0.2)
    if os.name != "nt" and is_running(pid):
        os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
    remove_pid(pid_file)
    print(f"{name} 已停止")


def show_status() -> None:
    backend_pid = read_pid(BACKEND_PID_FILE) or pid_by_port(BACKEND_PORT)
    frontend_pid = read_pid(FRONTEND_PID_FILE) or pid_by_port(FRONTEND_PORT)
    print(f"backend: {'running' if port_is_open(BACKEND_PORT) else 'stopped'}")
    print(f"frontend: {'running' if port_is_open(FRONTEND_PORT) else 'stopped'}")
    print(f"backend pid: {backend_pid or '-'}")
    print(f"frontend pid: {frontend_pid or '-'}")
    print(f"backend stdout: {BACKEND_OUT_LOG}")
    print(f"backend stderr: {BACKEND_ERR_LOG}")
    print(f"frontend stdout: {FRONTEND_OUT_LOG}")
    print(f"frontend stderr: {FRONTEND_ERR_LOG}")


def build_frontend() -> None:
    subprocess.run(["npm.cmd", "run", "build"], cwd=FRONTEND_ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="AetherCore 统一运维脚本")
    parser.add_argument("action", choices=["start", "stop", "restart", "status", "build"], nargs="?", default="start")
    parser.add_argument("target", choices=["all", "backend", "frontend"], nargs="?", default="all")
    args = parser.parse_args()

    ensure_dirs()

    if args.action == "start":
        if args.target in {"all", "backend"}:
            start_backend()
        if args.target in {"all", "frontend"}:
            start_frontend()
        return 0

    if args.action == "stop":
        if args.target in {"all", "frontend"}:
            stop_process("frontend", FRONTEND_PID_FILE)
        if args.target in {"all", "backend"}:
            stop_process("backend", BACKEND_PID_FILE)
        return 0

    if args.action == "restart":
        if args.target in {"all", "frontend"}:
            stop_process("frontend", FRONTEND_PID_FILE)
        if args.target in {"all", "backend"}:
            stop_process("backend", BACKEND_PID_FILE)
        if args.target in {"all", "backend"}:
            start_backend()
        if args.target in {"all", "frontend"}:
            start_frontend()
        return 0

    if args.action == "status":
        show_status()
        return 0

    if args.action == "build":
        if args.target in {"all", "frontend"}:
            build_frontend()
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
