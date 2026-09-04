from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = PROJECT_ROOT / ".runtime"
PID_ROOT = RUNTIME_ROOT / "pids"
LOG_ROOT = RUNTIME_ROOT / "logs"
BACKEND_ROOT = PROJECT_ROOT / "backend"
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
FRONTEND_DIST_ROOT = FRONTEND_ROOT / "dist"
BACKEND_ENV_FILE = BACKEND_ROOT / ".env"

# 启动后等待端口就绪的窗口
START_TIMEOUT_SECONDS = 20
# SIGTERM 后的优雅退出窗口,应大于 uvicorn --timeout-graceful-shutdown(10s)
GRACEFUL_STOP_TIMEOUT_SECONDS = 15
# SIGKILL 后等待进程消亡的窗口
SIGKILL_TIMEOUT_SECONDS = 5
# 进程退出后等待端口释放的窗口(TIME_WAIT/内核清理)
PORT_RELEASE_TIMEOUT_SECONDS = 5


class ProcessState(Enum):
    """进程活性三态。

    ZOMBIE 表示进程已退出但尚未被父进程收割(wait/reap)。
    僵尸不持有端口、不消耗 CPU、不可被信号唤醒——对服务管理而言等价于死,
    但 os.kill(pid, 0) 会误报为存活,这正是 stale pid 死锁的根源。
    """

    ALIVE = "alive"
    ZOMBIE = "zombie"
    DEAD = "dead"


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


def get_process_state(pid: int | None) -> ProcessState:
    """判定进程活性三态,僵尸感知,不受 pid 复用伪装影响的只是存在性。"""
    if not pid:
        return ProcessState.DEAD
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
        return ProcessState.ALIVE if "running" in result.stdout else ProcessState.DEAD

    # POSIX: /proc/<pid>/stat 的 state 字段(comm 含空格/括号,须从最后一个 ')' 之后解析)
    try:
        stat_content = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError):
        return ProcessState.DEAD
    except PermissionError:
        # 进程存在但无权读取,保守视为存活
        return ProcessState.ALIVE
    try:
        state_char = stat_content[stat_content.rfind(")") + 2 :].split()[0]
    except (IndexError, ValueError):
        return ProcessState.DEAD
    if state_char == "Z":
        return ProcessState.ZOMBIE
    return ProcessState.ALIVE


def is_running(pid: int | None) -> bool:
    """向后兼容的布尔视图:僵尸与死进程都返回 False。"""
    return get_process_state(pid) == ProcessState.ALIVE


def pid_matches_spec(pid: int, spec: ServiceSpec) -> bool:
    """核对 pid 对应的命令行是否仍是本服务,防 pid 复用误判。

    POSIX 读 /proc/<pid>/cmdline;Windows 无对应机制,恒真
    (由端口校验兜底)。
    """
    if os.name == "nt":
        return True
    try:
        cmdline_blob = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "ignore")
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return False
    args = [part for part in cmdline_blob.split("\0") if part]
    if not args:
        # 僵尸/内核线程的 cmdline 为空
        return False
    marker = _command_marker(spec)
    if marker is None:
        return True
    return marker in cmdline_blob


def _command_marker(spec: ServiceSpec) -> str | None:
    """从命令定义提取稳定标识(如 `-m uvicorn` 的模块名),用于命令行核对。"""
    command = spec.command
    for index, part in enumerate(command):
        if part == "-m" and index + 1 < len(command):
            return command[index + 1]
    return None


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
    """解析服务 pid:pid 文件 + 端口双源,僵尸/死进程/pid 复用均自动清理。

    返回 None 表示服务未在运行(此时 pid 文件已被自愈清理或本就不存在)。
    """
    pid = read_pid(spec.pid_file)
    if pid is not None:
        state = get_process_state(pid)
        if state == ProcessState.ALIVE and pid_matches_spec(pid, spec):
            return pid
        if state != ProcessState.ALIVE:
            # 僵尸或死进程:pid 文件已过期,清理
            remove_pid(spec.pid_file)
        # 存活但命令行不匹配(pid 被复用):pid 文件指向了别人的进程,同样过期
        else:
            remove_pid(spec.pid_file)
    port_pid = pid_by_port(spec.port)
    if port_pid and get_process_state(port_pid) == ProcessState.ALIVE:
        if port_pid != pid:
            write_pid(spec.pid_file, spec, port_pid)
        return port_pid
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


def _wait_for(predicate, timeout_seconds: float, poll_interval: float = 0.2) -> bool:
    """在超时窗口内轮询等待条件成立,返回最终一次判定结果。"""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll_interval)
    return predicate()


