"""会话指令的只读投影。浏览器和宿主使用同一份数据。"""

from app.services.context.context_pipeline import context_pipeline
from app.services.llm_config_service import llm_config_service
from app.services.session_types import AgentSession
from app.services.skill_tools import visible_schemas
from app.services.store import store_service
from app.services.tool_service import tool_service


def _runtime(session: AgentSession):
    conversation = store_service.get_conversation_by_session(session.session_id) or {}
    return llm_config_service.resolve_for_conversation(conversation)


def inspect_context(session: AgentSession) -> dict:
    return context_pipeline.inspect_status(session, lambda: _runtime(session))


def inspect_tools(session: AgentSession) -> dict:
    catalog = tool_service.create_catalog_snapshot(session)
    schemas = visible_schemas(session, catalog.schemas) if _runtime(session).extra_body.get("tools") != [] else []
    tools = []
    for schema in schemas:
        name = schema["function"]["name"]
        descriptor = catalog.host_descriptors_by_name.get(name)
        source = "built_in" if descriptor is None else "mcp" if descriptor.get("kind") == "mcp" else "host"
        tools.append({"name": name, "source": source})
    return {"revision": catalog.revision, "tools": sorted(tools, key=lambda item: item["name"])}
