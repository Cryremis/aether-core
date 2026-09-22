from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import settings
from app.services.scheduling.executor import ScheduleExecutor
from app.services.scheduling.recurrence import next_occurrence
from app.services.store import store_service


logger = logging.getLogger(__name__)


class ScheduleScheduler:
    """持久化任务调度器。触发推进与 run 生成在 SQLite 事务内完成。"""

    def __init__(self) -> None:
        self._executor = ScheduleExecutor()
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._tasks: list[asyncio.Task[Any]] = []
        self._running = False
        self._last_tick_at: datetime | None = None

    @property
    def last_tick_at(self) -> datetime | None:
        return self._last_tick_at

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        recovered = store_service.recover_expired_schedule_run_leases()
        if recovered:
            logger.warning("恢复 %d 个过期的定时任务执行租约", recovered)
        self._tasks = [
            asyncio.create_task(self._dispatcher(), name="schedule-dispatcher"),
            *(
                asyncio.create_task(
                    self._worker(worker_index), name=f"schedule-worker-{worker_index}"
                )
                for worker_index in range(settings.scheduler_workers)
            ),
        ]

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def tick_once(self) -> int:
        now = datetime.now(timezone.utc)
        created = store_service.materialize_due_schedule_tasks(
            now_iso=now.isoformat(),
            limit=500,
            build_plan=self._build_plan,
        )
        self._last_tick_at = now
        for run in created:
            await self._queue.put(run)
        return len(created)

    async def health(self) -> dict[str, Any]:
        return {
            "enabled": settings.scheduler_enabled,
            "running": self._running,
            "last_tick_at": self._last_tick_at.isoformat() if self._last_tick_at else None,
            "queued": self._queue.qsize(),
        }

    def _build_plan(self, task: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        due = self._parse(task.get("next_run_at"))
        if due is None:
            return {"occurrences": [], "run_count_delta": 0}
        schedule = task.get("schedule") or {}
        timezone_name = str(task["timezone"])
        starts_at = self._parse(task.get("starts_at")) or due
        ends_at = self._parse(task.get("ends_at"))
        max_runs = int(task["max_runs"]) if task.get("max_runs") is not None else None
        remaining = max_runs - int(task["run_count"]) if max_runs is not None else None
        if remaining is not None and remaining <= 0:
            return {
                "occurrences": [],
                "run_count_delta": 0,
                "status": "completed",
                "next_run_at": None,
            }

        policy = str(task.get("missed_run_policy") or "skip")
        occurrences: list[datetime] = []
        cursor = due
        if policy == "catch_up":
            while cursor <= now and (remaining is None or len(occurrences) < remaining):
                occurrences.append(cursor)
                cursor = next_occurrence(
                    schedule, after=cursor, timezone_name=timezone_name
                )
        else:
            occurrences.append(due)
            cursor = (
                next_occurrence(schedule, after=now, timezone_name=timezone_name)
                if policy in {"skip", "run_once"}
                else next_occurrence(schedule, after=due, timezone_name=timezone_name)
            )

        if ends_at is not None:
            occurrences = [item for item in occurrences if item <= ends_at]
            if cursor > ends_at:
                cursor_dt = None
            else:
                cursor_dt = cursor
        else:
            cursor_dt = cursor

        if remaining is not None:
            occurrences = occurrences[:remaining]
        if not occurrences:
            return {
                "occurrences": [],
                "run_count_delta": 0,
                "status": "expired",
                "next_run_at": None,
            }

        if max_runs is not None and int(task["run_count"]) + len(occurrences) >= max_runs:
            next_run_at = None
            status = "completed"
        else:
            next_run_at = cursor_dt.isoformat() if cursor_dt else None
            status = "expired" if cursor_dt is None else None
        return {
            "occurrences": [item.isoformat() for item in occurrences],
            "run_count_delta": len(occurrences),
            "next_run_at": next_run_at,
            "status": status,
        }

    async def _dispatcher(self) -> None:
        while self._running:
            try:
                await self.tick_once()
            except Exception:  # noqa: BLE001 - 调度循环不能因单次异常退出
                logger.exception("定时任务调度 tick 失败")
            await asyncio.sleep(settings.scheduler_tick_seconds)

    async def _worker(self, worker_index: int) -> None:
        while self._running:
            try:
                lease_owner = f"{uuid.uuid4().hex}:{worker_index}"
                lease_expires_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=settings.scheduler_lease_seconds)
                ).isoformat()
                run = store_service.claim_next_schedule_run(
                    lease_owner=lease_owner,
                    lease_expires_at=lease_expires_at,
                )
                if run is not None:
                    await self._executor.execute(run)
                else:
                    await asyncio.sleep(min(0.5, settings.scheduler_tick_seconds))
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001 - worker 必须持续服务后续任务
                logger.exception("定时任务 worker 执行失败")

    @staticmethod
    def _parse(value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)


schedule_scheduler = ScheduleScheduler()
