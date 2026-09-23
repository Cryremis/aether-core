from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from app.schemas.agent import AgentEvent
from app.services.agent_run_service import agent_run_service
from app.services.context.message_adapter import context_message_adapter
from app.services.session_service import session_service
from app.services.session_types import AgentSession
from app.services.store import store_service


class SubagentService:
    """一层 Subagent 编排服务。

    子 Agent 拥有稳定实体 ID 与独立上下文；每轮沟通对应一个 latest run，
    并与父 Agent 共享 Workspace。子 Agent 不获得 subagent 工具。
    """

    def __init__(self) -> None:
        self._watchers: set[asyncio.Task[None]] = set()
        self._futures: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def execute_tool(
        self,
        parent_session: AgentSession,
        arguments: dict[str, Any],
        *,
        parent_run_id: str | None,
        tool_name: str,
    ) -> dict[str, Any]:
        if not parent_run_id:
            raise RuntimeError("subagent tools require an active parent run")
        if not parent_session.subagent_tools_enabled:
            raise RuntimeError("subagents cannot create nested subagents")

        if tool_name == "subagent_create":
            return await self.create_subagent(
                parent_session,
                parent_run_id=parent_run_id,
                name=str(arguments["name"]),
                task=str(arguments["task"]),
                instructions=str(arguments.get("instructions") or ""),
                allowed_tools=[str(item) for item in arguments.get("allowed_tools", [])],
            )
        if tool_name == "subagent_send_message":
            return await self.send_message(
                parent_session,
                parent_run_id=parent_run_id,
                subagent_id=str(arguments["subagent_id"]),
                message=str(arguments["message"]),
            )
        if tool_name == "subagent_wait":
            return await self.wait(
                parent_session,
                subagent_id=str(arguments.get("subagent_id") or "") or None,
                timeout_seconds=arguments.get("timeout_seconds"),
            )
        if tool_name == "subagent_list":
            return {"subagents": self.list_subagents(parent_session)}
        if tool_name == "subagent_cancel" and "subagent_id" in arguments:
            return await self.cancel(parent_session, str(arguments["subagent_id"]))
        if tool_name == "subagent_get_result" and "subagent_id" in arguments:
            return self.get_result(parent_session, str(arguments["subagent_id"]))

        raise RuntimeError("未知 subagent 工具")

    async def create_subagent(
        self,
        parent_session: AgentSession,
        *,
        parent_run_id: str,
        name: str,
        task: str,
        instructions: str = "",
        allowed_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        child_session = self._create_child_session(
            parent_session,
            name=name,
            allowed_tools=allowed_tools,
        )
        full_task = task
        if instructions:
            full_task = f"{task}\n\nAdditional instructions:\n{instructions}"

        subagent_id = f"subagent_{uuid.uuid4().hex}"
        latest_run_id = await agent_run_service.start_chat_run(
            child_session,
            full_task,
            parent_run_id=parent_run_id,
            agent_kind="subagent",
        )
        row = store_service.create_subagent(
            subagent_id=subagent_id,
            latest_run_id=latest_run_id,
            workspace_id=parent_session.workspace_id,
            parent_run_id=parent_run_id,
            parent_session_id=parent_session.session_id,
            child_session_id=child_session.session_id,
            name=name,
            task=task,
        )
        self._start_watcher(
            parent_run_id=parent_run_id,
            parent_session_id=parent_session.session_id,
            subagent_id=subagent_id,
            latest_run_id=latest_run_id,
            name=name,
        )
        await self._publish_parent_event(
            parent_session,
            parent_run_id,
            "subagent_created",
            subagent_id=subagent_id,
            latest_run_id=latest_run_id,
            child_session_id=child_session.session_id,
            name=name,
            task=task,
        )
        await self._publish_parent_event(
            parent_session,
            parent_run_id,
            "subagent_started",
            subagent_id=subagent_id,
            latest_run_id=latest_run_id,
            child_session_id=child_session.session_id,
            name=name,
        )
        return self._public_row(store_service.get_subagent(subagent_id) or row)

    async def send_message(
        self,
        parent_session: AgentSession,
        *,
        parent_run_id: str,
        subagent_id: str,
        message: str,
    ) -> dict[str, Any]:
        row = self._owned_row(parent_session, subagent_id, include_destroyed=True)
        if row.get("destroyed_at"):
            raise RuntimeError("子 Agent 已销毁，不能继续发送消息")

        latest_run_id = str(row["latest_run_id"])
        latest_run = store_service.get_agent_run(latest_run_id)
        if latest_run and str(latest_run.get("status")) in {"queued", "running", "waiting_input"}:
            raise RuntimeError("子 Agent 正在执行，请先等待当前任务完成")

        child_session = session_service.get_or_create(str(row["child_session_id"]))
        new_run_id = await agent_run_service.start_chat_run(
            child_session,
            message,
            parent_run_id=parent_run_id,
            agent_kind="subagent",
        )
        store_service.set_subagent_latest_run(subagent_id, new_run_id)
        self._start_watcher(
            parent_run_id=parent_run_id,
            parent_session_id=parent_session.session_id,
            subagent_id=subagent_id,
            latest_run_id=new_run_id,
            name=str(row["name"]),
        )
        await self._publish_parent_event(
            parent_session,
            parent_run_id,
            "subagent_status_changed",
            subagent_id=subagent_id,
            latest_run_id=new_run_id,
            child_session_id=str(row["child_session_id"]),
            status="running",
            reason="follow_up_message",
        )
        updated = store_service.get_subagent(subagent_id) or row
        return self._public_row(updated)

    async def wait(
        self,
        parent_session: AgentSession,
        *,
        subagent_id: str | None,
        timeout_seconds: Any = None,
    ) -> dict[str, Any]:
        rows = (
            [self._owned_row(parent_session, subagent_id)]
            if subagent_id
            else [
                self._owned_row(parent_session, str(row["subagent_id"]))
                for row in store_service.list_subagents_for_session(parent_session.session_id)
            ]
        )
        if not rows:
            return {"subagents": [], "status": "completed"}

        try:
            timeout = float(timeout_seconds) if timeout_seconds is not None else None
        except (TypeError, ValueError):
            timeout = None

        async def wait_one(row: dict[str, Any]) -> dict[str, Any]:
            latest_run_id = str(row["latest_run_id"])
            run = store_service.get_agent_run(latest_run_id)
            if run is None:
                raise RuntimeError("子 Agent 运行记录不存在")
            if str(run.get("status")) not in {"queued", "running", "waiting_input"}:
                return self._public_row(store_service.get_subagent(str(row["subagent_id"])) or row)

            future = self._futures.get(str(row["subagent_id"]))
            if future is not None and not future.done():
                await future
            else:
                # 进程重启后没有内存 watcher，则按持久状态短轮询恢复。
                while True:
                    current = store_service.get_agent_run(latest_run_id)
                    if current is None or str(current.get("status")) not in {
                        "queued",
                        "running",
                        "waiting_input",
                    }:
                        break
                    await asyncio.sleep(0.1)
            return self._public_row(store_service.get_subagent(str(row["subagent_id"])) or row)

        try:
            items = await asyncio.wait_for(asyncio.gather(*(wait_one(row) for row in rows)), timeout=timeout)
        except asyncio.TimeoutError:
            return {
                "status": "running",
                "subagents": [self._public_row(row) for row in rows],
            }
        return {"status": "completed", "subagents": list(items)}

    def list_subagents(self, parent_session: AgentSession) -> list[dict[str, Any]]:
        return [
            self._public_row(row)
            for row in store_service.list_subagents_for_session(parent_session.session_id)
        ]

    async def cancel(self, parent_session: AgentSession, subagent_id: str) -> dict[str, Any]:
        row = self._owned_row(parent_session, subagent_id, include_destroyed=True)
        if row.get("destroyed_at"):
            raise RuntimeError("子 Agent 已销毁，不能停止")
        if str(row.get("status")) in {"completed", "failed", "cancelled", "timed_out"}:
            return {
                **self._public_row(row),
                "cancel_outcome": f"already_{row['status']}",
            }
        return await self._cancel_row(store_service.mark_subagent_cancel_requested(subagent_id) or row)

    def get_result(self, parent_session: AgentSession, subagent_id: str) -> dict[str, Any]:
        row = self._owned_row(parent_session, subagent_id, include_destroyed=True)
        if row.get("destroyed_at"):
            raise RuntimeError("子 Agent 已销毁，结果已从主 Agent 上下文移除")
        run = store_service.get_agent_run(str(row["latest_run_id"])) or {}
        result = run.get("result_json")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                result = result
        return {
            "subagent_id": subagent_id,
            "latest_run_id": str(row["latest_run_id"]),
            "status": str(run.get("status") or row.get("status")),
            "result": result,
            "error": run.get("error_json"),
        }

    async def destroy(
        self,
        parent_session: AgentSession,
        subagent_id: str,
        *,
        destroyed_by: str = "user",
        destroy_reason: str = "",
    ) -> dict[str, Any]:
        row = self._owned_row(parent_session, subagent_id, include_destroyed=True)
        if row.get("destroyed_at"):
            return self._public_row(row)

        updated = store_service.mark_subagent_destroyed(
            subagent_id,
            destroyed_by=destroyed_by,
            destroy_reason=destroy_reason,
        ) or row
        await self._cancel_row(updated)
        return self._public_row(store_service.get_subagent(subagent_id) or updated)

    async def cancel_for_parent_run(self, parent_run_id: str, parent_session_id: str) -> None:
        # 只取消当前父 run 创建的子任务，避免影响同一会话历史轮次。
        rows = [
            row
            for row in store_service.list_subagents_for_session(parent_session_id)
            if str(row.get("parent_run_id")) == parent_run_id
            and str(row.get("status")) not in {"completed", "failed", "cancelled", "timed_out"}
        ]
        if rows:
            await asyncio.gather(*(self._cancel_row(row) for row in rows))

    async def _cancel_row(self, row: dict[str, Any]) -> dict[str, Any]:
        subagent_id = str(row["subagent_id"])
        latest_run_id = str(row["latest_run_id"])
        child_session = session_service.get_or_create(str(row["child_session_id"]))
        run = await agent_run_service.cancel_run(child_session, latest_run_id)
        status = str(run.get("status") or row.get("status") or "failed")
        updated = store_service.update_subagent(
            subagent_id,
            status=status,
            result_text=run.get("result") if isinstance(run.get("result"), str) else None,
            error_text=run.get("error", {}).get("message") if isinstance(run.get("error"), dict) else None,
        ) or store_service.get_subagent(subagent_id) or row
        outcome = "cancelled" if status == "cancelled" else f"already_{status}"
        return {**self._public_row(updated), "cancel_outcome": outcome}

    def _create_child_session(
        self,
        parent_session: AgentSession,
        *,
        name: str,
        allowed_tools: list[str] | None,
    ) -> AgentSession:
        parent_conversation = store_service.get_conversation_by_session(parent_session.session_id) or {}
        child = session_service.get_or_create(
            workspace_id=parent_session.workspace_id,
            role="subagent",
            parent_session_id=parent_session.session_id,
        )
        session_service.clone_host_state(parent_session, child)
        child.platform_id = parent_session.platform_id or parent_conversation.get("platform_id")
        child.owner_user_id = parent_session.owner_user_id or parent_conversation.get("owner_user_id")
        child.external_user_id = parent_session.external_user_id or parent_conversation.get("external_user_id")
        child.platform_files = [dict(item) for item in parent_session.platform_files]
        child.platform_skills = [dict(item) for item in parent_session.platform_skills]
        child.uploaded_skills = [dict(item) for item in parent_session.uploaded_skills]
        child.session_mcp = [dict(item) for item in parent_session.session_mcp]
        child.disabled_capability_ids = list(parent_session.disabled_capability_ids)
        child.allow_network = parent_session.allow_network
        child.allowed_tools = list(dict.fromkeys(allowed_tools)) if allowed_tools else parent_session.allowed_tools
        child.subagent_tools_enabled = False
        conversation = store_service.create_conversation(
            session_id=child.session_id,
            workspace_id=parent_session.workspace_id,
            title=f"Subagent: {name}",
            host_name=parent_session.host_name or "AetherCore",
            platform_id=child.platform_id,
            owner_user_id=child.owner_user_id,
            external_user_id=child.external_user_id,
            external_org_id=parent_conversation.get("external_org_id"),
            conversation_key=f"subagent-{child.session_id}",
            visibility="hidden",
            metadata={
                "subagent": True,
                "parent_session_id": parent_session.session_id,
                "workspace_id": parent_session.workspace_id,
            },
        )
        child.conversation_id = conversation["conversation_id"]
        session_service.persist(child)
        return child

    def _start_watcher(
        self,
        *,
        parent_run_id: str,
        parent_session_id: str,
        subagent_id: str,
        latest_run_id: str,
        name: str,
    ) -> None:
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._futures[subagent_id] = future

        async def watch() -> None:
            try:
                run = await agent_run_service.wait_for_run(latest_run_id)
                result = run.get("result")
                error = run.get("error")
                status = str(run.get("status") or "failed")
                result_text = (
                    result
                    if isinstance(result, str)
                    else json.dumps(result, ensure_ascii=False)
                    if result is not None
                    else ""
                )
                error_text = error.get("message") if isinstance(error, dict) else str(error or "")
                store_service.update_subagent(
                    subagent_id,
                    status=status,
                    result_text=result_text or None,
                    error_text=error_text or None,
                )
                await self._inject_result(
                    parent_run_id=parent_run_id,
                    parent_session_id=parent_session_id,
                    subagent_id=subagent_id,
                    latest_run_id=latest_run_id,
                    name=name,
                    status=status,
                    result_text=result_text or error_text or "",
                )
                if not future.done():
                    future.set_result(self._public_row(store_service.get_subagent(subagent_id) or {}))
            except Exception as exc:  # noqa: BLE001
                store_service.update_subagent(subagent_id, status="failed", error_text=str(exc))
                if not future.done():
                    future.set_exception(exc)
            finally:
                self._futures.pop(subagent_id, None)

        watcher = asyncio.create_task(watch())
        self._watchers.add(watcher)
        watcher.add_done_callback(self._watchers.discard)

    async def _inject_result(
        self,
        *,
        parent_run_id: str,
        parent_session_id: str,
        subagent_id: str,
        latest_run_id: str,
        name: str,
        status: str,
        result_text: str,
    ) -> None:
        parent = session_service.get_or_create(parent_session_id)
        row = store_service.get_subagent(subagent_id)
        if row is not None and row.get("destroyed_at"):
            return
        content = f"Subagent {name} {status}: {result_text}"
        turn_index = max([int(item.get("turn_index") or 0) for item in parent.messages], default=0) + 1
        parent.messages.append(
            context_message_adapter.ensure_runtime_metadata(
                {
                    "role": "user",
                    "content": content,
                    "subagent_id": subagent_id,
                    "latest_run_id": latest_run_id,
                    "workspace_id": parent.workspace_id,
                },
                turn_index=turn_index,
                kind="subagent_result",
            )
        )
        session_service.persist(parent)

        await self._publish_parent_event(
            parent,
            parent_run_id,
            "subagent_result_ready",
            subagent_id=subagent_id,
            latest_run_id=latest_run_id,
            name=name,
            status=status,
            result=result_text,
        )
        event_type = "subagent_completed" if status == "completed" else "subagent_failed"
        await self._publish_parent_event(
            parent,
            parent_run_id,
            event_type,
            subagent_id=subagent_id,
            latest_run_id=latest_run_id,
            name=name,
            status=status,
        )

    async def _publish_parent_event(
        self,
        parent_session: AgentSession,
        parent_run_id: str,
        event_type: str,
        **payload: Any,
    ) -> None:
        event = AgentEvent(
            type=event_type,
            session_id=parent_session.session_id,
            payload=payload,
        )
        await agent_run_service.publish_event(parent_run_id, event)

    def _owned_row(
        self,
        parent_session: AgentSession,
        subagent_id: str,
        *,
        include_destroyed: bool = False,
    ) -> dict[str, Any]:
        row = store_service.get_subagent(subagent_id)
        if row is None or str(row.get("parent_session_id")) != parent_session.session_id:
            raise LookupError("子 Agent 不存在或不属于当前会话")
        if not include_destroyed and row.get("destroyed_at"):
            raise LookupError("子 Agent 已销毁")
        return row

    def _public_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "subagent_id": str(row.get("subagent_id")),
            "workspace_id": str(row.get("workspace_id") or ""),
            "child_session_id": str(row.get("child_session_id") or ""),
            "latest_run_id": str(row.get("latest_run_id") or ""),
            "name": str(row.get("name")),
            "task": str(row.get("task")),
            "status": str(row.get("status")),
            "result": row.get("result_text"),
            "error": row.get("error_text"),
            "cancel_requested_at": row.get("cancel_requested_at"),
            "created_at": row.get("created_at"),
            "finished_at": row.get("finished_at"),
            "destroyed_at": row.get("destroyed_at"),
            "current_action": self._current_action(row),
            "user_can_message": False,
        }

    def _current_action(self, row: dict[str, Any]) -> dict[str, str] | None:
        if row.get("destroyed_at"):
            return {"kind": "destroyed", "label": "已销毁"}

        status = str(row.get("status") or "")
        terminal_labels = {
            "completed": "已完成",
            "failed": "执行失败",
            "cancelled": "已停止",
            "timed_out": "已超时",
        }
        if status in terminal_labels:
            return {"kind": status, "label": terminal_labels[status]}

        child_session = session_service.get_or_create(str(row.get("child_session_id") or ""))
        active_run = child_session.active_run_view or {}
        assistant = active_run.get("assistant") if isinstance(active_run.get("assistant"), dict) else {}
        blocks = list(assistant.get("blocks", []))
        running_tool = next(
            (
                block
                for block in reversed(blocks)
                if isinstance(block, dict)
                and block.get("kind") == "tool"
                and block.get("status") == "running"
            ),
            None,
        )
        if isinstance(running_tool, dict):
            label = str(running_tool.get("title") or "正在执行工具")
            meta = str(running_tool.get("meta") or "").strip()
            return {"kind": "tool", "label": f"{label} {meta}".strip()}

        speaking = next(
            (
                block
                for block in reversed(blocks)
                if isinstance(block, dict)
                and block.get("kind") in {"reasoning", "content"}
                and block.get("status") == "streaming"
            ),
            None,
        )
        if isinstance(speaking, dict):
            return {"kind": "thinking", "label": "思考中"}
        return {"kind": "running", "label": "准备执行"}


subagent_service = SubagentService()
