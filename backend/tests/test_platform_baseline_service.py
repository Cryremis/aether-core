from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.schemas.platform import (
    PlatformBaselineDirectoryRequest,
    PlatformBaselineMoveRequest,
    PlatformBaselineWriteRequest,
)
from app.services.conversation_service import conversation_service
from app.services.file_service import file_service
from app.services.platform_baseline_service import platform_baseline_service
from app.services.session_service import session_service
from app.services.skill_service import skill_service
from app.services.store import store_service
from app.services.session_workspace_sync_service import WorkspaceState, session_workspace_sync_service


def initialize_store(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    store_service._db_path = storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.storage_root = storage_root
    session_service._sessions.clear()
    store_service.initialize()
    skill_service.ensure_built_in_layout()


def test_platform_baseline_materializes_into_new_admin_session(tmp_path):
    initialize_store(tmp_path)

    standalone_root = platform_baseline_service.ensure_platform_root("standalone")
    (standalone_root / "work" / "guide.txt").write_text("baseline guide", encoding="utf-8")
    skill_dir = standalone_root / "skills" / "analysis-helper"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: analysis-helper\ndescription: 分析辅助技能\n---\n\n先读取文件，再分析。\n",
        encoding="utf-8",
    )

    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None

    session = conversation_service.bootstrap_admin_workbench(admin)
    files = file_service.list_platform_files(session)
    sidebar_files = file_service.list_sidebar_files(session)
    skills = skill_service.list_for_session(session)

    assert any(item.name == "guide.txt" for item in files)
    assert any(item.name == "guide.txt" for item in sidebar_files)
    assert any(item.name == "analysis-helper" and item.source == "platform" for item in skills)
    assert session.workspace is not None
    assert not (session.workspace.work_dir / "guide.txt").exists()
    assert not (session.workspace.skills_dir / "analysis-helper" / "SKILL.md").exists()
    assert file_service.read_text(session, relative_path="work/guide.txt") == "baseline guide"


def test_platform_baseline_materializes_into_new_host_session(tmp_path):
    initialize_store(tmp_path)

    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None
    platform = store_service.create_platform(
        platform_key="atk-assistant",
        display_name="ATK Assistant",
        host_type="embedded",
        description="ATK 测试平台",
        owner_user_id=admin.user_id,
    )

    platform_root = platform_baseline_service.ensure_platform_root(platform["platform_key"])
    (platform_root / "work" / "repo" / "main.py").parent.mkdir(parents=True, exist_ok=True)
    (platform_root / "work" / "repo" / "main.py").write_text("print('hello')\n", encoding="utf-8")

    session, _ = conversation_service.bootstrap_host_workbench(
        platform_key=platform["platform_key"],
        external_user_id="user-001",
        external_user_name="用户一",
        external_org_id="org-001",
        conversation_id=None,
        conversation_key="conv-001",
        host_name="ATK",
    )

    assert session.workspace is not None
    assert any(item["name"] == "main.py" for item in session.platform_files)
    assert any(item.name == "main.py" for item in file_service.list_sidebar_files(session))
    assert not (session.workspace.work_dir / "repo" / "main.py").exists()
    assert file_service.read_text(session, relative_path="work/repo/main.py").strip() == "print('hello')"


def test_platform_baseline_shared_files_are_visible_in_list_tool(tmp_path):
    initialize_store(tmp_path)

    standalone_root = platform_baseline_service.ensure_platform_root("standalone")
    (standalone_root / "work" / "repo").mkdir(parents=True, exist_ok=True)
    (standalone_root / "work" / "repo" / "main.py").write_text("print('shared')\n", encoding="utf-8")

    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None

    session = conversation_service.bootstrap_admin_workbench(admin)

    work_entries = file_service.list(session, path="/workspace/work")
    repo_entries = file_service.list(session, path="/workspace/work/repo")
    read_result = file_service.read(session, file_path="/workspace/work/repo/main.py")

    assert any(item.name == "repo" and item.entry_type == "dir" for item in work_entries)
    assert any(item.name == "main.py" and item.source == "platform" for item in repo_entries)
    assert "print('shared')" in read_result.content


def test_deleted_baseline_file_is_hidden_from_list_and_read(tmp_path):
    initialize_store(tmp_path)

    standalone_root = platform_baseline_service.ensure_platform_root("standalone")
    (standalone_root / "work" / "repo").mkdir(parents=True, exist_ok=True)
    (standalone_root / "work" / "repo" / "main.py").write_text("print('shared')\n", encoding="utf-8")

    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None
    session = conversation_service.bootstrap_admin_workbench(admin)
    assert session.workspace is not None

    session_workspace_sync_service.save_state(
        session.workspace,
        WorkspaceState(tombstones=("work/repo/main.py",)),
    )

    repo_entries = file_service.list(session, path="/workspace/work/repo")
    assert not any(item.name == "main.py" for item in repo_entries)

    import pytest

    with pytest.raises(FileNotFoundError):
        file_service.read_text(session, relative_path="work/repo/main.py")


def test_platform_skill_refreshes_for_existing_session(tmp_path):
    initialize_store(tmp_path)

    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None

    session = conversation_service.bootstrap_admin_workbench(admin)
    initial_names = {item.name for item in skill_service.list_for_session(session)}

    standalone_root = platform_baseline_service.ensure_platform_root("standalone")
    live_skill_dir = standalone_root / "skills" / "analysis-helper"
    live_skill_dir.mkdir(parents=True, exist_ok=True)
    (live_skill_dir / "SKILL.md").write_text(
        "---\nname: analysis-helper\ndescription: 会话内实时可见的平台技能\n---\n\n刷新列表后即可出现。\n",
        encoding="utf-8",
    )

    refreshed_names = {item.name for item in skill_service.list_for_session(session)}

    assert "analysis-helper" not in initial_names
    assert "analysis-helper" in refreshed_names


def test_platform_baseline_file_manager_operations(tmp_path):
    initialize_store(tmp_path)

    platform_root = platform_baseline_service.ensure_platform_root("standalone")
    (platform_root / "work" / "docs").mkdir(parents=True, exist_ok=True)

    created_dir = platform_baseline_service.create_directory(
        "standalone",
        PlatformBaselineDirectoryRequest(relative_path="work/docs/specs"),
    )
    assert created_dir.kind == "directory"

    created_file = platform_baseline_service.write_text(
        "standalone",
        PlatformBaselineWriteRequest(relative_path="work/docs/specs/readme.md", content="# hello"),
    )
    assert created_file.kind == "file"

    content = platform_baseline_service.read_text("standalone", relative_path="work/docs/specs/readme.md")
    assert content.content == "# hello"

    moved = platform_baseline_service.move_path(
        "standalone",
        PlatformBaselineMoveRequest(
            source_relative_path="work/docs/specs/readme.md",
            target_relative_path="work/docs/specs/guide.md",
        ),
    )
    assert moved.relative_path == "work/docs/specs/guide.md"

    platform_baseline_service.delete_file("standalone", relative_path="work/docs/specs/guide.md")
    assert not (platform_root / "work" / "docs" / "specs" / "guide.md").exists()

    import shutil

    shutil.rmtree(platform_root / "work" / "docs")
