# 宿主平台操作 Agent 与 Subagent 设计

## 1. 结论

AetherCore 已经具备嵌入式会话的雏形：宿主可以通过 `/api/v1/host/bind` 创建或复用会话、注入宿主工具，前端可以使用 embed token 创建新会话、发送消息、订阅 SSE、中断运行、查看会话摘要和历史会话。

当前缺口是：

1. 缺少面向宿主后端的统一 Control Plane API，隐藏/恢复、分页查询、运行详情、Webhook 等能力不完整。
2. 运行状态主要保存在进程内存中，SSE 断线重连只能在运行未结束时 replay，历史 run 没有独立持久化。
3. 会话没有 `visibility` / `archived` / `deleted_at` 等生命周期字段，不能实现隐藏与恢复。
4. 没有 subagent 的领域模型、通信协议、权限继承、预算与深度控制。

推荐采用“宿主 Control Plane + 持久 Run/Event Store + 中央 Orchestrator”的架构。不要把 subagent 简单实现成一个 HTTP 转发工具，而应该把“子运行”作为一等资源，用 append-only 事件流和显式状态机驱动。

## 2. 现状能力矩阵

| 能力 | 当前状态 | 说明 |
| --- | --- | --- |
| 创建会话 | 部分支持 | `bind` 可创建/复用；前端 `bootstrap` 可基于 embed 会话创建新会话 |
| 列出会话 | 支持 | embed 用户维度列表，缺少分页、过滤、包含隐藏项 |
| 查看会话 | 支持 | 摘要包含消息、transcript、runtime、workboard、elicitation、active_run |
| 重命名/删除 | 支持 | 现在是硬删除，没有恢复能力 |
| 隐藏/恢复 | 不支持 | 数据模型无 `visibility`、`archived`、`deleted_at` |
| 发送消息 | 支持 | `/api/v1/agent/chat` 返回 SSE |
| 获取结果 | 部分支持 | 运行中通过 `result/completed` SSE；结束后主要从 transcript 恢复 |
| 查看运行状态 | 部分支持 | `active_run` 只反映活跃运行；历史 run 无资源详情 |
| 断线重连 | 部分支持 | 运行存活时 `AgentRunService` 可 replay；进程重启或 run 结束后不可用 |
| 中断运行 | 支持 | `/abort` 触发取消并保留部分输出 |
| 人工输入 | 支持 | elicitation 接口 |
| Subagent | 不支持 | 只有搜索工具提示中出现 sub-agent 建议，没有运行时实现 |

## 3. 设计原则

1. **资源化**：Conversation、Run、Agent、SubagentRun、Event 都有稳定 ID、状态机和并发版本。
2. **事件优先**：所有状态转换都产生 append-only 事件；API 返回的是事件投影。
3. **最小信任**：平台密钥只用于 bind 和平台级管理，用户数据操作必须携带平台、外部用户、会话和 scope 绑定的短期 token。
4. **权限向下收敛**：子 agent 的工具权限是父 agent 权限、agent 定义、平台策略的交集，禁止越权。
5. **显式边界**：默认深度为 1 层；其他资源边界由平台显式配置，初始版本不设置默认数值上限，调度器负责排队与背压。
6. **失败隔离**：子 agent 崩溃只影响对应子运行，父 agent 收到结构化错误并可重试或降级。
7. **可恢复**：Run 与 Event 落库；SSE 使用 `Last-Event-ID` 或 cursor 恢复。
8. **可观测**：统一 trace/span ID，暴露生命周期、耗时、token、成本、工具调用和错误分类。

## 4. 总体架构

```text
Host Backend
  ├─ bind / principal-token exchange
  └─ Host Control API
        │
        ▼
AetherCore Control Plane
  ├─ Conversation Service
  ├─ Run Manager
  ├─ Run Event Store
  ├─ Agent Registry
  ├─ Orchestrator
  └─ Webhook / Outbox
        │
        ▼
Runtime Plane
  ├─ Parent Agent Run
  ├─ Subagent Run (bounded fan-out)
  ├─ Tool Executor
  └─ Sandbox / Workspace
```

### 4.1 Control Plane

面向宿主后端和管理工作台，只做资源生命周期、权限校验、幂等、查询和事件订阅，不直接承载 LLM 循环。

### 4.2 Runtime Plane

