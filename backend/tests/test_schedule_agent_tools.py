from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.config import settings
from app.services.session_types import AgentSession
from app.services.store import store_service
from app.services.tool_service import tool_service


def initialize_store(tmp_path: Path) -> None:
    settings.storage_root = tmp_path / "storage"
    store_service._db_path = settings.metadata_db_path  # noqa: SLF001
    store_service._db_path.parent.mkdir(parents=True, exist_ok=True)
    store_service.initialize()
    settings.agent_schedules_require_approval = True


def test_schedule_tools_are_registered() -> None:
    for name in (
        "schedule_create",
        "schedule_list",
        "schedule_update",
        "schedule_pause",
        "schedule_resume",
        "schedule_delete",
    ):
        assert tool_service._registry.get_handler(name) is not None  # noqa: SLF001


def test_agent_create_tool_uses_session_identity_and_requires_approval(tmp_path) -> None:
    initialize_store(tmp_path)
    session = AgentSession(session_id="sess_tool", owner_user_id=7)
    handler = tool_service._registry.get_handler("schedule_create")  # noqa: SLF001
    assert handler is not None

    result = asyncio.run(
        handler(
            session,
            {
                "title": "每日检查",
                "prompt": "检查服务状态",
                "target_mode": "new_session_per_run",
                "schedule": {"type": "daily", "time": "09:00"},
                "timezone": "Asia/Shanghai",
            },
        )
    )

    assert result.status == "success"
    assert result.data["status"] == "pending_approval"
    row = store_service.get_schedule_task(str(result.data["task_id"]))
    assert row is not None
    assert row["owner_user_id"] == 7
    assert row["created_via"] == "agent"
    assert row["created_session_id"] == "sess_tool"


def test_agent_list_tool_only_returns_current_identity(tmp_path) -> None:
    initialize_store(tmp_path)
    handler = tool_service._registry.get_handler("schedule_list")  # noqa: SLF001
    assert handler is not None

    first = asyncio.run(
        tool_service._registry.get_handler("schedule_create")(  # noqa: SLF001
            AgentSession(session_id="sess_one", owner_user_id=1),
            {
                "title": "用户一任务",
                "prompt": "执行",
                "target_mode": "new_session_per_run",
                "schedule": {"type": "daily", "time": "09:00"},
            },
        )
    )
    asyncio.run(
        tool_service._registry.get_handler("schedule_create")(  # noqa: SLF001
            AgentSession(session_id="sess_two", owner_user_id=2),
            {
                "title": "用户二任务",
                "prompt": "执行",
                "target_mode": "new_session_per_run",
                "schedule": {"type": "daily", "time": "10:00"},
            },
        )
    )

    result = asyncio.run(
        handler(AgentSession(session_id="sess_one", owner_user_id=1), {})
    )

    assert result.status == "success"
    assert result.data["total"] == 1
    assert result.data["items"][0]["task_id"] == first.data["task_id"]
