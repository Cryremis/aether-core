# backend/tests/test_sandbox_runner.py
import asyncio
from pathlib import Path

import pytest

from app.core.config import settings
from app.sandbox.docker_executor import DockerSandboxExecutor
from app.sandbox.models import SandboxWorkspace
from app.sandbox.runner import SandboxRunner


def build_workspace(root: Path) -> SandboxWorkspace:
    for name in ["input", "skills", "work", "output", "logs", "metadata", ".overlay-work"]:
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in ["input", "skills", "work", "output", "logs"]:
        (root / ".overlay-work" / name).mkdir(parents=True, exist_ok=True)
    return SandboxWorkspace(
        session_id="sess_demo",
        root=root,
        baseline_root=None,
        input_dir=root / "input",
        skills_dir=root / "skills",
        work_dir=root / "work",
        output_dir=root / "output",
        logs_dir=root / "logs",
        overlay_work_dir=root / ".overlay-work",
        metadata_dir=root / "metadata",
    )


def test_docker_executor_builds_isolated_run_args(tmp_path):
    workspace = build_workspace(tmp_path / "sandbox")
    executor = DockerSandboxExecutor()

    args = executor._build_docker_run_args(
        workspace,
        "test-container",
        ["/bin/bash", "-lc", "echo hello"],
    )

    joined = " ".join(args)
    assert "--network none" in joined
    assert "--read-only" in joined
    assert "--cap-drop ALL" in joined
    assert settings.sandbox_docker_image in args


def test_runner_fails_closed_when_executor_unavailable(tmp_path, monkeypatch):
    runner = SandboxRunner()

    class FakeExecutor:
        async def check_availability(self):
            return False, "docker-daemon-down"

        async def run_shell(self, workspace, command, shell):
            raise AssertionError("unavailable executor should not be called")

    monkeypatch.setitem(runner._executors, "docker", FakeExecutor())
    monkeypatch.setattr(settings, "sandbox_executor", "docker")
    monkeypatch.setattr(settings, "sandbox_fail_closed", True)

    workspace = build_workspace(tmp_path / "sandbox")

    async def execute():
        with pytest.raises(RuntimeError, match="fail-closed"):
            await runner.run_shell(workspace, "echo hello", "bash")

    asyncio.run(execute())
