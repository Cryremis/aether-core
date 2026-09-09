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
