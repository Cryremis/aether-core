# 能力系统

AetherCore 将 Skill 与 MCP 统一称为能力。能力可安装到平台、用户或会话三个作用域，运行时按平台、用户、会话顺序合并，同名的窄作用域配置覆盖宽作用域配置。

## Skill

- 平台级 Skill 位于平台基线的 `skills/` 目录。
- 用户级 Skill 位于 `storage/users/<principal>/skills/`，跨会话生效。
- 会话级 Skill 位于会话 workspace，仅在当前会话生效。
- 删除使用硬删除；Agent 每轮实时解析有效列表。

## MCP

支持 STDIO 与 Streamable HTTP。STDIO server 通过 `docker exec` 在会话 sandbox 内运行，不在 API 主机直接执行；远程连接仅接受 HTTPS，并拒绝解析到私网、环回、链路本地、保留或组播地址的目标。

`env`、`headers`、OAuth token 和动态注册客户端信息使用 Fernet 加密保存；能力配置文件只保留 `secret_ref`，列表 API 只返回 `has_secrets` / `oauth_connected` 状态。生产环境必须设置独立的 `CAPABILITY_SECRET_ENCRYPTION_KEY`，轮换前应重新配置现有凭据。

```json
{
  "name": "context7",
  "transport": "stdio",
  "command": ["npx", "-y", "@upstash/context7-mcp"],
  "startup_timeout_sec": 10,
  "tool_timeout_sec": 60
}
```

远程示例：

```json
{
  "name": "docs",
  "transport": "streamable_http",
  "url": "https://mcp.example.com/mcp"
}
```

OAuth 2.1 MCP 使用 SDK 的受保护资源元数据发现、PKCE、动态客户端注册和 refresh token 自动刷新流程：

```json
{
  "name": "private-docs",
  "transport": "streamable_http",
  "url": "https://mcp.example.com/mcp",
  "auth": "oauth",
  "oauth_scopes": "mcp:tools"
}
```

OAuth 回调地址为 `${APP_PUBLIC_BASE_URL}/api/v1/capabilities/mcp/oauth/callback`。生产环境需要将 `APP_PUBLIC_BASE_URL` 配置为浏览器可访问的 HTTPS 公网地址。

MCP 工具向模型暴露为 `mcp__<server>__<tool>`。连接和工具发现不会改变进行中的模型轮次，下一轮工具目录快照才会使用新 revision。

## 浏览器偏好

能力默认全部开启。用户关闭的能力标识保存在浏览器 localStorage，每次对话请求发送到服务端并只应用于当前会话。服务端仍会重新校验能力归属，客户端偏好不会扩大权限。

## 拓展市场

商店保存拓展名称、介绍、版本、提交人、更新时间和使用量。Skill artifact 与 MCP JSON 按版本不可变保存；安装会复制或归一化到目标作用域。商店文件不得包含访问令牌等秘密。

### Agent 技能制作与平台共享

Agent 通过 `discover_tools(bundle_id="skill_authoring")` 加载制作工具，通过 `skill_market` 加载当前宿主平台的搜索、查看和发布工具，通过 `skill_management` 加载维护策略及下架操作。完整参数定义只在加载后的下一模型轮次披露，不依赖供应商原生 tool_search。

内置 `skill-creator` 指导创建私有草稿、编写 SKILL.md 及辅助资源、校验并冻结快照，再按用户意图安装到会话或个人空间、导出 ZIP、发布到当前平台。格式校验不代表行为试跑。市场技能可复制为独立草稿进行修改。

新市场条目仅同一宿主平台可见。默认 `creator_only`（仅创作者发新版），也支持 `platform_members`（平台成员共同发新版）。共同维护不授予修改维护权限或下架权限；这些操作由创作者或管理员执行。新版需要当前 `base_version` 和 `expected_revision`，冲突时先合并再发布；历史版本不可覆盖。发布可携带 `idempotency_key` 去重。

新 Agent 会话安装使用会话独立目录，避免共享 workspace 时串入其他会话。发布不会自动设置平台预置，安装不会自动跟随市场新版升级。市场“使用量”当前统计安装次数。

市场查询和发布可携带 `session_id`，由服务端确定当前平台；管理端使用有管理权限的 `platform_id`。嵌入身份直接绑定其认证平台。详情与安装同样检查平台归属，嵌入用户可直接发布，无需原生账号。新增 `GET /api/v1/extensions/{entry_id}` 和 `PATCH /api/v1/extensions/{entry_id}`；PATCH 接受 `expected_revision`、`maintenance_policy`、`status`（active/unlisted）。

兼容性：启动时增量迁移市场表并保留旧版本制品；旧条目缺少平台归属时不出现在新平台市场中，需要管理员在备份后核实归属并回填 platform_id/author_key。不要猜测归属或继续全局公开。回退业务版本前应备份数据库；可禁用新增工具入口，保留新增列、历史版本和审计记录。没有新增环境变量、端口或跨平台发布入口。新增依赖 PyYAML，用于安全解析技能文件头。

## API

- `GET /api/v1/capabilities?session_id=...`
- `POST /api/v1/capabilities/skills`
- `POST /api/v1/capabilities/mcp?scope=user|session|platform`
- `POST /api/v1/capabilities/mcp/{name}/connect?session_id=...`
- `POST /api/v1/capabilities/mcp/{name}/disconnect?session_id=...`
- `POST /api/v1/capabilities/mcp/{name}/oauth/start?session_id=...`
- `GET /api/v1/capabilities/mcp/oauth/callback`
- `DELETE /api/v1/capabilities/mcp/{name}/oauth?session_id=...`
- `GET /api/v1/extensions`
- `POST /api/v1/extensions`
- `POST /api/v1/extensions/{entry_id}/install`
