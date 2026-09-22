# AetherCore /host/bind 安全修复说明

日期：2026-09-22。对应 [PR #5](https://github.com/Cryremis/aether-core/pull/5) 的收敛方案。

## 1. 问题与范围

原实现先按 conversation key 或 conversation ID 查找，未命中时按 session ID 回退。回退路径没有完整验证持久化记录中的平台、用户和删除状态，可能绑定不属于目标用户或平台的已有会话。显式 session ID 与旧 conversation key 同时存在时，还可能先选中另一对话。

该接口要求平台密钥认证。风险涉及持有平台凭证的调用方，或将终端用户提交的 ID 转发给接口的宿主代理；不是匿名请求仅凭会话 ID 即可访问。

本次修复只处理 AetherCore 自身的目标选择与授权。移除先前提交新增的 `/sessions/{session_id}/binding` 接口，不涉及宿主 Redis 恢复、页面上下文查询或 agent runtime 生命周期。

## 2. 绑定契约

| 请求 | 处理 |
| --- | --- |
| 提供 session_id | 直接查询该会话；不使用 conversation_key 改变目标 |
| 只提供 conversation_id | 直接查询该对话；不使用 conversation_key 改变目标 |
| 同时提供两个 ID | 两个 ID 必须对应同一对话 |
| 没有显式 ID | 保留既有按 conversation_key 查找及创建新会话的流程 |
| 显式 ID 不存在、已删除、跨平台、跨用户或相互冲突 | 拒绝，不转入新建流程 |
| 显式 ID 为空字符串 | 请求模型校验拒绝；新建请省略 ID 或传 null |

对所有显式目标，必须满足：

1. 持久化 conversation 存在。
2. `deleted_at` 为 null。
3. `platform_id` 与平台密钥对应的平台一致。
4. `external_user_id` 与请求中声明的宿主用户一致。
5. 如提供 conversation_id，其值与记录匹配。

授权失败发生在加载/创建运行态、注入宿主上下文、更新工具或认证信息之前。拒绝请求不会更改原会话，也不会签发新的 embed token。

复用现有错误协议：平台密钥无效为 401；上述显式目标校验失败由 ValueError 转为 400；请求模型错误为 422。目标不存在、已删除与归属不匹配使用统一错误文案。

## 3. 改动文件

| 文件 | 职责 |
| --- | --- |
| [registry.py](../backend/app/host/registry.py) | 明确区分已有目标绑定与新建，统一验证显式目标 |
| [host.py](../backend/app/schemas/host.py) | 说明两个 ID 的已有资源语义，拒绝空字符串 |
| [test_host_binding_ownership.py](../backend/tests/test_host_binding_ownership.py) | 覆盖拒绝条件、无副作用以及合法复用 |

没有新增 API、配置或数据库迁移，不修改 embed SDK、工作台前端、run、SSE、subagent 或 workspace 实现。

## 4. 项目边界

AetherCore 是会话归属和生命周期的事实来源。已有平台认证接口 `GET /api/v1/host/conversations?external_user_id=...` 可按平台和用户列出会话；使用该接口的宿主必须从已认证身份取得用户 ID，不能直接信任终端用户提供的身份声明。

AiClusterHub 的业务权限、Redis 记录、页面实例及命令投递由 AiClusterHub 自己负责。其缓存缺失不应要求 AetherCore 提供专用“恢复缓存”接口。已有会话列表不返回页面权限，宿主也不能将工具回调携带的页面数据直接视为可信授权。

该安全修复可以独立合并和部署。宿主的列表查询方案不依赖本 PR 新增能力；两者不再要求捆绑部署。生产环境应单独安排安全补丁上线，不能把宿主侧预校验当作 AetherCore 自身检查的替代。

## 5. 兼容性与部署

显式 ID 现在表示“绑定已有资源”。以前通过自定义且不存在的 session_id 创建会话的调用方，需要改为省略两个 ID，让 AetherCore 生成 ID，再保存绑定响应。conversation_key 仍可用于新建流程中的复用。

已有合法会话可正常复用，即使请求带有另一个会话的旧 conversation_key，也不会改变显式目标。软删除的会话不能通过 /bind 重新获得工具或认证上下文。

无需清空宿主 Redis或删除历史对话。回滚不会要求数据库回滚，但会重新暴露原有校验缺口。

## 6. 验证

从仓库根目录运行：

```bash
PYTHONPATH=backend .venv/bin/python -m pytest -n 0 \
  backend/tests/test_host_binding_ownership.py \
  backend/tests/test_auth_and_sessions.py \
  backend/tests/test_host_agent_orchestration.py -q
```

新增测试按 session-only、conversation-only 和双 ID 三种请求覆盖不存在、已删除、跨平台、跨用户和对话不匹配；断言拒绝后不加载或创建运行态、不附加宿主状态、不写会话元数据。正向测试验证合法新建、显式 ID 优先、按对话 ID 复用，以及平台认证要求。

串行执行用于避免此前发现的并行 worker 收集列表不一致问题。本地测试通过不代表已完成生产部署验证。

## 7. 单独治理的 runtime 稳定性

run 终态收敛、SSE 断开、真实取消、重启恢复、子任务结果回传与取消、共享 workspace 和并发执行保护应使用独立的问题记录与回归测试。它们不属于本次归属校验补丁，不在本 PR 中扩大修改范围。
