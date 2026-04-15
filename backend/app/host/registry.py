# backend/app/host/registry.py
from app.schemas.host import HostBindRequest, HostBindingSummary
from app.services.session_service import session_service


class HostRegistry:
    """维护宿主平台与 AetherCore 会话之间的绑定关系。"""

    def bind(self, request: HostBindRequest) -> HostBindingSummary:
        session = session_service.get_or_create(request.session_id)
        session_service.attach_host(
            session=session,
            host_name=request.host_name,
            host_type=request.host_type,
            context=request.context.model_dump(),
            tools=[item.model_dump() for item in request.tools],
            skills=[
                {
                    **item.model_dump(),
                    "source": "host",
                }
                for item in request.skills
            ],
            apis=[item.model_dump() for item in request.apis],
        )
        return HostBindingSummary(
            host_name=request.host_name,
            host_type=request.host_type,
            session_id=session.session_id,
            tool_count=len(request.tools),
            skill_count=len(request.skills),
            api_count=len(request.apis),
        )


host_registry = HostRegistry()
