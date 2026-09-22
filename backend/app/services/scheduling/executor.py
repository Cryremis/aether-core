from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.services.agent_run_service import agent_run_service
from app.services.platform_baseline_service import platform_baseline_service
from app.services.scheduling.errors import ScheduleError
from app.services.scheduling.service import schedule_service
from app.services.session_service import session_service
from app.services.store import store_service


class ScheduleExecutor:
    """把定时触发转换为真实 Agent Run，并保证终态始终落库。"""

    async def execute(self, run: dict[str, Any]) -> None:
        run_id = str(run["run_id"])
        task = run.get("task") or store_service.get_schedule_task(str(run["task_id"]))
        agent_run_id: str | None = None
        session_id: str | None = None
        if task is None:
            store_service.finish_schedule_run(
                run_id, status="failed", error="定时任务不存在"
            )
            return
        try:
            session = self._prepare_session(task)
            if session is None:
                store_service.finish_schedule_run(
                    run_id,
                    status="skipped",
                    error="目标会话正在执行其他任务",
                    result_summary="按 skip 策略跳过本次触发",
                )
                return

            message = f"[定时任务：{task['title']}]\n\n{task['prompt']}"
            agent_run_id = await self._start_with_policy(session, message, task, run_id)
            session_id = session.session_id
            final_run = await self._wait_with_timeout(
                agent_run_id, int(task["timeout_seconds"])
            )
            status = str(final_run.get("status") or "failed")
            schedule_status = "succeeded" if status == "completed" else (
                "timed_out" if status == "timed_out" else "failed"
            )
            result_summary = self._result_summary(final_run)
            store_service.finish_schedule_run(
                run_id,
                status=schedule_status,
                session_id=session.session_id,
                conversation_id=session.conversation_id,
                agent_run_id=agent_run_id,
                error=None if schedule_status == "succeeded" else result_summary,
                result_summary=result_summary,
            )
        except asyncio.CancelledError:
            if agent_run_id and session_id:
                session = session_service.get_or_create(session_id)
                await agent_run_service.cancel_run(session, agent_run_id)
            store_service.finish_schedule_run(
                run_id,
                status="cancelled",
                error="调度器停止，执行已取消",
            )
            raise
        except Exception as exc:  # noqa: BLE001 - 后台执行必须捕获所有异常并落终态
            store_service.finish_schedule_run(
                run_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )

    def _prepare_session(self, task: dict[str, Any]) -> Any | None:
        if str(task["target_mode"]) == "existing_session":
            session = session_service.get_or_create(str(task["target_session_id"]))
            conversation = store_service.get_conversation_by_session(session.session_id)
            if conversation is not None:
                session.conversation_id = str(conversation["conversation_id"])
            if store_service.get_active_agent_run_for_session(session.session_id) is not None:
                return None if str(task["concurrency_policy"]) == "skip" else session
            return session

        # 每次执行创建独立会话和工作区，避免上下文与文件互相污染。
        session = session_service.get_or_create()

        owner_user_id = task.get("owner_user_id")
        platform_id = task.get("platform_id")
        external_user_id = task.get("external_user_id")
        host_name = "AetherCore"
        if platform_id is not None:
            platform = store_service.get_platform_by_id(int(platform_id))
            host_name = str(platform["display_name"]) if platform else host_name
        title_prefix = str(task.get("new_session_title_prefix") or task["title"])
        conversation = store_service.create_conversation(
            session_id=session.session_id,
            workspace_id=parent_workspace_id,
            title=f"{title_prefix} · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
            host_name=host_name,
            platform_id=platform_id,
            owner_user_id=owner_user_id,
            external_user_id=external_user_id,
            external_org_id=task.get("external_org_id"),
        )
        session.conversation_id = str(conversation["conversation_id"])
        session.owner_user_id = owner_user_id
        session.platform_id = platform_id
        session.external_user_id = external_user_id
        session.host_name = host_name
        if platform_id is not None:
            platform = store_service.get_platform_by_id(int(platform_id))
            if platform is not None:
                platform_baseline_service.materialize_to_session(
                    str(platform["platform_key"]), session
                )
        elif owner_user_id is not None:
            platform_baseline_service.materialize_to_session("standalone", session)
        session_service.persist(session)
        return session

    async def _start_with_policy(
        self, session: Any, message: str, task: dict[str, Any], run_id: str
    ) -> str:
        timeout = int(task["timeout_seconds"])
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            try:
                return await agent_run_service.start_chat_run(
                    session,
                    message,
                    idempotency_key=f"schedule:{task['task_id']}:{run_id}",
                )
            except RuntimeError as exc:
                if "当前会话已有执行中的任务" not in str(exc):
                    raise
                if str(task["concurrency_policy"]) != "queue":
                    raise ScheduleError("目标会话忙碌，任务被跳过") from exc
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("等待目标会话空闲超时") from exc
                await asyncio.sleep(min(1.0, max(0.1, timeout / 30)))

    async def _wait_with_timeout(self, agent_run_id: str, timeout_seconds: int) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                agent_run_service.wait_for_run(agent_run_id), timeout=timeout_seconds
            )
        except TimeoutError as exc:
            run = store_service.get_agent_run(agent_run_id) or {}
            if run:
                session = session_service.get_or_create(str(run["session_id"]))
                await agent_run_service.cancel_run(session, agent_run_id)
            final_run = store_service.get_agent_run(agent_run_id) or {}
            if str(final_run.get("status")) in {"failed", "cancelled", "timed_out"}:
                return {**final_run, "status": "timed_out"}
            raise TimeoutError("Agent 执行超时") from exc

    @staticmethod
    def _result_summary(run: dict[str, Any]) -> str:
        result = run.get("result_json") or {}
        if isinstance(result, str):
            import json

            try:
                result = json.loads(result)
            except ValueError:
                result = {}
        content = result.get("content") or result.get("message") or result.get("summary")
        text = str(content or "")
        return text[:500] or str(run.get("status") or "")
