from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.schemas.schedule import (
    ScheduleCreateRequest,
    ScheduleExecutionPolicy,
    ScheduleStatus,
    ScheduleTargetMode,
    ScheduleTaskView,
    ScheduleUpdateRequest,
)
from app.services.scheduling.errors import (
    ScheduleConflictError,
    ScheduleError,
    SchedulePermissionError,
)
from app.services.scheduling.models import ScheduleIdentity
from app.services.scheduling.recurrence import next_occurrence, preview_next_runs
from app.services.store import store_service


ACTIVE_COUNT_STATUSES = ("active", "pending_approval")


class ScheduleService:
    """定时任务领域服务：统一 API 与 Agent 工具的业务和权限边界。"""

    def identity_from_session(self, session: Any) -> ScheduleIdentity:
        return ScheduleIdentity(
            owner_user_id=session.owner_user_id,
            platform_id=session.platform_id,
            external_user_id=session.external_user_id,
        )

    def create(
        self,
        request: ScheduleCreateRequest,
        *,
        identity: ScheduleIdentity,
        created_via: str,
        created_session_id: str | None = None,
        created_by_user_id: int | None = None,
    ) -> ScheduleTaskView:
        if identity.is_empty:
            raise SchedulePermissionError("无法识别任务归属身份")
        active_count = sum(
            store_service.count_schedule_tasks(
                owner_user_id=identity.owner_user_id,
                platform_id=identity.platform_id,
                external_user_id=identity.external_user_id,
                status=status,
            )
            for status in ACTIVE_COUNT_STATUSES
        )
        if active_count >= settings.schedule_max_per_owner:
            raise ScheduleError("当前身份的活跃定时任务数量已达上限")

        self._validate_target(request.target, identity)
        now = datetime.now(timezone.utc)
        starts_at = self._to_utc_datetime(request.starts_at) or now
        ends_at = self._to_utc_datetime(request.ends_at)
        if ends_at is not None and ends_at <= starts_at:
            raise ScheduleError("结束时间必须晚于开始时间")
        if ends_at is not None and ends_at <= now:
            raise ScheduleError("结束时间必须晚于当前时间")

        schedule = request.schedule.model_dump(mode="json")
        if request.schedule.type == "interval":
            every_seconds = int(request.schedule.every_seconds)
            if every_seconds < settings.schedule_min_interval_seconds:
                raise ScheduleError(
                    f"最小间隔为 {settings.schedule_min_interval_seconds} 秒"
                )
        next_run_at = next_occurrence(
            schedule,
            after=max(now, starts_at),
            timezone_name=request.timezone,
        )
        if ends_at is not None and next_run_at > ends_at:
            raise ScheduleError("首次运行时间晚于结束时间")

        status = ScheduleStatus.ACTIVE
        if created_via == "agent" and settings.agent_schedules_require_approval:
            status = ScheduleStatus.PENDING_APPROVAL
        task_id = f"sch_{uuid.uuid4().hex}"
        now_iso = now.isoformat()
        row = store_service.create_schedule_task(
            {
                "task_id": task_id,
                "title": request.title.strip(),
                "owner_user_id": identity.owner_user_id,
                "platform_id": identity.platform_id,
                "external_user_id": identity.external_user_id,
                "external_org_id": identity.external_org_id,
                "prompt": request.prompt.strip(),
                "target_mode": request.target.mode.value,
                "target_session_id": request.target.session_id,
                "target_conversation_id": None,
                "new_session_title_prefix": request.target.new_session_title_prefix,
                "workspace_policy": request.target.workspace_policy,
                "schedule_json": self._schedule_json(schedule),
                "schedule": schedule,
                "timezone": request.timezone,
                "starts_at": starts_at.isoformat(),
                "ends_at": ends_at.isoformat() if ends_at else None,
                "next_run_at": next_run_at.isoformat(),
                "timeout_seconds": request.execution.timeout_seconds,
                "concurrency_policy": request.execution.concurrency_policy.value,
                "missed_run_policy": request.execution.missed_run_policy.value,
                "max_runs": request.execution.max_runs,
                "run_count": 0,
                "status": status.value,
                "created_via": created_via,
                "created_session_id": created_session_id,
                "created_by_user_id": created_by_user_id,
                "revision": 1,
                "created_at": now_iso,
                "updated_at": now_iso,
            }
        )
        return self.to_view(row)

    def list(
        self,
        *,
        identity: ScheduleIdentity,
        status: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ScheduleTaskView], int]:
        if identity.is_empty:
            raise SchedulePermissionError("无法识别任务归属身份")
        rows = store_service.list_schedule_tasks(
            owner_user_id=identity.owner_user_id,
            platform_id=identity.platform_id,
            external_user_id=identity.external_user_id,
            status=status,
            search=search,
            limit=limit,
            offset=offset,
        )
        total = store_service.count_schedule_tasks(
            owner_user_id=identity.owner_user_id,
            platform_id=identity.platform_id,
            external_user_id=identity.external_user_id,
            status=status,
        )
        return [self.to_view(row) for row in rows], total

    def get(self, task_id: str, *, identity: ScheduleIdentity) -> ScheduleTaskView:
        row = self._authorized_row(task_id, identity)
        return self.to_view(row)

    def update(
        self,
        task_id: str,
        request: ScheduleUpdateRequest,
        *,
        identity: ScheduleIdentity,
    ) -> ScheduleTaskView:
        current = self._authorized_row(task_id, identity)
        if str(current["status"]) == ScheduleStatus.ARCHIVED.value:
            raise ScheduleError("已归档任务不能修改")

        updates: dict[str, Any] = {}
        if request.title is not None:
            updates["title"] = request.title.strip()
        if request.prompt is not None:
            updates["prompt"] = request.prompt.strip()
        if request.target is not None:
            self._validate_target(request.target, identity)
            updates.update(
                {
                    "target_mode": request.target.mode.value,
                    "target_session_id": request.target.session_id,
                    "target_conversation_id": None,
                    "new_session_title_prefix": request.target.new_session_title_prefix,
                    "workspace_policy": request.target.workspace_policy,
                }
            )

        schedule_changed = request.schedule is not None or request.timezone is not None
        schedule = (
            request.schedule.model_dump(mode="json")
            if request.schedule is not None
            else current["schedule"]
        )
        timezone_name = request.timezone or str(current["timezone"])
        if request.schedule is not None and schedule.get("type") == "interval":
            if int(schedule["every_seconds"]) < settings.schedule_min_interval_seconds:
                raise ScheduleError(
                    f"最小间隔为 {settings.schedule_min_interval_seconds} 秒"
                )
        if request.starts_at is not None:
            updates["starts_at"] = self._to_utc_datetime(request.starts_at).isoformat()
        if request.ends_at is not None:
            updates["ends_at"] = self._to_utc_datetime(request.ends_at).isoformat()

        now = datetime.now(timezone.utc)
        starts_at = self._parse_datetime(
            updates.get("starts_at", current.get("starts_at")), now
        )
        ends_at = self._parse_datetime(updates.get("ends_at", current.get("ends_at")))
        if ends_at is not None and ends_at <= max(now, starts_at):
            raise ScheduleError("结束时间必须晚于开始时间和当前时间")

        if schedule_changed or request.starts_at is not None or request.ends_at is not None:
            next_run_at = next_occurrence(
                schedule,
                after=max(now, starts_at),
                timezone_name=timezone_name,
            )
            if ends_at is not None and next_run_at > ends_at:
                raise ScheduleError("首次运行时间晚于结束时间")
            updates["schedule_json"] = self._schedule_json(schedule)
            updates["timezone"] = timezone_name
            updates["next_run_at"] = next_run_at.isoformat()

        if request.execution is not None:
            updates.update(
                {
                    "timeout_seconds": request.execution.timeout_seconds,
                    "concurrency_policy": request.execution.concurrency_policy.value,
                    "missed_run_policy": request.execution.missed_run_policy.value,
                    "max_runs": request.execution.max_runs,
                }
            )
        updated = store_service.update_schedule_task(
            task_id,
            updates,
            expected_revision=request.expected_revision,
        )
        if updated is None:
            raise ScheduleConflictError("任务已被其他操作修改，请刷新后重试")
        return self.to_view(updated)

    def pause(self, task_id: str, *, identity: ScheduleIdentity) -> ScheduleTaskView:
        row = self._authorized_row(task_id, identity)
        if str(row["status"]) not in {ScheduleStatus.ACTIVE.value, ScheduleStatus.PENDING_APPROVAL.value}:
            raise ScheduleError("只有待确认或运行中的任务可以暂停")
        updated = store_service.set_schedule_task_status(
            task_id, ScheduleStatus.PAUSED.value
        )
        if updated is None:
            raise ScheduleError("任务不存在")
        return self.to_view(updated)

    def resume(self, task_id: str, *, identity: ScheduleIdentity) -> ScheduleTaskView:
        row = self._authorized_row(task_id, identity)
        if str(row["status"]) != ScheduleStatus.PAUSED.value:
            raise ScheduleError("只有已暂停的任务可以恢复")
        now = datetime.now(timezone.utc)
        starts_at = self._parse_datetime(row.get("starts_at"), now)
        ends_at = self._parse_datetime(row.get("ends_at"))
        next_run_at = next_occurrence(
            row["schedule"], after=max(now, starts_at), timezone_name=str(row["timezone"])
        )
        if ends_at is not None and next_run_at > ends_at:
            next_run_at = None
            status = ScheduleStatus.EXPIRED.value
        else:
            status = ScheduleStatus.ACTIVE.value
        updated = store_service.update_schedule_task(
            task_id,
            {
                "status": status,
                "next_run_at": next_run_at.isoformat() if next_run_at else None,
            },
            expected_revision=int(row["revision"]),
        )
        if updated is None:
            raise ScheduleConflictError("任务已被其他操作修改，请刷新后重试")
        return self.to_view(updated)

    def approve(self, task_id: str, *, identity: ScheduleIdentity) -> ScheduleTaskView:
        row = self._authorized_row(task_id, identity)
        if str(row["status"]) != ScheduleStatus.PENDING_APPROVAL.value:
            raise ScheduleError("任务当前不需要审批")
        now = datetime.now(timezone.utc)
        starts_at = self._parse_datetime(row.get("starts_at"), now)
        ends_at = self._parse_datetime(row.get("ends_at"))
        next_run_at = next_occurrence(
            row["schedule"], after=max(now, starts_at), timezone_name=str(row["timezone"])
        )
        if ends_at is not None and next_run_at > ends_at:
            raise ScheduleError("审批后首次运行时间晚于结束时间")
        updated = store_service.update_schedule_task(
            task_id,
            {"status": ScheduleStatus.ACTIVE.value, "next_run_at": next_run_at.isoformat()},
            expected_revision=int(row["revision"]),
        )
        if updated is None:
            raise ScheduleConflictError("任务已被其他操作修改，请刷新后重试")
        return self.to_view(updated)

    def reject(self, task_id: str, *, identity: ScheduleIdentity) -> ScheduleTaskView:
        row = self._authorized_row(task_id, identity)
        if str(row["status"]) != ScheduleStatus.PENDING_APPROVAL.value:
            raise ScheduleError("任务当前不需要审批")
        updated = store_service.set_schedule_task_status(
            task_id, ScheduleStatus.ARCHIVED.value, reason="用户拒绝 Agent 创建的定时任务"
        )
        if updated is None:
            raise ScheduleError("任务不存在")
        return self.to_view(updated)

    def archive(self, task_id: str, *, identity: ScheduleIdentity) -> ScheduleTaskView:
        self._authorized_row(task_id, identity)
        updated = store_service.set_schedule_task_status(
            task_id, ScheduleStatus.ARCHIVED.value
        )
        if updated is None:
            raise ScheduleError("任务不存在")
        return self.to_view(updated)

    def create_manual_run(
        self,
        task_id: str,
        *,
        identity: ScheduleIdentity,
    ) -> dict[str, Any]:
        row = self._authorized_row(task_id, identity)
        if str(row["status"]) in {ScheduleStatus.ARCHIVED.value, ScheduleStatus.COMPLETED.value, ScheduleStatus.EXPIRED.value}:
            raise ScheduleError("任务已结束，不能手动运行")
        now = datetime.now(timezone.utc)
        run = store_service.create_schedule_run(
            task_id=task_id,
            scheduled_for=now.isoformat(),
            trigger_at=now.isoformat(),
        )
        return run

    def list_runs(
        self,
        task_id: str,
        *,
        identity: ScheduleIdentity,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        self._authorized_row(task_id, identity)
        return store_service.list_schedule_task_runs(task_id, limit=limit, offset=offset)

    def get_run(self, run_id: str, *, identity: ScheduleIdentity) -> dict[str, Any]:
        run = store_service.get_schedule_run(run_id)
        if run is None:
            raise LookupError("执行记录不存在")
        self._authorized_row(str(run["task_id"]), identity)
        return run

    def to_view(self, row: dict[str, Any]) -> ScheduleTaskView:
        now = datetime.now(timezone.utc)
        schedule = row.get("schedule") or {}
        next_runs = []
        if row.get("status") == ScheduleStatus.ACTIVE.value and row.get("next_run_at"):
            next_runs = preview_next_runs(
                schedule,
                after=self._parse_datetime(row["next_run_at"], now),
                timezone_name=str(row["timezone"]),
                count=3,
            )
        execution = ScheduleExecutionPolicy(
            timeout_seconds=int(row["timeout_seconds"]),
            concurrency_policy=str(row["concurrency_policy"]),
            missed_run_policy=str(row["missed_run_policy"]),
            max_runs=int(row["max_runs"]) if row.get("max_runs") is not None else None,
        )
        payload = {
            **row,
            "status": row["status"],
            "target_mode": row["target_mode"],
            "schedule": schedule,
            "execution": execution,
            "next_runs": next_runs,
        }
        return ScheduleTaskView.model_validate(payload)

    def _authorized_row(self, task_id: str, identity: ScheduleIdentity) -> dict[str, Any]:
        row = store_service.get_schedule_task(task_id)
        if row is None or not identity.matches(row):
            raise SchedulePermissionError("定时任务不存在或无权访问")
        return row

    def _validate_target(self, target: Any, identity: ScheduleIdentity) -> None:
        if target.mode == ScheduleTargetMode.NEW_SESSION_PER_RUN:
            if not target.new_session_title_prefix:
                return
            if len(target.new_session_title_prefix.strip()) < 1:
                raise ScheduleError("新会话标题前缀不能为空白")
            return
        session_id = str(target.session_id or "").strip()
        if not session_id:
            raise ScheduleError("固定会话任务必须提供 session_id")
        conversation = store_service.get_conversation_by_session(session_id)
        if conversation is None or conversation.get("deleted_at") is not None:
            raise ScheduleError("目标会话不存在")
        if not identity.matches(conversation):
            raise SchedulePermissionError("无权将定时任务绑定到目标会话")

    @staticmethod
    def _schedule_json(schedule: dict[str, Any]) -> str:
        import json

        return json.dumps(schedule, ensure_ascii=False)

    @staticmethod
    def _to_utc_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.astimezone(timezone.utc)

    @staticmethod
    def _parse_datetime(value: Any, default: datetime | None = None) -> datetime | None:
        if value in (None, ""):
            return default
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)


schedule_service = ScheduleService()