继承现有 `AgentEngine`、`AgentRunService`、`ToolService` 与 sandbox 能力。`AgentRunService` 从“内存执行器”升级为 Run Manager 的执行适配器；真正的 run 状态、事件和结果由持久层负责。

### 4.3 Integration Plane

保留现有 host tools、skills、system prompts、MCP、host APIs。宿主工具调用继续通过 Tool Executor 代理，并注入当前 principal 的认证，不把宿主密钥暴露给模型。

## 5. Host Control API

以下路径均以平台认证 + principal token 为前提，响应使用统一 `ApiResponse`。

### 5.1 会话资源

```http
POST   /api/v1/host/conversations
GET    /api/v1/host/conversations
GET    /api/v1/host/conversations/{conversation_id}
PATCH  /api/v1/host/conversations/{conversation_id}
POST   /api/v1/host/conversations/{conversation_id}/hide
POST   /api/v1/host/conversations/{conversation_id}/restore
DELETE /api/v1/host/conversations/{conversation_id}
POST   /api/v1/host/conversations/{conversation_id}/fork
```

创建请求必须支持：

```json
{
  "external_user_id": "u_123",
  "external_org_id": "org_1",
  "conversation_key": "crm-case-456",
  "title": "Customer case 456",
  "visibility": "normal",
  "context": {},
  "tools": [],
  "skills": [],
  "idempotency_key": "host-case-456"
}
```

列表请求支持：

```text
?cursor=&limit=&include_hidden=true&include_archived=true
&status=active&updated_after=&order=updated_at desc
```

Patch 支持：

```json
{
  "expected_revision": 12,
  "title": "New title",
  "visibility": "normal",
  "pinned": true,
  "metadata": {}
}
```

### 5.2 消息与运行

```http
POST /api/v1/host/conversations/{conversation_id}/messages
GET  /api/v1/host/runs/{run_id}
GET  /api/v1/host/runs/{run_id}/events
POST /api/v1/host/runs/{run_id}/cancel
POST /api/v1/host/runs/{run_id}/elicitation/{request_id}/responses
GET  /api/v1/host/conversations/{conversation_id}/assistant-messages
```

发送消息响应返回 `run_id` 和初始状态；执行模式三选一：

1. `response_mode=stream`：立即升级为 SSE。
2. `response_mode=poll`：返回 `202`，宿主轮询 run。
3. `response_mode=webhook`：返回 `202`，AetherCore 通过签名 Webhook 推送终态。

SSE 响应必须带事件 ID：

```text
id: 42
event: content_delta
data: {...}
```

客户端可用 `Last-Event-ID: 42` 或 `?cursor=42` 续读。

### 5.3 最近 AI 回复

会话详情和历史接口不能只暴露最终回复。宿主和主 agent 都可以按需读取最近 N 条 AI 回复，并得到每条回复对应的运行状态：

```http
GET /api/v1/host/conversations/{conversation_id}/assistant-messages
    ?limit=20
    &include_subagents=true
    &run_status=completed,failed
```

返回结构：

```json
{
  "items": [
    {
      "message_id": "msg_...",
      "run_id": "run_...",
      "agent_id": null,
      "subagent_run_id": null,
      "content": "AI visible reply",
      "run_status": "completed",
      "created_at": "2026-09-18T00:00:00Z"
    }
  ],
  "next_cursor": null
}
```

规则：

1. `content` 只返回用户可见文本，不混入内部推理和工具原始输出。
2. `include_subagents=true` 时返回 subagent 的最终可见回复。
3. 每条回复都带 run 状态，页面可以直接展示“生成中 / 已完成 / 失败 / 已取消”。
4. 主 agent 读取自己的上下文时同样使用这个投影，避免只能看到最终一条回复。

### 5.4 运行详情

```json
{
  "run_id": "run_...",
  "conversation_id": "conv_...",
  "session_id": "sess_...",
  "status": "running",
  "created_at": "2026-09-18T00:00:00Z",
  "started_at": "2026-09-18T00:00:01Z",
  "finished_at": null,
  "last_event_cursor": 87,
  "result": null,
  "error": null,
  "usage": {
    "input_tokens": 1234,
    "output_tokens": 234,
    "tool_calls": 4,
    "subagent_runs": 2
  },
  "subagents": []
}
```

### 5.5 Webhook

事件：

```text
run.created
run.started
run.waiting_input
run.completed
run.failed
run.cancelled
run.timed_out
```