def start_service(spec: ServiceSpec) -> bool:
    """启动服务。端口是唯一真相:

    - 端口已被本服务进程监听 → 收养(pid 文件自动回写),幂等成功
    - 端口被外来进程占用 → 明确报错(不覆盖别人的服务)
    - pid 文件指向死/僵尸/复用进程 → 自愈清理后正常启动
    - pid 对应进程活着但没监听 → 可能仍在启动中,短暂等待后报告
    """
    # 预检:端口裁决
    if port_is_open(spec.port):
        holder_pid = pid_by_port(spec.port)
        if holder_pid and get_process_state(holder_pid) == ProcessState.ALIVE:
            if read_pid(spec.pid_file) != holder_pid:
                write_pid(spec.pid_file, spec, holder_pid)
            print(f"{spec.name} already running, pid={holder_pid}")
            return True
        print(
            f"{spec.name} cannot start: port {spec.port} is occupied "
            f"but no live owning process was identified (holder pid={holder_pid or 'unknown'})"
        )
        return False

    # 端口空闲:核对 pid 文件
    recorded_pid = read_pid(spec.pid_file)
    if recorded_pid is not None:
        state = get_process_state(recorded_pid)
        if state == ProcessState.ALIVE and pid_matches_spec(recorded_pid, spec):
            # 进程活着但尚未监听:可能正在启动(uvicorn 冷启动/依赖加载),给一个短窗口
            if _wait_for(lambda: port_is_open(spec.port), timeout_seconds=8):
                print(f"{spec.name} already running, pid={recorded_pid}")
                return True
            print(
                f"{spec.name} pid={recorded_pid} is alive but not listening on port {spec.port}; "
                f"it may be wedged — check {spec.stderr_log}"
            )
            return False
        # 死进程 / 僵尸 / pid 被复用:pid 文件过期,自愈
        remove_pid(spec.pid_file)

    # 启动
    popen_kwargs: dict[str, int] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    else:
        popen_kwargs["start_new_session"] = 1

    with spec.stdout_log.open("ab") as stdout, spec.stderr_log.open("ab") as stderr:
        process = subprocess.Popen(
            spec.command,
            cwd=spec.workdir,
            stdout=stdout,
            stderr=stderr,
            shell=False,
            **popen_kwargs,  # type: ignore[arg-type]
        )
    write_pid(spec.pid_file, spec, process.pid)

    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if port_is_open(spec.port):
            print(f"{spec.name} started, pid={process.pid}")
            return True
        if process.poll() is not None:
            break
        time.sleep(0.2)

    # 启动失败:收敛到干净状态(不留半死进程和脏 pid 文件)
    print(f"{spec.name} failed to start")
    if get_process_state(process.pid) == ProcessState.ALIVE:
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass
    remove_pid(spec.pid_file)
    stderr_tail = tail_log(spec.stderr_log)
    if stderr_tail:
        print(stderr_tail)
    return False


def stop_service(spec: ServiceSpec) -> bool:
    """停止服务,分级收敛,保证终态干净:

    POSIX:  SIGTERM(优雅窗口 15s) → SIGKILL(强制窗口 5s) → 端口释放等待(5s) → pid 清理
    Windows: Stop-Process(强停) → 端口释放等待 → pid 清理

    僵尸视为已停止(惰性进程,等父进程收割,不影响服务语义)。
    无论走哪条路径,只要进程死透且端口释放,pid 文件必然被清理。
    """
    pid = resolve_service_pid(spec)
    if not pid:
        # 无可识别的运行进程:清理可能残留的 stale pid 文件
        stale_pid = read_pid(spec.pid_file)
        if stale_pid is not None and get_process_state(stale_pid) != ProcessState.ALIVE:
            remove_pid(spec.pid_file)
        if port_is_open(spec.port):
            holder = pid_by_port(spec.port)
            print(
                f"{spec.name} cannot stop: port {spec.port} is held by an unmanaged process "
                f"(pid={holder or 'unknown'}) — stop it manually"
            )
            return False
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
        if result.returncode != 0 and get_process_state(pid) == ProcessState.ALIVE:
            print(f"{spec.name} failed to stop, pid={pid}")
            if result.stderr.strip():
                print(result.stderr.strip())
            return False
    else:
        # 第一级:优雅停止。uvicorn 配置了 --timeout-graceful-shutdown,
        # 收到 SIGTERM 后会在内部宽限期内处理完在途请求再退出。
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        if not _wait_for(
            lambda: get_process_state(pid) != ProcessState.ALIVE,
            GRACEFUL_STOP_TIMEOUT_SECONDS,
        ):
            # 第二级:强制终止。SIGKILL 不可被捕获,进程必死;
            # 死后进入僵尸态直到父进程收割——对服务管理而言等价于已停止。
            print(
                f"{spec.name} did not exit within {GRACEFUL_STOP_TIMEOUT_SECONDS}s "
                f"(in-flight requests?), sending SIGKILL"
            )
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            _wait_for(
                lambda: get_process_state(pid) != ProcessState.ALIVE,
                SIGKILL_TIMEOUT_SECONDS,
            )

    final_state = get_process_state(pid)
    if final_state == ProcessState.ALIVE:
        # SIGKILL 后仍存活:理论上不可能,除非进程处于不可中断睡眠(D 状态)
        print(
            f"{spec.name} failed to stop, pid={pid} is unkillable "
            "(likely in uninterruptible sleep — check for stuck I/O)"
        )
        return False

    # 第三级:等待端口释放(内核清理监听 socket)
    if not _wait_for(lambda: not port_is_open(spec.port), PORT_RELEASE_TIMEOUT_SECONDS):
        holder = pid_by_port(spec.port)
        print(
            f"{spec.name} process exited but port {spec.port} is still held "
            f"(pid={holder or 'unknown'})"
        )
        return False

    # 收敛完成:清理 pid 文件
    remove_pid(spec.pid_file)
    if final_state == ProcessState.ZOMBIE:
        print(f"{spec.name} stopped (pid={pid} zombie pending parent reaping)")
    else:
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
