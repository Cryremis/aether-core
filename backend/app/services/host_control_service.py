from __future__ import annotations

import uuid
from typing import Any

from app.host.registry import host_registry
from app.schemas.agent import AgentEvent
from app.schemas.host import (
    HostBindRequest,
    HostContextDescriptor,
    HostConversationCreateRequest,
    HostConversationPatchRequest,
)
from app.schemas.platform import ConversationSummary
from app.services.agent_run_service import agent_run_service
from app.services.session_service import session_service
from app.services.store import store_service
from app.services.workspace_runtime_service import workspace_runtime_service


class HostControlService:
    """宿主平台控制面：会话、消息、运行与回复投影。"""

    def create_conversation(self, *, platform: dict, request: HostConversationCreateRequest) -> dict[str, Any]:
        context = request.context.model_dump(mode="json")
        context["user"] = {
            **(context.get("user") or {}),
            "id": request.external_user_id,
            "name": request.external_user_name,
        }
        bind_request = HostBindRequest(
            platform_key=str(platform["platform_key"]),
            host_name=request.host_name,
            conversation_key=request.conversation_key or request.idempotency_key or f"host-created-{uuid.uuid4().hex}",
            visibility=request.visibility,
            context=HostContextDescriptor(**context),
            tools=request.tools,
            skills=request.skills,
            system_prompts=request.system_prompts,
            apis=request.apis,
        )
        result = host_registry.bind(bind_request, platform=platform)
        if request.title and request.title != "新对话":
            updated = store_service.update_conversation(
                str(result["conversation_id"]),
                title=request.title,
            )
            result["revision"] = int(updated.get("revision") or 0) if updated else 0
        else:
            conversation = store_service.get_conversation(str(result["conversation_id"]))
            result["revision"] = int(conversation.get("revision") or 0) if conversation else 0
        result["visibility"] = request.visibility
        return result

    def list_conversations(
        self,
        *,
        platform: dict,
        external_user_id: str,
        include_hidden: bool,
        include_archived: bool,
        include_deleted: bool,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        rows = store_service.list_conversations_for_host_user(
            platform_id=int(platform["platform_id"]),
            external_user_id=external_user_id,
            include_hidden=include_hidden,
            include_archived=include_archived,
            include_deleted=include_deleted,
            limit=limit + 1,
            offset=offset,
        )
        has_more = len(rows) > limit
        items = rows[:limit]
        return {
            "items": [self._summary(row).model_dump(mode="json") for row in items],
            "next_cursor": str(offset + limit) if has_more else None,
        }

    def get_conversation(self, *, platform: dict, conversation_id: str) -> dict[str, Any]:
        conversation = self._conversation(platform, conversation_id)
        return self._summary(conversation).model_dump(mode="json")

    def get_workspace(self, *, platform: dict, workspace_id: str) -> dict[str, Any]:
        workspace = self._workspace(platform, workspace_id)
        members = store_service.list_workspace_members(workspace_id)
        runtime = store_service.get_workspace_runtime(workspace_id)
        if runtime is not None:
            runtime["active_command_count"] = workspace_runtime_service.active_command_count(workspace_id)
        return {
            "workspace_id": str(workspace["workspace_id"]),
            "owner_session_id": str(workspace["owner_session_id"]),
            "owner_conversation_id": workspace.get("owner_conversation_id"),
            "status": str(workspace.get("status") or "active"),
            "revision": int(workspace.get("revision") or 1),
            "baseline_root": str(workspace.get("baseline_root") or ""),
            "created_at": str(workspace.get("created_at") or ""),
            "updated_at": str(workspace.get("updated_at") or ""),
            "members": [self._workspace_member(item) for item in members],
            "runtime": runtime,
        }

    def list_workspace_members(self, *, platform: dict, workspace_id: str) -> dict[str, Any]:
        self._workspace(platform, workspace_id)
        return {
            "items": [
                self._workspace_member(item)
                for item in store_service.list_workspace_members(workspace_id)
            ],
        }

    def get_workspace_runtime(self, *, platform: dict, workspace_id: str) -> dict[str, Any]:
        self._workspace(platform, workspace_id)
        runtime = store_service.get_workspace_runtime(workspace_id)
        if runtime is None:
            return {"workspace_id": workspace_id, "status": "missing", "active_command_count": 0}
        runtime["active_command_count"] = workspace_runtime_service.active_command_count(workspace_id)
        return runtime

    def patch_conversation(
        self,
        *,
        platform: dict,
        conversation_id: str,
        request: HostConversationPatchRequest,
    ) -> dict[str, Any]:
        self._conversation(platform, conversation_id)
        try:
            updated = store_service.update_conversation(
                conversation_id,
                expected_revision=request.expected_revision,
                title=request.title,
                visibility=request.visibility,
                pinned=request.pinned,
                archived=request.archived,
                metadata=request.metadata,
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if updated is None:
            raise LookupError("会话不存在")
        return self._summary(updated).model_dump(mode="json")

    def set_visibility(self, *, platform: dict, conversation_id: str, visibility: str) -> dict[str, Any]:
        return self.patch_conversation(
            platform=platform,
            conversation_id=conversation_id,
            request=HostConversationPatchRequest(visibility=visibility),
        )

    def delete_conversation(self, *, platform: dict, conversation_id: str) -> bool:
        self._conversation(platform, conversation_id)
        return store_service.soft_delete_conversation(conversation_id)

    async def send_message(
        self,
        *,
        platform: dict,
        conversation_id: str,
        message: str,
        allow_network: bool | None,
        client_message_id: str | None,
        idempotency_key: str | None,
        reasoning_effort: str | None,
    ) -> dict[str, Any]:
        conversation = self._conversation(platform, conversation_id)
        session = session_service.get_or_create(str(conversation["session_id"]))
        effective_idempotency_key = idempotency_key or client_message_id
        if effective_idempotency_key:
            existing = store_service.find_agent_run_by_idempotency(
                conversation_id,
                effective_idempotency_key,
            )
            if existing is not None:
                return {
                    "run_id": existing["run_id"],
                    "conversation_id": conversation_id,
                    "session_id": session.session_id,
                    "status": existing["status"],
                    "idempotent_replay": True,
                }
        if allow_network is not None:
            session.allow_network = allow_network
        run_id = await agent_run_service.start_chat_run(
            session,
            message,
            client_message_id=client_message_id,
            reasoning_effort=reasoning_effort,
            idempotency_key=effective_idempotency_key,
        )
        event = AgentEvent(
            type="run_started",
            session_id=session.session_id,
            payload={"run_id": run_id},
        )
        await agent_run_service.publish_event(run_id, event)
        return {
            "run_id": run_id,
            "conversation_id": conversation_id,
            "session_id": session.session_id,
            "status": "running",
        }

    def get_run(self, *, platform: dict, run_id: str) -> dict[str, Any]:
        run = agent_run_service.get_run_view(run_id)
        if run is None:
            raise LookupError("运行不存在")
        conversation = self._conversation(platform, str(run.get("conversation_id") or ""))
        run["conversation_id"] = conversation["conversation_id"]
        run["subagents"] = store_service.list_subagents_for_session(str(conversation["session_id"]))
        return run

    def list_run_events(
        self,
        *,
        platform: dict,
        run_id: str,
        after_seq: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        run = self.get_run(platform=platform, run_id=run_id)
        return store_service.list_agent_run_events(run_id, after_seq=after_seq, limit=limit)

    async def cancel_run(self, *, platform: dict, run_id: str) -> dict[str, Any]:
        run = self.get_run(platform=platform, run_id=run_id)
        session = session_service.get_or_create(str(run["session_id"]))
        cancelled_run_id = session.request_abort()
        if cancelled_run_id:
            event = AgentEvent(
                type="run_status_changed",
                session_id=session.session_id,
                payload={"run_id": run_id, "status": "cancelling"},
            )
            await agent_run_service.publish_event(run_id, event)
        return {"run_id": run_id, "status": "cancelling" if cancelled_run_id else str(run.get("status"))}

    def assistant_messages(
        self,
        *,
        platform: dict,
        conversation_id: str,
        limit: int,
        include_subagents: bool,
        run_statuses: set[str] | None = None,
    ) -> dict[str, Any]:
        conversation = self._conversation(platform, conversation_id)
        session = session_service.get_or_create(str(conversation["session_id"]))
        candidates: list[dict[str, Any]] = []

        for message in session.messages:
            if str(message.get("role")) != "assistant" or not message.get("content"):
                continue
            if message.get("visible_in_transcript") is False:
                continue
            candidates.append(
                {
                    "message_id": str(message.get("message_id") or f"msg_{len(candidates)}"),
                    "run_id": str(message.get("run_id")) if message.get("run_id") else None,
                    "agent_id": None,
                    "subagent_id": None,
                    "content": str(message.get("content") or ""),
                    "created_at": str(message.get("timestamp") or conversation.get("updated_at") or ""),
                }
            )

        if include_subagents:
            for row in store_service.list_subagents_for_session(str(conversation["session_id"])):
                if not row.get("result_text") and not row.get("error_text"):
                    continue
                candidates.append(
                    {
                        "message_id": f"subagent_{row['subagent_id']}",
                        "run_id": str(row["latest_run_id"]),
                        "agent_id": str(row["name"]),
                        "subagent_id": str(row["subagent_id"]),
                        "content": str(row.get("result_text") or row.get("error_text") or ""),
                        "created_at": str(row.get("finished_at") or row.get("updated_at") or ""),
                    }
                )

        run_ids = [str(item["run_id"]) for item in candidates if item.get("run_id")]
        status_by_run = {str(row["run_id"]): str(row["status"]) for row in store_service.get_agent_runs(run_ids)}
        for item in candidates:
            item["run_status"] = status_by_run.get(str(item.get("run_id"))) if item.get("run_id") else None

        if run_statuses:
            candidates = [item for item in candidates if str(item.get("run_status")) in run_statuses]
        candidates.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        items = candidates[:limit]
        return {
            "items": items,
            "next_cursor": None,
        }

    def _conversation(self, platform: dict, conversation_id: str) -> dict[str, Any]:
        conversation = store_service.get_conversation(conversation_id)
        if (
            conversation is None
            or int(conversation.get("platform_id") or 0) != int(platform["platform_id"])
            or conversation.get("deleted_at") is not None
        ):
            raise LookupError("会话不存在")
        return conversation

    def _workspace(self, platform: dict, workspace_id: str) -> dict[str, Any]:
        workspace = store_service.get_workspace(workspace_id)
        if workspace is None or str(workspace.get("status") or "") == "deleted":
            raise LookupError("Workspace 不存在")
        owner_conversation_id = str(workspace.get("owner_conversation_id") or "")
        conversation = (
            store_service.get_conversation(owner_conversation_id)
            if owner_conversation_id
            else None
        )
        if conversation is None:
            owner_session_id = str(workspace.get("owner_session_id") or "")
            conversation = store_service.get_conversation_by_session(owner_session_id)
        if (
            conversation is None
            or int(conversation.get("platform_id") or 0) != int(platform["platform_id"])
            or conversation.get("deleted_at") is not None
        ):
            raise LookupError("Workspace 不存在")
        return workspace

    @staticmethod
    def _workspace_member(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": str(row.get("session_id")),
            "conversation_id": row.get("conversation_id"),
            "role": str(row.get("role") or "owner"),
            "parent_session_id": row.get("parent_session_id"),
            "title": row.get("title"),
            "visibility": row.get("visibility"),
            "joined_at": str(row.get("joined_at") or ""),
            "last_active_at": str(row.get("last_active_at") or ""),
        }

    @staticmethod
    def _summary(row: dict[str, Any]) -> ConversationSummary:
        return ConversationSummary(
            conversation_id=str(row["conversation_id"]),
            session_id=str(row["session_id"]),
            workspace_id=str(row.get("workspace_id") or ""),
            title=str(row.get("title") or "新对话"),
            host_name=str(row.get("host_name") or ""),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
            last_message_at=str(row.get("last_message_at") or ""),
            message_count=int(row.get("message_count") or 0),
            visibility=str(row.get("visibility") or "normal"),
            archived_at=row.get("archived_at"),
            pinned_at=row.get("pinned_at"),
            deleted_at=row.get("deleted_at"),
            revision=int(row.get("revision") or 0),
        )


host_control_service = HostControlService()
