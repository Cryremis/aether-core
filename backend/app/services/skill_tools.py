"""按需披露的技能工具包；与市场 HTTP 接口共用业务服务。"""
from app.services.skill_authoring_service import skill_authoring_service
from app.services.tool_result_service import ToolExecutionResult

S = {"type": "string"}
I = {"type": "integer", "minimum": 0}
SCOPE = {"type": "string", "enum": ["session", "user"]}
POLICY = {"type": "string", "enum": ["creator_only", "platform_members"]}
DEFINITIONS = {
    "skill_init": ("创建私有技能草稿，可从当前平台指定版本完整复制，返回可编辑路径。", {"name": S, "description": S, "content": S, "source_entry_id": S, "source_version": S}, ["name", "description"]),
    "skill_validate": ("校验草稿并冻结快照；不代表已完成行为测试。", {"draft_id": S}, ["draft_id"]),
    "skill_install": ("安装已验证快照或当前平台市场技能，默认当前会话。", {"snapshot_id": S, "entry_id": S, "version": S, "scope": SCOPE}, []),
    "skill_export": ("导出已验证技能快照或当前平台指定版本为可下载 ZIP。", {"snapshot_id": S, "entry_id": S, "version": S}, []),
    "skill_uninstall": ("删除当前会话或个人安装的技能，不删除市场条目。", {"name": S, "scope": SCOPE}, ["name"]),
    "skill_market_search": ("搜索当前宿主平台共享技能，不包含其他平台。", {"query": S, "offset": I, "limit": I}, []),
    "skill_market_inspect": ("查看平台技能版本、文件和内容；不会加载执行指令。", {"entry_id": S, "version": S, "file_path": S}, ["entry_id"]),
    "skill_publish": ("将冻结快照发布到当前平台。更新必须提供当前 revision 和 base_version。共同维护允许平台成员发新版。", {"snapshot_id": S, "name": S, "description": S, "idempotency_key": S, "version": S, "entry_id": S, "expected_revision": I, "base_version": S, "maintenance_policy": POLICY}, ["snapshot_id", "name", "description"]),
    "skill_set_maintenance": ("创作者或平台管理员修改维护方式。", {"entry_id": S, "expected_revision": I, "maintenance_policy": POLICY}, ["entry_id", "expected_revision", "maintenance_policy"]),
    "skill_set_status": ("创作者或平台管理员下架或恢复共享条目，保留历史版本。", {"entry_id": S, "expected_revision": I, "status": {"type": "string", "enum": ["active", "unlisted"]}}, ["entry_id", "expected_revision", "status"]),
}
BUNDLES = {
    "skill_authoring": ("制作、验证、保存和导出技能", list(DEFINITIONS)[:5]),
    "skill_market": ("搜索、查看和发布当前宿主平台共享技能", list(DEFINITIONS)[5:8]),
    "skill_management": ("创作者管理维护方式、下架和恢复", list(DEFINITIONS)[8:]),
}


def register_skill_tools(registry):
    def handler_for(name):
        async def handler(session, arguments):
            import jsonschema
            description, properties, required = DEFINITIONS[name]
            clean = {k: v for k, v in arguments.items() if k != "timeout_seconds"}
            try:
                jsonschema.validate(clean, {"type": "object", "properties": properties, "required": required, "additionalProperties": False})
                result = skill_authoring_service.execute(session, name, clean)
                return ToolExecutionResult.success("skill.operation", description, result)
            except (ValueError, RuntimeError, KeyError, OSError, jsonschema.ValidationError) as exc:
                code = "NOT_AUTHORIZED" if isinstance(exc, PermissionError) else "SKILL_OPERATION_FAILED"
                if str(exc).startswith(("VERSION_CONFLICT:", "REVISION_CONFLICT:")):
                    code = str(exc).split(":", 1)[0]
                return ToolExecutionResult.failure("skill.operation", "技能操作失败", code=code, message=str(exc))
        return handler

    for name, (description, properties, required) in DEFINITIONS.items():
        registry.register(name, description, {"properties": properties}, handler_for(name), required=required)

    async def discover(session, arguments):
        bundle = arguments.get("bundle_id")
        query = str(arguments.get("query", "")).casefold()
        allowed = set(session.allowed_tools) if session.allowed_tools is not None else set(DEFINITIONS)
        from app.services.skill_authoring_service import author_key
        from app.services.extension_store_service import extension_store_service
        try:
            from app.services.store import store_service
            with store_service._connect() as conn:
                creator = conn.execute("SELECT 1 FROM extension_entries WHERE platform_id IS ? AND author_key=? LIMIT 1", (session.platform_id, author_key(session))).fetchone() is not None
        except PermissionError:
            creator = False
        if not creator:
            # 管理能力在服务端仍会逐条鉴权，发现目录也不向普通成员推荐管理工具。
            from app.services.store import store_service
            user = store_service.get_user_by_id(session.owner_user_id) if session.owner_user_id else None
            admin = bool(user and (user.role == "system_admin" or store_service.is_platform_admin(platform_id=session.platform_id, user_id=user.user_id)))
            if not admin:
                allowed.difference_update(BUNDLES["skill_management"][1])
        names = BUNDLES[bundle][1] if bundle in BUNDLES else [name for name, spec in DEFINITIONS.items() if query and query in (name + spec[0]).casefold()]
        names = [name for name in names if name in allowed][:min(8, max(1, int(arguments.get("limit", 8))))]
        session.loaded_skill_tools = list(dict.fromkeys([*session.loaded_skill_tools, *names]))
        return ToolExecutionResult.success("tools.loaded", "技能工具将在下一模型轮次可用", {"loaded": names, "bundles": {key: value[0] for key, value in BUNDLES.items()}})

    registry.register("discover_tools", "按需加载技能工具。skill_authoring：制作/验证/保存/导出；skill_market：搜索/查看/发布当前平台共享技能。加载后下一模型轮次调用。", {"properties": {"bundle_id": {"type": "string", "enum": list(BUNDLES)}, "query": S, "limit": I}}, discover)


def visible_schemas(session, schemas):
    return [schema for schema in schemas if schema["function"]["name"] not in DEFINITIONS or schema["function"]["name"] in session.loaded_skill_tools]
