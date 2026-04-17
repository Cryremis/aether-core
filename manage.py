# manage.py
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
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
START_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class ServiceSpec:
    """统一描述一个可管理服务。"""

    name: str
    workdir: Path
    pid_file: Path
    stdout_log: Path
    stderr_log: Path
    port: int

    def command(self) -> list[str]:
        if self.name == "backend":
            return [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ]
        vite_bin = FRONTEND_ROOT / "node_modules" / "vite" / "bin" / "vite.js"
        if vite_bin.exists():
            return ["node", str(vite_bin), "--host", "127.0.0.1", "--port", str(self.port)]
        return ["npm.cmd" if os.name == "nt" else "npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(self.port)]


BACKEND_SERVICE = ServiceSpec(
    name="backend",
    workdir=BACKEND_ROOT,
    pid_file=BACKEND_PID_FILE,
    stdout_log=BACKEND_OUT_LOG,
    stderr_log=BACKEND_ERR_LOG,
    port=BACKEND_PORT,
)
FRONTEND_SERVICE = ServiceSpec(
    name="frontend",
    workdir=FRONTEND_ROOT,
    pid_file=FRONTEND_PID_FILE,
    stdout_log=FRONTEND_OUT_LOG,
    stderr_log=FRONTEND_ERR_LOG,
    port=FRONTEND_PORT,
)


def ensure_dirs() -> None:
    PID_ROOT.mkdir(parents=True, exist_ok=True)
    LOG_ROOT.mkdir(parents=True, exist_ok=True)


def read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return None
    if content.startswith("{"):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return None
        pid = payload.get("pid")
        return int(pid) if pid else None
    return int(content)


def is_running(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue; if ($p) {{ 'running' }}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return "running" in result.stdout
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
    if os.name == "nt":
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

    result = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in result.stdout.splitlines():
        try:
            return int(line.strip())
        except ValueError:
            continue
    return None


def write_pid(path: Path, spec: ServiceSpec, pid: int) -> None:
    payload = {
        "service": spec.name,
        "pid": pid,
        "port": spec.port,
        "command": spec.command(),
        "workdir": str(spec.workdir),
        "updated_at": time.time(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def remove_pid(path: Path) -> None:
    if path.exists():
        path.unlink()


def resolve_service_pid(spec: ServiceSpec) -> int | None:
    pid = read_pid(spec.pid_file)
    if pid and is_running(pid):
        return pid
    port_pid = pid_by_port(spec.port)
    if port_pid and is_running(port_pid):
        if port_pid != pid:
            write_pid(spec.pid_file, spec, port_pid)
        return port_pid
    if pid and not is_running(pid):
        remove_pid(spec.pid_file)
    return None


def tail_log(path: Path, lines: int = 20) -> str:
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(content[-lines:])


def start_service(spec: ServiceSpec) -> None:
    pid = resolve_service_pid(spec)
    if pid or port_is_open(spec.port):
        print(f"{spec.name} 已在运行，PID={pid}")
        return

    popen_kwargs: dict[str, object] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    else:
        popen_kwargs["start_new_session"] = True

    with spec.stdout_log.open("ab") as stdout, spec.stderr_log.open("ab") as stderr:
        process = subprocess.Popen(
            spec.command(),
            cwd=spec.workdir,
            stdout=stdout,
            stderr=stderr,
            shell=False,
            **popen_kwargs,
        )
    write_pid(spec.pid_file, spec, process.pid)

    deadline = time.time() + START_TIMEOUT_SECONDS
    while time.time() < deadline:
        current_pid = resolve_service_pid(spec)
        if current_pid and port_is_open(spec.port):
            print(f"{spec.name} 已启动，PID={current_pid}")
            return
        if process.poll() is not None:
            break
        time.sleep(0.2)

    remove_pid(spec.pid_file)
    print(f"{spec.name} 启动失败")
    stderr_tail = tail_log(spec.stderr_log)
    if stderr_tail:
        print(stderr_tail)


def stop_service(spec: ServiceSpec) -> None:
    pid = resolve_service_pid(spec)
    if not pid:
        print(f"{spec.name} 未运行")
        return
    if os.name == "nt":
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"Stop-Process -Id {pid} -Force -ErrorAction Stop",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print(f"{spec.name} 停止失败，无法终止 PID={pid}")
            if result.stderr.strip():
                print(result.stderr.strip())
            return
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    for _ in range(30):
        if not is_running(pid) and not port_is_open(spec.port):
            break
        time.sleep(0.2)
    if os.name != "nt" and is_running(pid):
        os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
    remaining_pid = resolve_service_pid(spec)
    if remaining_pid or port_is_open(spec.port):
        print(f"{spec.name} 停止失败，PID={remaining_pid or pid} 仍在运行")
        return
    remove_pid(spec.pid_file)
    print(f"{spec.name} 已停止")


def show_status() -> None:
    backend_pid = resolve_service_pid(BACKEND_SERVICE)
    frontend_pid = resolve_service_pid(FRONTEND_SERVICE)
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
            start_service(BACKEND_SERVICE)
        if args.target in {"all", "frontend"}:
            start_service(FRONTEND_SERVICE)
        return 0

    if args.action == "stop":
        if args.target in {"all", "frontend"}:
            stop_service(FRONTEND_SERVICE)
        if args.target in {"all", "backend"}:
            stop_service(BACKEND_SERVICE)
        return 0

    if args.action == "restart":
        if args.target in {"all", "frontend"}:
            stop_service(FRONTEND_SERVICE)
        if args.target in {"all", "backend"}:
            stop_service(BACKEND_SERVICE)
        if args.target in {"all", "backend"}:
            start_service(BACKEND_SERVICE)
        if args.target in {"all", "frontend"}:
            start_service(FRONTEND_SERVICE)
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