每个请求带时间戳、nonce、事件 ID 和 HMAC 签名。Webhook 状态与重试记录进入 outbox，达到最大重试后进入 dead-letter，管理台可手动重放。

## 6. 数据模型

### 6.1 conversations 扩展

在现有表上增量添加：

```sql
visibility TEXT NOT NULL DEFAULT 'normal'; -- normal | hidden
archived_at TEXT;
pinned_at TEXT;
deleted_at TEXT;
last_run_id TEXT;
expected_revision INTEGER NOT NULL DEFAULT 0;
```

语义：

1. `hidden` 只影响默认列表，仍可通过显式 `include_hidden=true` 访问。
2. `archived_at` 表示归档，可恢复。
3. `deleted_at` 默认软删除，满足审计和误删恢复；只有管理员或 retention 任务可硬删除。
4. `conversation_key` 保持平台 + 外部用户维度幂等复用。

### 6.2 agent_runs

```text
run_id PK
conversation_id FK
session_id
parent_run_id nullable
agent_id nullable
root_run_id
status
request_json
result_json
error_json
cancellation_reason
idempotency_key
event_cursor
created_by_kind
created_by_id
trace_id
created_at / started_at / finished_at
expires_at
```

唯一约束：

```text
(platform_id, external_user_id, idempotency_key)
```

活跃运行约束可先在单进程锁中实现，数据库层使用 partial unique index：

```sql
CREATE UNIQUE INDEX one_active_run_per_session
ON agent_runs(session_id)
WHERE status IN ('queued', 'running', 'waiting_input');
```

### 6.3 run_events

```text
event_id PK
run_id FK
seq
type
payload_json
visibility
created_at
UNIQUE(run_id, seq)
```

规则：

1. 只追加，不更新。
2. 每个事件由 Run Manager 统一写入并发布。
3. SSE、Webhook、审计和投影消费同一个事件流。
4. 保存策略区分“完整审计事件”和“用户可见事件”，默认对外只返回可见事件。
5. 事件投影生成 `assistant_messages` 视图，支持按最近 N 条读取并附带 run 状态。

### 6.4 agents 与 agent_versions

```text
agent_id PK
platform_id
name
display_name
current_version
created_at

agent_versions
version_id PK
agent_id FK
system_prompt
model_policy
allowed_tools
denied_tools
max_subagent_depth
subagent_tools_enabled
timeout_seconds
token_budget
created_at
```

平台可以定义平台级 agent，宿主可以注入会话级 agent 定义；版本不可变，修改生成新版本。

### 6.5 subagent_runs

```text
subagent_run_id PK
parent_run_id FK
child_run_id FK
role
task
handoff_context_json
allowed_tools
status
result_json
created_at / finished_at
```

Subagent 是 run 之间的有向关系，不要求一定是 conversation。这样不会污染用户可见会话列表，也便于并行 fan-out。

## 7. Run 状态机

```text
queued
  -> running
  -> waiting_input
  -> running
  -> completed

任意非终态:
  -> cancelling -> cancelled
  -> timed_out
  -> failed
```

状态定义：

| 状态 | 语义 |
| --- | --- |
| `queued` | 已持久化，等待 worker |
| `running` | 正在推理或执行工具 |
| `waiting_input` | 等待用户或宿主回答 elicitation |
| `cancelling` | 已收到取消请求，正在清理工具与子运行 |
| `completed` | 成功终态，`result_json` 可用 |
| `failed` | 异常终态，错误结构化 |
| `cancelled` | 主动取消终态，可保留部分结果 |
| `timed_out` | 预算超时终态 |

终态记录不可变；后续继续对话创建新 run。

## 8. Subagent 编排

### 8.1 两种模式

**Agents as tools（推荐第一阶段）**

父 agent 通过工具创建有边界的子运行，等待结果并汇总。适合调研、代码分析、验证等可并行任务。

**Handoff（第二阶段）**

父 agent 将控制权交给另一个 agent。AetherCore 记录 `handoff_started`、`handoff_completed` 事件，并把必要上下文显式传递。适合客服路由、专家接管等场景。

初始落地策略：

1. 主 agent 可以使用 subagent 工具。
2. subagent 自身不开放 subagent 工具，即默认最大深度为 1 层。
3. 后续开放多层时必须通过平台配置显式调整，不允许隐式递归。
4. 并行 subagent 不设置默认数值上限，由平台按资源和模型配额配置，调度器负责排队、并发执行和背压。

### 8.2 内置工具

