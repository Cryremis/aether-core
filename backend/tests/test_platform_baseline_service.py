# backend/tests/test_platform_baseline_service.py
from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.services.conversation_service import conversation_service
from app.services.file_service import file_service
from app.services.platform_baseline_service import platform_baseline_service
from app.services.session_service import session_service
from app.services.skill_service import skill_service
from app.services.store import store_service
from app.schemas.platform import (
    PlatformBaselineDirectoryRequest,
    PlatformBaselineMoveRequest,
    PlatformBaselineWriteRequest,
)


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
    (standalone_root / "input" / "guide.txt").write_text("baseline guide", encoding="utf-8")
    skill_dir = standalone_root / "skills" / "analysis-helper"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "# backend/tests/test_platform_baseline_service.py\n---\nname: analysis-helper\ndescription: 分析辅助技能\n---\n\n先读取文件，再分析。\n",
        encoding="utf-8",
    )

    admin = store_service.get_user_by_username(settings.auth_system_admin_username)
    assert admin is not None

    session = conversation_service.bootstrap_admin_workbench(admin)
    files = file_service.list_platform_files(session)
    skills = skill_service.list_for_session(session)

    assert any(item.name == "guide.txt" for item in files)
    assert any(item.name == "analysis-helper" and item.source == "platform" for item in skills)


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
    assert file_service.read_text(session, relative_path="work/repo/main.py").strip() == "print('hello')"


def test_platform_baseline_file_manager_operations(tmp_path):
    initialize_store(tmp_path)

    platform_root = platform_baseline_service.ensure_platform_root("standalone")
    (platform_root / "input" / "docs").mkdir(parents=True, exist_ok=True)

    created_dir = platform_baseline_service.create_directory(
        "standalone",
        PlatformBaselineDirectoryRequest(relative_path="input/docs/specs"),
    )
    assert created_dir.kind == "directory"

    created_file = platform_baseline_service.write_text(
        "standalone",
        PlatformBaselineWriteRequest(relative_path="input/docs/specs/readme.md", content="# hello"),
    )
    assert created_file.kind == "file"

    content = platform_baseline_service.read_text("standalone", relative_path="input/docs/specs/readme.md")
    assert content.content == "# hello"

    moved = platform_baseline_service.move_path(
        "standalone",
        PlatformBaselineMoveRequest(
            source_relative_path="input/docs/specs/readme.md",
            target_relative_path="input/docs/specs/guide.md",
        ),
    )
    assert moved.relative_path == "input/docs/specs/guide.md"

    platform_baseline_service.delete_file("standalone", relative_path="input/docs/specs/guide.md")
    assert not (platform_root / "input" / "docs" / "specs" / "guide.md").exists()
