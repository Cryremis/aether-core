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

    def bind(self, request: HostBindRequest) -> dict:
        external_user_id = request.context.user.get("id") or request.context.user.get("account_id") or "host_user"
        external_user_name = request.context.user.get("name", "Host User")
        
        platform = store_service.get_platform_by_key(request.host_type)
        if platform is None:
            platform = store_service.get_platform_by_key("standalone")
        if platform is None:
            raise RuntimeError("找不到可用平台，请确保 standalone 平台已初始化")
        
        conversation = store_service.find_host_conversation(
            platform_id=platform["platform_id"],
            external_user_id=str(external_user_id),
            conversation_key=None,
            conversation_id=None,
        )
        
        if conversation is None:
            session = session_service.get_or_create(request.session_id)
            conversation = store_service.create_conversation(
                session_id=session.session_id,
                title="新对话",
                host_name=request.host_name,
                host_type=request.host_type,
                platform_id=platform["platform_id"],
                external_user_id=str(external_user_id),
                external_org_id=None,
                conversation_key=None,
                metadata={"external_user_name": external_user_name},
            )
        else:
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
            platform_id=platform["platform_id"],
            conversation_id=conversation["conversation_id"],
            external_user_id=str(external_user_id),
        )
        
        return {
            "host_name": request.host_name,
            "host_type": request.host_type,
            "session_id": session.session_id,
            "conversation_id": conversation["conversation_id"],
            "token": token,
            "tool_count": len(request.tools),
            "skill_count": len(request.skills),
            "api_count": len(request.apis),
        }


host_registry = HostRegistry()