```text
subagent_create
subagent_send_message
subagent_wait
subagent_list
subagent_cancel
subagent_get_result
```

`subagent_create` 参数：

```json
{
  "name": "repo_explorer",
  "task": "Analyze authentication boundaries",
  "instructions": "Only inspect backend code.",
  "allowed_tools": ["file_read", "search"],
  "model": "balanced",
  "mode": "async",
  "visibility": "user_read_only"
}
```

### 8.3 通信模型

父与子不共享内存，只通过持久化消息通信：

```text
parent message queue -> child run
child result / event stream -> parent orchestrator
```

父 agent 可见的信息包括：

1. 子 agent 的任务描述。
2. 子 agent 的状态。
3. 最终结构化结果与显式 artifacts。

子 agent 不可见：

1. 父会话完整历史，除非父显式传入摘要或片段。
2. 父 agent 的系统提示词。
3. 未列入 `allowed_tools` 的工具。
4. 宿主密钥和超出 principal scope 的数据。

用户可见性：

1. subagent 对用户可见，工作台以只读卡片展示任务、状态、最终回复和耗时。
2. 用户默认不能直接向 subagent 发消息；追加沟通由主 agent 发起。
3. 宿主控制面可以按权限查看完整状态与审计事件，但默认不替 subagent 发消息。
4. subagent 的最终回复对用户可见，内部工具调用和原始日志仅审计可见。

### 8.4 上下文与工作区

子 agent 默认使用独立 workspace：

```text
root_workspace/
  parent/
  subagents/run_.../
  shared/artifacts/
```

父传子通过 `handoff_context_json` 和显式 artifact 引用。子写父必须产出 artifact，父通过受控路径读取。这样既能协作，又能避免并发写坏工作区。

### 8.5 权限与预算

初始版本不设置人为偏低的默认并发、token 或工具调用上限，避免阻塞大量并行专家任务。平台可以按资源和模型配额配置边界；未配置时由调度器负责：

1. 按平台、用户、模型、会话分层排队。
2. 在资源不足时平滑背压，而不是直接拒绝所有任务。
3. 暴露排队数、运行数、等待时长，便于容量规划。
4. 支持平台配置 timeout、token、工具调用等预算。

唯一默认深度限制是 1 层：subagent 不获得 subagent 工具。平台可以收紧权限，不能被宿主或模型放宽。所有工具权限取交集：

```text
effective_tools =
  parent_effective_tools
  ∩ agent_version.allowed_tools
  ∩ platform_policy
  ∩ runtime_disabled_capabilities
```

### 8.6 取消与失败

1. 父 run 取消时级联取消所有未终态子 run。
2. 单个子 run 超时或失败不自动取消父 run，父收到结构化错误。
3. 子 run 等待用户输入时，父 run 进入 `waiting_input` 并携带 `subagent_run_id`。
4. 子 run 的工具执行必须能响应取消信号，复用现有 tool task cleanup。
5. Worker 崩溃后 run 由 reconciliation 恢复为 `failed` 或重新排队，事件保持单调。

### 8.7 结果主动回传

subagent 最终回复不能要求主 agent 轮询才知道结果。Orchestrator 在子 run 进入终态时必须主动回传：

1. 写入 `subagent_result_ready` 事件。
2. 将结构化结果转换成一条普通上下文消息，追加到父 run 上下文末尾。
3. 若父 run 正在运行，向父 agent 发送 wake 信号；若父 run 已结束，下一条用户消息继续时会在上下文末尾看到该结果。
4. 若父 run 正在 `subagent_wait`，立即解除等待并返回该结果。
5. 结果同时进入父会话 transcript 和 subagent run 的 `result_json`，页面、主 agent 和审计看到同一份数据。

注入消息示例：

```json
{
  "role": "tool_result",
  "content": "Subagent repo_explorer completed: ...",
  "metadata": {
    "subagent_run_id": "run_...",
    "parent_run_id": "run_...",
    "result_kind": "subagent_final_reply"
  }
}
```

## 9. 事件协议扩展

在现有 `AgentEvent` 基础上增加：

```text
run_queued
run_status_changed
subagent_created
subagent_started
subagent_status_changed
subagent_result_ready
subagent_completed
subagent_failed
handoff_started
handoff_completed
webhook_scheduled
```

事件 payload 必须包含：

```json
{
  "run_id": "run_...",
  "parent_run_id": null,
  "root_run_id": "run_...",
  "trace_id": "trace_...",
  "seq": 42
}
```

