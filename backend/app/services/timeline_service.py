from __future__ import annotations

import copy
import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.session_service import session_service
from app.services.session_types import AgentSession
from app.services.store import store_service


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TimelineForkResult:
    session: AgentSession
    conversation: dict[str, Any]
    source_message_id: str


@dataclass
class TimelineRerunResult:
    rerun_prompt: str
    action: str
    source_message_id: str
    anchor_message_id: str
    rerun_message_id: str
    truncated_count: int


class TimelineService:
    """会话时间线变更服务，统一承载 fork/rerun/edit 等分支语义。"""

    _TIMELINE_HISTORY_LIMIT = 120
    _WORKSPACE_DIRS_TO_CLONE = ("input", "skills", "work", "output", "logs")

    def fork_from_message(
        self,
        *,
        source_session: AgentSession,
        source_conversation: dict[str, Any],
        source_message_id: str,
    ) -> TimelineForkResult:
        source_index = self._require_message_index(source_session, source_message_id)
        forked_messages = copy.deepcopy(source_session.messages[: source_index + 1])

        new_session = session_service.get_or_create()
        if source_session.baseline_root:
            session_service.bind_baseline_root(new_session, Path(source_session.baseline_root))
        self._clone_workspace(source_session, new_session)
        self._clone_host_and_assets(source_session, new_session)
        new_session.messages = forked_messages
        allowed_message_ids = {
            str(message.get("message_id") or "")
            for message in forked_messages
            if str(message.get("role") or "") in {"user", "assistant", "elicitation_response"}
        }
        new_session.transcript = [
            copy.deepcopy(item)
            for item in source_session.transcript
            if str(item.get("id") or "") in allowed_message_ids
        ]
        new_session.allow_network = source_session.allow_network
        new_session.message_schema_version = source_session.message_schema_version
        new_session.context_state = copy.deepcopy(source_session.context_state)
        new_session.active_run = None
        new_session.last_abort = None
        new_session.active_run_view = None
        session_service.persist(new_session)
        self._clone_metadata_state(source_session, new_session)

        conversation = store_service.create_conversation(
            session_id=new_session.session_id,
            title=source_conversation.get("title") or "新对话",
            host_name=new_session.host_name or source_conversation.get("host_name") or "AetherCore",
            platform_id=source_conversation.get("platform_id"),
            owner_user_id=source_conversation.get("owner_user_id"),
            external_user_id=source_conversation.get("external_user_id"),
            external_org_id=source_conversation.get("external_org_id"),
            conversation_key=None,
            metadata=self._build_fork_metadata(source_conversation, source_session, source_message_id),
        )
        new_session.conversation_id = conversation.get("conversation_id")
        session_service.persist(new_session)
        store_service.touch_conversation(new_session.session_id, message_count=len(new_session.messages))

        self._append_timeline_event(
            source_session,
            action="fork",
            payload={
                "source_message_id": source_message_id,
                "target_role": str(source_session.messages[source_index].get("role") or ""),
                "forked_session_id": new_session.session_id,
                "forked_conversation_id": new_session.conversation_id,
                "prefix_count": len(forked_messages),
            },
        )
        session_service.persist(source_session)
        self._append_timeline_event(
            new_session,
            action="fork_origin",
            payload={
                "source_session_id": source_session.session_id,
                "source_conversation_id": source_session.conversation_id,
                "source_message_id": source_message_id,
                "prefix_count": len(forked_messages),
            },
        )
        session_service.persist(new_session)

        return TimelineForkResult(
            session=new_session,
            conversation=conversation,
            source_message_id=source_message_id,
        )

    def rerun_from_message(
        self,
        *,
        session: AgentSession,
        source_message_id: str,
        edited_content: str | None = None,
    ) -> TimelineRerunResult:
        source_index = self._require_message_index(session, source_message_id)
        source_role = str(session.messages[source_index].get("role") or "")
        anchor_index = self._resolve_user_anchor_index(session.messages, source_index)
        anchor_message = session.messages[anchor_index]
        anchor_message_id = str(anchor_message.get("message_id") or "")
        original_prompt = str(anchor_message.get("content") or "")

        rerun_prompt = original_prompt
        action = "rerun"
        if edited_content is not None:
            edited = edited_content.strip()
            if not edited:
                raise ValueError("编辑后的消息不能为空。")
            rerun_prompt = edited
            action = "edit"

        before_count = len(session.messages)
        session.messages = copy.deepcopy(session.messages[: anchor_index + 1])
        allowed_message_ids = {
            str(message.get("message_id") or "")
            for message in session.messages
            if str(message.get("role") or "") in {"user", "assistant", "elicitation_response"}
        }
        session.transcript = [
            copy.deepcopy(item)
            for item in session.transcript
            if str(item.get("id") or "") in allowed_message_ids
        ]
        rerun_message_id = anchor_message_id
        if edited_content is not None:
            session.messages[anchor_index]["content"] = rerun_prompt
            rerun_message_id = anchor_message_id
        session.active_run = None
        session.last_abort = None
        session.active_run_view = None
        self._append_timeline_event(
            session,
            action=action,
            payload={
                "source_message_id": source_message_id,
                "anchor_message_id": anchor_message_id,
                "source_role": source_role,
                "before_count": before_count,
                "after_count": len(session.messages),
                "truncated_count": max(0, before_count - len(session.messages)),
                "edited": edited_content is not None,
                "rerun_message_id": rerun_message_id,
            },
        )
        session_service.persist(session)
        store_service.touch_conversation(session.session_id, message_count=len(session.messages))

        return TimelineRerunResult(
            rerun_prompt=rerun_prompt,
            action=action,
            source_message_id=source_message_id,
            anchor_message_id=anchor_message_id,
            rerun_message_id=rerun_message_id,
            truncated_count=max(0, before_count - len(session.messages)),
        )

    def _require_message_index(self, session: AgentSession, message_id: str) -> int:
        target = message_id.strip()
        if not target:
            raise ValueError("message_id 不能为空。")
        for index, message in enumerate(session.messages):
            if str(message.get("message_id") or "") == target:
                return index
        raise ValueError("目标消息不存在，可能已被重写。")

    def _resolve_user_anchor_index(self, messages: list[dict[str, Any]], start_index: int) -> int:
        if start_index < 0 or start_index >= len(messages):
            raise ValueError("目标消息下标越界。")
        for index in range(start_index, -1, -1):
            item = messages[index]
            if str(item.get("role") or "") != "user":
                continue
            if str(item.get("content") or "").strip():
                return index
        raise ValueError("未找到可重跑的用户消息。")

    def _clone_workspace(self, source_session: AgentSession, target_session: AgentSession) -> None:
        if source_session.workspace is None or target_session.workspace is None:
            return
        for dir_name in self._WORKSPACE_DIRS_TO_CLONE:
            source_dir = source_session.workspace.root / dir_name
            target_dir = target_session.workspace.root / dir_name
            self._copy_dir_contents(source_dir, target_dir)

    def _clone_metadata_state(self, source_session: AgentSession, target_session: AgentSession) -> None:
        if source_session.workspace is None or target_session.workspace is None:
            return
        source_metadata = source_session.workspace.metadata_dir
        target_metadata = target_session.workspace.metadata_dir
        if not source_metadata.exists():
            return
        target_metadata.mkdir(parents=True, exist_ok=True)
        for item in source_metadata.iterdir():
            if item.name == "session.json":
                continue
            if item.is_file() and item.suffix == ".json":
                target_file = target_metadata / item.name
                shutil.copy2(item, target_file)
                self._rewrite_metadata_session_id(target_file, target_session.session_id)

    def _copy_dir_contents(self, source_dir: Path, target_dir: Path) -> None:
        if not source_dir.exists():
            return
        target_dir.mkdir(parents=True, exist_ok=True)
        for item in source_dir.iterdir():
            target_path = target_dir / item.name
            if target_path.exists():
                if target_path.is_dir():
                    shutil.rmtree(target_path, ignore_errors=True)
                else:
                    target_path.unlink(missing_ok=True)
            if item.is_dir():
                shutil.copytree(item, target_path)
            else:
                shutil.copy2(item, target_path)

    def _rewrite_metadata_session_id(self, metadata_file: Path, session_id: str) -> None:
        if metadata_file.name not in {"workboard.json", "elicitation.json"}:
            return
        try:
            payload = json.loads(metadata_file.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        payload["session_id"] = session_id
        metadata_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _clone_host_and_assets(self, source_session: AgentSession, target_session: AgentSession) -> None:
        session_service.attach_host(
            session=target_session,
            host_name=source_session.host_name,
            context=copy.deepcopy(source_session.host_context),
            tools=copy.deepcopy(source_session.host_tools),
            skills=copy.deepcopy(source_session.host_skills),
            system_prompts=copy.deepcopy(source_session.host_system_prompts),
            apis=copy.deepcopy(source_session.host_apis),
        )
        target_session.platform_files = copy.deepcopy(source_session.platform_files)
        target_session.platform_skills = copy.deepcopy(source_session.platform_skills)
        target_session.uploaded_skills = copy.deepcopy(source_session.uploaded_skills)
        target_session.uploads = copy.deepcopy(source_session.uploads)
        target_session.artifacts = copy.deepcopy(source_session.artifacts)

    def _build_fork_metadata(
        self,
        source_conversation: dict[str, Any],
        source_session: AgentSession,
        source_message_id: str,
    ) -> dict[str, Any]:
        metadata = self._parse_metadata_json(source_conversation.get("metadata_json"))
        metadata["fork"] = {
            "from_session_id": source_session.session_id,
            "from_conversation_id": source_session.conversation_id,
            "from_message_id": source_message_id,
            "created_at": _utcnow_iso(),
        }
        return metadata

    def _parse_metadata_json(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            stripped = raw.strip()
            if not stripped:
                return {}
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    def _append_timeline_event(self, session: AgentSession, *, action: str, payload: dict[str, Any]) -> None:
        timeline = session.context_state.get("timeline")
        if not isinstance(timeline, dict):
            timeline = {"revision": 0, "history": []}
            session.context_state["timeline"] = timeline
        history = timeline.get("history")
        if not isinstance(history, list):
            history = []
            timeline["history"] = history

        history.append(
            {
                "event_id": f"timeline_evt_{uuid.uuid4().hex}",
                "action": action,
                "created_at": _utcnow_iso(),
                **payload,
            }
        )
        if len(history) > self._TIMELINE_HISTORY_LIMIT:
            overflow = len(history) - self._TIMELINE_HISTORY_LIMIT
            del history[:overflow]
        timeline["revision"] = int(timeline.get("revision") or 0) + 1


timeline_service = TimelineService()
