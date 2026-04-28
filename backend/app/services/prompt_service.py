from __future__ import annotations

import re
from typing import Any

from app.schemas.prompt import PlatformPromptConfigSummary, PlatformPromptConfigUpdateRequest
from app.services.session_types import AgentSession
from app.services.skill_service import skill_service
from app.services.store import store_service


class PromptService:
    def get_platform_summary(self, platform_id: int) -> PlatformPromptConfigSummary | None:
        row = store_service.get_platform_prompt_config(platform_id)
        if row is None:
            return None
        return self._to_summary(row)

    def update_platform_config(
        self,
        platform_id: int,
        request: PlatformPromptConfigUpdateRequest,
    ) -> PlatformPromptConfigSummary:
        row = store_service.upsert_platform_prompt_config(
            platform_id=platform_id,
            enabled=request.enabled,
            system_prompt=request.system_prompt.strip(),
        )
        return self._to_summary(row)

    def delete_platform_config(self, platform_id: int) -> None:
        store_service.delete_platform_prompt_config(platform_id)

    def build_system_messages(
        self,
        session: AgentSession,
        *,
        conversation: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []

        core_prompt = skill_service.build_core_system_prompt().strip()
        if core_prompt:
            messages.append({"role": "system", "content": core_prompt})

        platform = self._resolve_platform(conversation)
        platform_prompt = self._resolve_platform_prompt(session, conversation, platform)
        if platform_prompt:
            messages.append({"role": "system", "content": platform_prompt})

        for item in session.host_system_prompts:
            if not item.get("enabled", True):
                continue
            rendered = self._render_template(
                str(item.get("content") or ""),
                session=session,
                conversation=conversation,
                platform=platform,
            ).strip()
            if rendered:
                messages.append({"role": "system", "content": rendered})

        environment_prompt = skill_service.build_environment_prompt(session).strip()
        if environment_prompt:
            messages.append({"role": "system", "content": environment_prompt})

        return messages

    def _resolve_platform(self, conversation: dict[str, Any] | None) -> dict[str, Any] | None:
        if not conversation or not conversation.get("platform_id"):
            return None
        return store_service.get_platform_by_id(int(conversation["platform_id"]))

    def _resolve_platform_prompt(
        self,
        session: AgentSession,
        conversation: dict[str, Any] | None,
        platform: dict[str, Any] | None,
    ) -> str:
        if platform is None:
            return ""
        row = store_service.get_platform_prompt_config(int(platform["platform_id"]))
        if row is None or not row.get("enabled"):
            return ""
        return self._render_template(
            str(row.get("system_prompt") or ""),
            session=session,
            conversation=conversation,
            platform=platform,
        ).strip()

    def _render_template(
        self,
        template: str,
        *,
        session: AgentSession | None = None,
        conversation: dict[str, Any] | None = None,
        platform: dict[str, Any] | None = None,
    ) -> str:
        if not template.strip():
            return ""

        host_context = (session.host_context if session else {}) or {}
        values: dict[str, Any] = {
            "platform": platform or {},
            "conversation": {
                "id": conversation.get("conversation_id") if conversation else "",
                "session_id": session.session_id if session else "",
            },
            "host": {
                "name": session.host_name if session else "",
                "user": host_context.get("user", {}),
                "page": host_context.get("page", {}),
                "extras": host_context.get("extras", {}),
                "context": host_context,
            },
            "workspace": {
                "input_dir": str(session.workspace.input_dir) if session and session.workspace else "",
                "skills_dir": str(session.workspace.skills_dir) if session and session.workspace else "",
                "work_dir": str(session.workspace.work_dir) if session and session.workspace else "",
                "output_dir": str(session.workspace.output_dir) if session and session.workspace else "",
            },
        }

        def replace(match: re.Match[str]) -> str:
            raw_key = match.group(1).strip()
            resolved = self._lookup_value(values, raw_key.split("."))
            return match.group(0) if resolved is None else str(resolved)

        return re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", replace, template)

    def _lookup_value(self, values: dict[str, Any], path: list[str]) -> Any:
        current: Any = values
        for part in path:
            if isinstance(current, dict) and part in current:
                current = current[part]
                continue
            return None
        return current

    def _to_summary(self, row: dict[str, Any]) -> PlatformPromptConfigSummary:
        return PlatformPromptConfigSummary(
            enabled=bool(row.get("enabled", True)),
            system_prompt=str(row.get("system_prompt") or ""),
            updated_at=row.get("updated_at"),
        )


prompt_service = PromptService()