对现有前端兼容：未知事件忽略；新增字段可选。旧字段 `session_id` 继续保留。

## 10. 安全设计

1. **Principal token**：bind 成功后返回短期 token，绑定 `platform_id + external_user_id + conversation_id + scopes + expiry`。Host Control API 只接受该 token 做用户级操作。
2. **平台密钥**：只允许 bind、token exchange、平台级配置和管理操作，不允许直接读取任意用户会话。
3. **Scope**：`conversation:read`、`conversation:write`、`run:create`、`run:read`、`run:cancel`、`subagent:create`。
4. **请求幂等**：创建会话与发送消息必须带 `Idempotency-Key`，重复请求返回原资源。
5. **并发控制**：会话和工具目录使用 `expected_revision` 乐观锁。
6. **事件可见性**：`internal` 事件只进审计，不进入宿主或前端流。
7. **审计**：记录 principal、IP、action、resource、trace_id、结果。
8. **限流**：按平台、用户、会话、模型和 subagent 深度分层限流。

## 11. 可靠性设计

1. Run 创建先落库再排队，API 成功即代表请求不会因进程退出丢失。
2. Worker 拉取 run 并写入 `run_started`；执行过程所有事件先落库再发布。
3. SSE 使用 cursor，不依赖内存 queue 存活。
4. 事件写入与业务投影解耦，投影可从事件重建。
5. Webhook 使用 outbox，避免事件写入成功但通知丢失。
6. 启动时 reconciliation：孤儿 running run 根据策略标记失败或重新入队。
7. 事件与 run 结果保留窗口可配置，满足审计和成本平衡。

## 12. 实施路线

### 阶段一：补齐宿主会话控制面（1 周）

1. conversations 增加 `visibility/archived/deleted/last_run_id`。
2. 新增 Host Conversation API：创建、列表、详情、patch、隐藏、恢复、软删除。
3. 列表支持 cursor 与过滤。
4. 前端工作台同步隐藏/归档状态。
5. 补齐权限与回归测试。

### 阶段二：Run/Event 持久化（1-2 周）

1. 新增 `agent_runs`、`run_events`。
2. `AgentRunService` 改为 Run Manager 适配器。
3. SSE 支持 event id 和 cursor。
4. 新增 run 详情、取消、结果查询。
5. 增加 reconciliation 与终态不变量测试。

### 阶段三：Subagent MVP（1-2 周）

1. 引入 agent registry 与内置 agent 定义。
2. 实现 agents-as-tools 模式的 `subagent_create/wait/cancel/get_result`。
3. 实现权限交集与一层深度控制；并发、时长、token 等边界交给平台配置和调度器。
4. 父子事件透传到 run timeline。
5. 前端展示 subagent 树与实时状态。

### 阶段四：生产化（1-2 周）

1. Webhook/outbox、重试、dead-letter。
2. OpenTelemetry trace、token/cost 统计。
3. 水平扩展：API 无状态化，run worker 队列化，锁迁移到数据库或 Redis。
4. 管理台支持 run 审计、事件重放和失败诊断。
5. Handoff 模式与更丰富的共享上下文策略。

## 13. 验收标准

1. 宿主后端可以在不打开工作台的情况下完成会话创建、隐藏、恢复、发送消息、轮询结果、订阅事件和取消。
2. 进程重启后，未完成 run 的状态可解释，已完成 run 的事件与结果可恢复。
3. SSE 断开 1 分钟后重连，不丢事件、不重复已确认事件。
4. 同一 `Idempotency-Key` 重复请求只创建一个会话或 run。
5. 创建会话时可以直接指定 `visibility=hidden`，隐藏会话默认不进入普通列表，但显式查询可访问。
6. 用户能看到 subagent 的任务状态和最终回复，但默认不能直接向 subagent 发消息。
7. 子 agent 无法访问父 agent 未授权工具；默认深度为 1 层，subagent 不获得 subagent 工具。
8. 取消父 run 后，所有子 run 与工具任务在超时时间内进入终态。
9. subagent 进入终态时主动产生 `subagent_result_ready`，并把最终回复追加到父 run 上下文末尾。
10. 宿主与页面可以获取最近 N 条 AI 回复，并附带每条回复的运行状态。
11. 全链路有 trace_id，管理台能查看每个 run 的状态、事件、耗时、token 与错误。
