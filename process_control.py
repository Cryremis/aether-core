from __future__ import annotations

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
FRONTEND_DIST_ROOT = FRONTEND_ROOT / "dist"
BACKEND_ENV_FILE = BACKEND_ROOT / ".env"
START_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    workdir: Path
    pid_file: Path
    stdout_log: Path
    stderr_log: Path
    port: int
    command: list[str]


@dataclass(frozen=True)
class RuntimeSettings:
    backend_host: str
    backend_port: int
    frontend_host: str
    frontend_port: int


def load_runtime_settings() -> RuntimeSettings:
    env_values = _load_env_file(BACKEND_ENV_FILE)
    backend_host = _read_setting("MANAGE_BACKEND_HOST", env_values, default=env_values.get("APP_HOST", "127.0.0.1"))
    backend_port = _read_int_setting(
        "MANAGE_BACKEND_PORT",
        env_values,
        default=_safe_int(env_values.get("APP_PORT"), 8100),
    )
    frontend_host = _read_setting("MANAGE_FRONTEND_HOST", env_values, default="127.0.0.1")
    frontend_port = _read_int_setting("MANAGE_FRONTEND_PORT", env_values, default=5178)
    return RuntimeSettings(
        backend_host=backend_host,
        backend_port=backend_port,
        frontend_host=frontend_host,
        frontend_port=frontend_port,
    )


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _safe_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _read_setting(name: str, env_values: dict[str, str], *, default: str) -> str:
    value = os.environ.get(name)
    if value is not None and value.strip():
        return value.strip()
    file_value = env_values.get(name)
    if file_value is not None and file_value.strip():
        return file_value.strip()
    return default


def _read_int_setting(name: str, env_values: dict[str, str], *, default: int) -> int:
    value = os.environ.get(name)
    if value is not None and value.strip():
        return _safe_int(value.strip(), default)
    file_value = env_values.get(name)
    if file_value is not None and file_value.strip():
        return _safe_int(file_value.strip(), default)
    return default


def ensure_runtime_dirs() -> None:
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


def port_is_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def pid_by_port(port: int) -> int | None:
    if os.name == "nt":
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            check=False,
        )
        targets = {f"127.0.0.1:{port}", f"0.0.0.0:{port}"}
        for line in result.stdout.splitlines():
            if "LISTENING" not in line or not any(target in line for target in targets):
                continue
            parts = line.split()
            if not parts:
                continue
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
        "command": spec.command,
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


def show_log_file(path: Path, *, lines: int = 50, label: str | None = None) -> None:
    header = label or str(path)
    print(f"=== {header} ===", flush=True)
    if not path.exists():
        print("(missing)", flush=True)
        return
    content = tail_log(path, lines=lines)
    if not content:
        print("(empty)", flush=True)
        return
    _safe_print(content)


def _safe_print(content: str) -> None:
    encoding = sys.stdout.encoding or "utf-8"
    data = content.encode(encoding, errors="replace")
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.write(b"\n")
    sys.stdout.flush()


def follow_log_files(paths: list[tuple[Path, str]], *, lines: int = 50, poll_interval: float = 1.0) -> None:
    for path, label in paths:
        show_log_file(path, lines=lines, label=label)

    positions: dict[str, int] = {}
    buffers: dict[str, str] = {}

    for path, label in paths:
        positions[label] = path.stat().st_size if path.exists() else 0
        buffers[label] = ""

    try:
        while True:
            updated = False
            for path, label in paths:
                if not path.exists():
                    positions[label] = 0
                    continue

                file_size = path.stat().st_size
                if positions[label] > file_size:
                    positions[label] = 0
                    buffers[label] = ""

                with path.open("r", encoding="utf-8", errors="ignore") as handle:
                    handle.seek(positions[label])
                    chunk = handle.read()
                    positions[label] = handle.tell()

                if not chunk:
                    continue

                updated = True
                combined = buffers[label] + chunk
                lines_out = combined.splitlines(keepends=True)
                buffers[label] = ""

                if lines_out and not lines_out[-1].endswith(("\n", "\r")):
                    buffers[label] = lines_out.pop()

                for line in lines_out:
                    _safe_print(f"[{label}] {line.rstrip()}")

            if not updated:
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\nlog follow stopped", flush=True)


def start_service(spec: ServiceSpec) -> bool:
    pid = resolve_service_pid(spec)
    if pid or port_is_open(spec.port):
        print(f"{spec.name} already running, pid={pid or '-'}")
        return True

    popen_kwargs: dict[str, object] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    else:
        popen_kwargs["start_new_session"] = True

    with spec.stdout_log.open("ab") as stdout, spec.stderr_log.open("ab") as stderr:
        process = subprocess.Popen(
            spec.command,
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
            print(f"{spec.name} started, pid={current_pid}")
            return True
        if process.poll() is not None:
            break
        time.sleep(0.2)

    remove_pid(spec.pid_file)
    print(f"{spec.name} failed to start")
    stderr_tail = tail_log(spec.stderr_log)
    if stderr_tail:
        print(stderr_tail)
    return False


def stop_service(spec: ServiceSpec) -> bool:
    pid = resolve_service_pid(spec)
    if not pid:
        print(f"{spec.name} is not running")
        return True

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
            print(f"{spec.name} failed to stop, pid={pid}")
            if result.stderr.strip():
                print(result.stderr.strip())
            return False
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
        print(f"{spec.name} failed to stop, pid={remaining_pid or pid} still running")
        return False

    remove_pid(spec.pid_file)
    print(f"{spec.name} stopped")
    return True


def build_frontend() -> None:
    npm_bin = "npm.cmd" if os.name == "nt" else "npm"
    subprocess.run([npm_bin, "run", "build"], cwd=FRONTEND_ROOT, check=True)


def check_health(url: str) -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="ignore")
    except Exception as exc:  # noqa: BLE001
        print(f"health check failed: {exc}")
        return False

    print(body)
    return True
