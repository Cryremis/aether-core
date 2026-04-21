# backend/app/host/registry.py
from app.schemas.host import HostBindRequest
from app.services.session_service import session_service
from app.services.store import store_service
from app.services.token_service import token_service


def _to_dict(item):
    if isinstance(item, dict):
        return item
    return item.model_dump()


class HostRegistry:
    """维护宿主平台与 AetherCore 会话之间的绑定关系。"""

    def bind(self, request: HostBindRequest, *, platform: dict[str, object]) -> dict:
        external_user_id = request.context.user.get("id") or request.context.user.get("account_id")
        if not external_user_id:
            raise ValueError("context.user.id 或 context.user.account_id 不能为空")
        external_user_name = request.context.user.get("name", "Host User")

        conversation = store_service.find_host_conversation(
            platform_id=int(platform["platform_id"]),
            external_user_id=str(external_user_id),
            conversation_key=request.conversation_key,
            conversation_id=request.conversation_id,
        )

        if conversation is None:
            session = session_service.get_or_create(request.session_id)
            conversation = store_service.create_conversation(
                session_id=session.session_id,
                title="新对话",
                host_name=request.host_name,
                host_type=request.host_type,
                platform_id=int(platform["platform_id"]),
                external_user_id=str(external_user_id),
                external_org_id=None,
                conversation_key=request.conversation_key,
                metadata={"external_user_name": external_user_name},
            )
        else:
            if request.session_id and request.session_id != conversation["session_id"]:
                raise ValueError("session_id 与现有会话绑定不一致")
            session = session_service.get_or_create(conversation["session_id"])

        session_service.attach_host(
            session=session,
            host_name=request.host_name,
            host_type=request.host_type,
            context=_to_dict(request.context),
            tools=[_to_dict(item) for item in request.tools],
            skills=[
                {
                    **_to_dict(item),
                    "source": "host",
                }
                for item in request.skills
            ],
            apis=[_to_dict(item) for item in request.apis],
        )

        session.conversation_id = conversation["conversation_id"]
        session_service.persist(session)

        token, _ = token_service.create_embed_token(
            platform_id=int(platform["platform_id"]),
            conversation_id=conversation["conversation_id"],
            external_user_id=str(external_user_id),
        )

        return {
            "platform_key": platform["platform_key"],
            "host_name": request.host_name,
            "host_type": request.host_type,
            "session_id": session.session_id,
            "conversation_id": conversation["conversation_id"],
            "conversation_key": conversation.get("conversation_key"),
            "token": token,
            "tool_count": len(request.tools),
            "skill_count": len(request.skills),
            "api_count": len(request.apis),
        }


host_registry = HostRegistry()
