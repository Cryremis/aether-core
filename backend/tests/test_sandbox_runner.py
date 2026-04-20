# backend/tests/test_sandbox_runner.py
import asyncio
import subprocess
from pathlib import Path

import pytest

from app.core.config import settings
from app.sandbox.docker_executor import BaselineRuntimePlan, DockerSandboxExecutor
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


def build_baseline_workspace(root: Path, baseline_root: Path) -> SandboxWorkspace:
    workspace = build_workspace(root)
    for name in ["input", "skills", "work", "output", "logs"]:
        (baseline_root / name).mkdir(parents=True, exist_ok=True)
    (baseline_root / "input" / "hello.txt").write_text("hello from baseline", encoding="utf-8")
    return SandboxWorkspace(
        session_id=workspace.session_id,
        root=workspace.root,
        baseline_root=baseline_root,
        input_dir=workspace.input_dir,
        skills_dir=workspace.skills_dir,
        work_dir=workspace.work_dir,
        output_dir=workspace.output_dir,
        logs_dir=workspace.logs_dir,
        overlay_work_dir=workspace.overlay_work_dir,
        metadata_dir=workspace.metadata_dir,
    )


def test_docker_executor_builds_isolated_run_args(tmp_path):
    workspace = build_workspace(tmp_path / "sandbox")
    executor = DockerSandboxExecutor()

    args = executor._build_docker_run_args(
        workspace,
        "test-container",
        ["/bin/bash", "-c", "echo hello"],
        BaselineRuntimePlan(mode="direct", mount_upper_workspace=False, requires_root=False),
    )

    joined = " ".join(args)
    assert "--network none" in joined
    assert "--read-only" in joined
    assert "--cap-drop ALL" in joined
    assert settings.sandbox_docker_image in args


def test_copy_baseline_runtime_command_keeps_shell_variables_escaped():
    executor = DockerSandboxExecutor()

    command = executor._build_runtime_command(
        ["/bin/bash", "-c", "cat /workspace/input/hello.txt"],
        BaselineRuntimePlan(mode="copy", mount_upper_workspace=False, requires_root=False),
    )

    script = command[-1]
    assert "/aether/baseline/${name}" in script
    assert "${{name}}" not in script
    assert "cat /workspace/input/hello.txt" in script


def test_overlay_baseline_run_args_requires_root_and_tmpfs(tmp_path):
    workspace = build_baseline_workspace(tmp_path / "sandbox", tmp_path / "baseline")
    executor = DockerSandboxExecutor()

    args = executor._build_docker_run_args(
        workspace,
        "test-container",
        ["/bin/bash", "-c", "echo hello"],
        BaselineRuntimePlan(mode="overlay", mount_upper_workspace=True, requires_root=True),
    )

    joined = " ".join(args)
    assert "--user root" in joined
    assert "dst=/aether/baseline,readonly" in joined
    assert "/workspace:size=64m" in joined


def test_copy_baseline_run_args_mounts_workspace_and_baseline(tmp_path):
    workspace = build_baseline_workspace(tmp_path / "sandbox", tmp_path / "baseline")
    executor = DockerSandboxExecutor()

    args = executor._build_docker_run_args(
        workspace,
        "test-container",
        ["/bin/bash", "-c", "echo hello"],
        BaselineRuntimePlan(mode="copy", mount_upper_workspace=False, requires_root=False),
    )

    joined = " ".join(args)
    assert "--user root" not in joined
    assert f"src={workspace.root.resolve()},dst=/workspace" in joined
    assert "dst=/aether/baseline,readonly" in joined


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


def test_docker_executor_smoke_with_copy_baseline_strategy(tmp_path):
    executor = DockerSandboxExecutor()
    docker_binary = executor._resolve_docker_binary()
    if not docker_binary:
        pytest.skip("docker 未安装，跳过真实容器 smoke test。")

    version = subprocess.run(
        [docker_binary, "version", "--format", "{{.Server.Version}}"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if version.returncode != 0:
        pytest.skip("docker daemon 不可用，跳过真实容器 smoke test。")

    workspace = build_baseline_workspace(tmp_path / "sandbox", tmp_path / "baseline")
    executor._baseline_plan_cache = BaselineRuntimePlan(
        mode="copy",
        mount_upper_workspace=False,
        requires_root=False,
    )

    async def execute():
        result = await executor.run_shell(
            workspace,
            'Get-Content /workspace/input/hello.txt; Write-Output "pwsh-ok"',
            "powershell",
        )
        assert result.exit_code == 0
        assert "hello from baseline" in result.stdout
        assert "pwsh-ok" in result.stdout

    asyncio.run(execute())
