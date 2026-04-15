# backend/app/sandbox/models.py
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SandboxWorkspace:
    """会话沙箱工作区。"""

    session_id: str
    root: Path
    input_dir: Path
    skills_dir: Path
    work_dir: Path
    output_dir: Path
    logs_dir: Path
    metadata_dir: Path


@dataclass(frozen=True)
class SandboxCommandResult:
    """沙箱命令执行结果。"""

    command: str
    shell: str
    executor: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    log_path: str
