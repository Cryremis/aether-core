from __future__ import annotations

import asyncio
from pathlib import Path

from app.services.search_service import search_service
from app.services.session_service import AgentSession
from app.sandbox.models import SandboxCommandResult, SandboxWorkspace


def build_workspace(root: Path) -> SandboxWorkspace:
    for name in ["input", "skills", "work", "output", "logs", "metadata", ".overlay-work"]:
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in ["input", "skills", "work", "output", "logs"]:
        (root / ".overlay-work" / name).mkdir(parents=True, exist_ok=True)
    return SandboxWorkspace(
        session_id="sess_search",
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


def test_search_defaults_to_container_work_dir(tmp_path):
    session = AgentSession(session_id="sess_search", workspace=build_workspace(tmp_path / "sandbox"))
    assert search_service.resolve_cwd(session, None) == "/workspace/work"
    assert search_service.resolve_cwd(session, "") == "/workspace/work"


def test_grep_shell_quotes_pattern_and_glob(monkeypatch, tmp_path):
    workspace = build_workspace(tmp_path / "sandbox")
    session = AgentSession(session_id="sess_search", workspace=workspace)
    recorded: dict[str, str] = {}

    async def fake_run_shell(*, workspace, command, shell):
        recorded["command"] = command
        recorded["shell"] = shell
        return SandboxCommandResult(
            command=command,
            shell=shell,
            executor="docker",
            exit_code=0,
            stdout="work/app.py\n",
            stderr="",
            duration_ms=5,
            log_path="logs/cmd.json",
        )

    monkeypatch.setattr("app.services.ripgrep_service.sandbox_runner.run_shell", fake_run_shell)

    async def execute():
        result = await search_service.execute_grep(
            session,
            {
                "pattern": "hello; touch /tmp/pwned",
                "glob": "*.py",
                "path": "work/repo dir",
                "output_mode": "files_with_matches",
            },
        )
        assert result["filenames"] == ["work/app.py"]

    asyncio.run(execute())

    command = recorded["command"]
    assert recorded["shell"] == "bash"
    assert "cd '/workspace/work/repo dir' &&" in command
    assert "'hello; touch /tmp/pwned'" in command
    assert "'*.py'" in command
    assert "touch /tmp/pwned &&" not in command


def test_glob_uses_workspace_root_for_relative_path(monkeypatch, tmp_path):
    workspace = build_workspace(tmp_path / "sandbox")
    session = AgentSession(session_id="sess_search", workspace=workspace)
    recorded: dict[str, str] = {}

    async def fake_run_shell(*, workspace, command, shell):
        recorded["command"] = command
        return SandboxCommandResult(
            command=command,
            shell=shell,
            executor="docker",
            exit_code=0,
            stdout="src/main.py\n",
            stderr="",
            duration_ms=5,
            log_path="logs/cmd.json",
        )

    monkeypatch.setattr("app.services.ripgrep_service.sandbox_runner.run_shell", fake_run_shell)

    async def execute():
        result = await search_service.execute_glob(
            session,
            {
                "pattern": "**/*.py",
                "path": "work/repo",
            },
        )
        assert result["filenames"] == ["src/main.py"]

    asyncio.run(execute())
    assert "cd /workspace/work/repo &&" in recorded["command"]
