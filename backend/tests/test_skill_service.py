# backend/tests/test_skill_service.py
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.core.config import settings
from app.services.session_service import session_service
from app.services.skill_service import skill_service
from app.services.store import store_service


def initialize_store(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    store_service._db_path = storage_root / "aethercore-test.db"
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.storage_root = storage_root
    session_service._sessions.clear()
    store_service.initialize()
    skill_service.ensure_built_in_layout()


def test_install_skill_upload_from_zip_and_invoke(tmp_path):
    initialize_store(tmp_path)

    session = session_service.get_or_create("sess_skill_zip")
    assert session.workspace is not None

    raw_zip = io.BytesIO()
    with zipfile.ZipFile(raw_zip, "w") as archive:
        archive.writestr(
            "excel-helper/SKILL.md",
            "---\nname: excel-helper\ndescription: 处理 Excel 与表格文件\ntags:\n  - upload\n---\n\n先检查文件，再执行分析。\n",
        )
        archive.writestr("excel-helper/references/readme.txt", "extra")

    cards = skill_service.install_skill_upload(
        session,
        filename="excel-helper.zip",
        raw_bytes=raw_zip.getvalue(),
    )

    assert any(item.name == "excel-helper" for item in cards)
    assert any(item.description == "处理 Excel 与表格文件" for item in cards)
    loaded = skill_service.invoke_skill(session, "excel-helper")
    injected = loaded["injected_messages"][0]["content"]
    assert "Base directory for this skill:" in injected
    assert "先检查文件，再执行分析。" in injected
    assert (session.workspace.skills_dir / "excel-helper" / "SKILL.md").exists()


def test_built_in_skills_refresh_when_disk_changes(tmp_path):
    initialize_store(tmp_path)

    session = session_service.get_or_create("sess_builtin_refresh")
    initial_names = {item.name for item in skill_service.list_for_session(session)}

    built_in_dir = settings.resolved_storage_root / "built_in_skills" / "live-helper"
    built_in_dir.mkdir(parents=True, exist_ok=True)
    (built_in_dir / "SKILL.md").write_text(
        "---\nname: live-helper\ndescription: 实时刷新的内置技能\n---\n\n在列表刷新时自动出现。\n",
        encoding="utf-8",
    )

    refreshed_names = {item.name for item in skill_service.list_for_session(session)}

    assert "live-helper" not in initial_names
    assert "live-helper" in refreshed_names
