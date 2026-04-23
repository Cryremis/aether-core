# backend/app/services/conversation_service.py
from __future__ import annotations

from app.schemas.platform import ConversationSummary
from app.services.platform_baseline_service import platform_baseline_service
from app.services.session_service import session_service
from app.services.session_types import AgentSession
from app.services.store import StoreUser, store_service
from app.services.token_service import token_service


class ConversationService:
    """会话创建、恢复与历史查询服务。"""

    def bootstrap_admin_workbench(self, user: StoreUser, session_id: str | None = None) -> AgentSession:
        session: AgentSession
        if session_id:
            conversation = store_service.get_conversation_by_session(session_id)
            if conversation is None or conversation.get("owner_user_id") != user.user_id:
                raise PermissionError("目标会话不存在或不属于当前用户")
            session = session_service.get_or_create(session_id)
            session.conversation_id = conversation["conversation_id"]
            return session

        session = session_service.get_or_create()
        platform = store_service.get_platform_by_key("standalone")
        if platform is None:
            raise RuntimeError("standalone 平台未初始化")
        conversation = store_service.create_conversation(
            session_id=session.session_id,
            title="新对话",
            host_name="AetherCore",
            platform_id=platform["platform_id"],
            owner_user_id=user.user_id,
            metadata={"owner_name": user.full_name},
        )
        session.conversation_id = conversation["conversation_id"]
        session.host_name = "AetherCore"
        platform_baseline_service.materialize_to_session("standalone", session)
        session_service.persist(session)
        return session

    def bootstrap_host_workbench(
        self,
        *,
        platform_key: str,
        external_user_id: str,
        external_user_name: str,
        external_org_id: str | None,
        conversation_id: str | None,
        conversation_key: str | None,
        host_name: str | None,
    ) -> tuple[AgentSession, str]:
        platform = store_service.get_platform_by_key(platform_key)
        if platform is None:
            raise RuntimeError("目标平台不存在")

        conversation = store_service.find_host_conversation(
            platform_id=platform["platform_id"],
            external_user_id=external_user_id,
            conversation_key=conversation_key,
            conversation_id=conversation_id,
        )
        if conversation is None:
            session = session_service.get_or_create()
            conversation = store_service.create_conversation(
                session_id=session.session_id,
                title="新对话",
                host_name=host_name or platform["display_name"],
                platform_id=platform["platform_id"],
                external_user_id=external_user_id,
                external_org_id=external_org_id,
                conversation_key=conversation_key,
                metadata={"external_user_name": external_user_name},
            )
            session.host_name = host_name or platform["display_name"]
            platform_baseline_service.materialize_to_session(platform["platform_key"], session)
        else:
            session = session_service.get_or_create(conversation["session_id"])

        session.conversation_id = conversation["conversation_id"]
        session_service.persist(session)
        token, _ = token_service.create_embed_token(
            platform_id=platform["platform_id"],
            conversation_id=conversation["conversation_id"],
            external_user_id=external_user_id,
        )
        return session, token

    def list_for_admin(self, user: StoreUser) -> list[ConversationSummary]:
        return [self._to_summary(item) for item in store_service.list_conversations_for_admin(user.user_id)]

    def list_for_host_user(self, *, platform_id: int, external_user_id: str) -> list[ConversationSummary]:
        return [
            self._to_summary(item)
            for item in store_service.list_conversations_for_host_user(
                platform_id=platform_id,
                external_user_id=external_user_id,
            )
        ]

    def _to_summary(self, row: dict) -> ConversationSummary:
        return ConversationSummary(
            conversation_id=row["conversation_id"],
            session_id=row["session_id"],
            title=row["title"],
            host_name=row["host_name"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_message_at=row["last_message_at"],
            message_count=row["message_count"],
        )


conversation_service = ConversationService()