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
    loaded = skill_service.invoke_skill(session, "excel-helper")
    injected = loaded["injected_messages"][0]["content"]
    assert "Base directory for this skill:" in injected
    assert "先检查文件，再执行分析。" in injected
    assert (session.workspace.skills_dir / "excel-helper" / "SKILL.md").exists()
