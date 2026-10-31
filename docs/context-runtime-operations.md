# 上下文运行、升级与内网验证

本说明对应 2026-10-31 的实现。设计与源码对照见 [可靠性设计](agent-context-reliability-design.md) 和 [二次审计矩阵](context-runtime-audit.md)。参考顺序为 Claude Code → Codex → OpenCode；本地版本和 Claude Code 研究镜像的来源限制已记录在设计第 2 节。

## 已接入生产链路

| 范围 | 当前行为 |
| --- | --- |
| 多轮回传 | Chat 保留 `reasoning_content` 与部分正文；Responses 保留原始 output items、加密思考；Messages 保留完整内容块、签名、redacted thinking 和工具块顺序 |
| 请求编译 | API 字段允许列表；调用／结果依赖闭合；扩展体不得替换身份、消息和工具；输出预算在编译与发送时一致 |
| 采样 | 留空继承，`model_defaults` 停止该字段继承，明确值包含 0；不再自动注入温度 0.7、frequency 0.4、presence 0.3 或 high；参数来源可见 |
| 部署范围 | 全局／平台采样只继承到相同模型、地址与协议；切换模型、协议、账户、部署修订、模板或原生前缀时显式建立 epoch，旧协议状态不跨绑定复用 |
| 压缩 | 先归档；旧工具结果提供原文引用；按完整工具步骤保留近期上下文与当前用户目标；独立无工具摘要任务覆盖全部选中数据；失败、过期候选和无法释放空间的候选不替换历史 |
| 精确事实 | 摘要附加原文 IP、URL、路径、反引号内容；系统／技能规则固定保留；事实超预算时明确阻断，不静默丢弃 |
| 原文访问 | `conversation_search`、`conversation_read` 只检索当前会话；模型读取不返回思考或原生签名；宿主大资料通过 `host_context_read` 分段获取 |
| 重试和工具 | 重试仅限首片前且有上限；已有输出后传输中断标为 incomplete；不通过撤掉工具降级；工具回执防止同一已提交操作重复执行，未知副作用状态停止自动执行 |
| 事件恢复 | v2 envelope 含 event / turn / step / attempt / message / part / epoch 与 continuation_of，数据库回放保留；流先持久化再发布；快照携带 last_seq；浏览器按 after_seq 补回，不重发任务 POST；宿主预览按 run/seq 去重并标注 incomplete |
| 停止与时限 | 请求预检、摘要、溢出恢复、退避等待、模型流及工具执行统一响应取消和运行时限；未知工具副作用不自动补执行；失败／取消保留一份展示与规范模型记录 |
| 离线检查／诊断 | 设置面板的离线协议检查走真实编译器，不调用模型；按会话权限查询请求与摘要任务元数据，不返回原文、思考或签名 |
| 历史与分支 | 原始记录和模型活动窗口分开；展示沿当前检查点来源展开，编辑／重跑后不复活旧分支；可在压缩前的消息处继续分支操作 |

这些是工程行为，不代表已在内网完成模型质量对照。模型原始输出本身改写 IP 或混用语言时，运行时不会静默纠正后伪造通过结果。

## 内网 vLLM 设置

1. 平台或个人模型设置选择 **Chat Completions / vLLM**，填写地址与服务实际 model 别名。未设置鉴权的本地服务可不填 key。地址以 `/v1` 为例，客户端使用 `/v1/chat/completions`。
2. “部署上下文上限”填写实际 `max_model_len`，不要填模型宣传窗口。输出上限与服务允许值匹配，包含该协议计入输出的思考 token。输入预算还会预留 4000 token 安全余量；过小窗口需要调整部署／输出设置。未知模型未配置窗口时，生产管线不再采用旧 200K 假设，使用最多 65536 token 的 `unverified_fallback`，工作台明确提示未验证；这不保证服务支持 64K，实际部署限制更小时必须填写真实值。目录匹配窗口标为 `catalog_unverified`，同样不能代替 max_model_len；显式配置优先于两者，回退时也可明确配置经过验证的旧额度。
3. 未确认模型需要哪些采样参数时勾选对应“模型默认”。本版本不会自动清掉已有环境、平台或个人明确保存的惩罚值。旧配置中的受管 extra_body 参数会在读取时纳入有效预算，重新保存时迁入 sampling；矛盾配置明确拒绝，避免两个来源互相覆盖。
4. 默认回传文本思考。DeepSeek V3.2／V4／Flash 或官方地址自动选择 DeepSeek 兼容语义；内网自定义别名请明确选择 **deepseek-thinking**。思考模式携带工具时所有历史 assistant 必须回传 `reasoning_content`，包括合法空值与跨用户轮次；不能关闭回传或改成 reasoning。服务实际关闭思考时用其真实开关（例如合法 extra_body.thinking 或 chat_template_kwargs）并声明部署模式。其他明确不接受文本思考的服务可用 generic 并关闭；原生协议始终保留原生状态。
5. `reasoning_effort` 只接受服务支持的名称或 1–100 数值，不自动把未知力度映射成另一档。服务特有的 thinking 开关／budget 仍可通过合法 extra_body 指定。配置本身不能证明模板和 reasoning/tool parser 正确，需核对推理服务启动信息。

Responses 使用 `/responses`；Messages 使用 `/messages` 和 Anthropic 请求头。原生协议目前接受规范化文本和函数工具，附件通过现有文件工具读取；不会把 Chat 多模态块直接塞成原生文本。原生压缩仅在 Responses 中显式勾选且服务支持 `/responses/compact` 时使用；不支持时明确失败，原始窗口保留。取消勾选后可在新 epoch 使用客户端摘要。

DeepSeek Responses 与 OpenAI Responses 的推理格式不同：前者回放明文 `reasoning_text` items，不自动请求 encrypted_content，也不默认支持原生 compact；后者保存服务返回的加密状态。DeepSeek 官方思考接口忽略 temperature／frequency_penalty／presence_penalty，top_p 还有具体模型的范围和固定值规则；这不能推导为内网 vLLM 也忽略这些参数。离线检查给出 `sent_unverified`、`model_default` 或已知官方忽略／限制状态，真实部署效果须实测。[DeepSeek 思考规则](https://api-docs.deepseek.com/guides/thinking_mode/)、[Responses 规则](https://api-docs.deepseek.com/guides/responses_api/)。

部署能力 JSON 是人工声明而非验收证明。例如：

```json
{"service_kind":"vllm","service_version":"实际版本","served_model_name":"实际别名","context_window":65536,"max_output_tokens":8192,"thinking_mode":"enabled","reasoning_required_with_tools":true,"reasoning_parser":"实际值","tool_parser":"实际值","chat_template_digest":"实际模板哈希","usage_semantics":"openai"}
```

上限优先于模型目录，不因未知别名依赖公网查询。`supported_parameters` 可限制明确参数；`effort_options` 可声明服务枚举。`deployment_revision` 用于手动标记配置变更；模板、tokenizer、parser 等完整声明也参与部署绑定。声明保存到现有 sampling JSON，不新增配置表字段。摘要使用独立任务，清除主任务的 stop、输出 schema 等限制；输出额度默认沿用主任务，可用 `compaction_max_output_tokens` 明确设置，最多模型调用次数 `compaction_max_calls` 默认 32、允许 1–64，不能收敛时保留原文并失败。

设置面板点击“离线协议检查”调用 `POST /api/v1/llm/validate`，结果仅表示真实编译器与占位状态投影通过，不连接模型、不执行工具、不验证服务端签名。`GET /api/v1/agent/{session_id}/context/diagnostics?limit=50&before=游标` 返回该会话请求的指纹、来源引用、排除原因、参数来源与映射、预算、tool revision、回传数量、usage 和终态。`before` 可省略，最多 100 项；不返回输入正文、思考、原生 payload、密钥或请求头。独立摘要 attempt 标明 purpose，不会恢复成用户对话。

## 升级和回滚

升级前停止写入并备份 `STORAGE_ROOT`，至少包含 SQLite 数据库及相关 WAL、sessions 目录和加密密钥。不要在运行中直接复制 SQLite 主文件而遗漏 WAL。

消息版本由 v2 升至 v3。首次加载旧会话会在原 session.json 同目录保留 `session.pre-context-v3.<内容哈希>.json`，并把原始消息写入账本；备份按内容幂等生成。只有唯一可确认的旧展示思考会恢复为文本思考，无法确认的历史标记 legacy_incomplete，已丢失的数据不能凭空恢复。

现有 SQLite 增加四张表：`context_records`（消息版本）、`context_snapshots`（带 revision 的当前状态）、`context_attempts`（请求指纹及部分响应）、`context_tool_executions`（工具回执）。账本快照是加载时的权威状态，旧 session.json 是兼容投影；CAS 拒绝过期写入覆盖和删除后复活。所有历史检索限制在当前会话。删除会话会清理这四张表中相应数据。

配置 `CAPABILITY_SECRET_ENCRYPTION_KEY` 时，新账本 payload、响应和回执使用现有 Fernet 密钥派生规则加密；需要持续保存同一密钥。旧 session.json、迁移备份及现有其他运行文件仍沿用原格式，不能据此声称整个存储目录都已加密。密钥轮换需先完成数据迁移，不能直接更换导致历史无法读取。

归档量约为原始历史加变更版本、运行响应和一个当前快照；长工具输出与持续流式快照会增加写入。当前不按时间自动删除原文，避免破坏检查点引用。容量应按实际会话长度监控，删除会话用于释放其逻辑记录，SQLite 物理回收由既有数据库运维流程处理。

回滚需停写并**整体恢复升级前数据库与 sessions 目录**，保留升级后副本供补偿。单独换旧代码或恢复一个 session.json 不能回滚权威账本。当前实现未提供 CONTEXT_RUNTIME_MODE、CONTEXT_DIAGNOSTICS_MODE 或 CONTEXT_NATIVE_COMPACTION_ENABLED 环境开关；设计中的这些名称不是可用部署参数。实际控制为 sampling.native_compaction、reasoning_replay 等模型配置及 `AGENT_MAX_CONTINUATIONS`（默认 2，范围 0–16）。

## 无外部副作用的协议诊断

在部署 AetherCore 后端的内网机器执行；只请求模型并执行内存 echo，不运行 shell、访问宿主接口或创建额外 Agent：

```powershell
cd C:\Work\AetherCore\backend
# 如服务需要鉴权，在本机设置 AETHERCORE_PROBE_API_KEY；不要把密钥写入报告。
python -m app.cli.context_probe --base-url http://your-vllm-host:8000/v1 --model your-model-alias --context-window 65536 --max-output 4096 --turns 30 --output .runtime/model-context-report.json
```

上面的 65536 是示例，必须替换为实际部署限制。报告包含逐轮 expected / actual、严格匹配、工具次数、思考字符数和错误，退出码 0 表示该次探针通过；不会写入 key 或思考原文。可用 `--api-key-env` 指定本机其他环境变量，`--api-format` 选择原生协议。探针是客户端协议诊断，不替代整个 Agent 的长任务、压缩及副作用恢复集成验收。

建议先跑 3 轮，再跑 10/30/50 轮，保存 vLLM 版本、模型权重／量化、chat_template、reasoning_parser、tool_call_parser、max_model_len 与参数。按设计第 18 节对照 AetherCore、Claude Code、Codex 和 OpenCode，分别比较固定请求与各自完整 Agent 工作流。首轮成功不能证明多轮和反复压缩后质量已对齐。

## 本地检查

```powershell
cd C:\Work\AetherCore\backend
python -m pytest tests -n 4 -q
cd C:\Work\AetherCore\frontend
npx tsc --noEmit
npm run test:frontend -- --maxWorkers=1 --minWorkers=1
npm run test:embed
npm run build
```

本地验证覆盖协议夹具和确定性运行时契约；真实 vLLM、Responses 与 Messages 服务在本环境不可用，需单列为待部署验证。默认端口、沙箱执行器和网络隔离均未调整；本次不自动重启服务，也不自动提交 Git。

最新工作区验证记录见 [二次审计矩阵](context-runtime-audit.md)。构建仍报告既有 vendor 循环分包和大 chunk 提示。内网模型质量、真实服务协议与同机性能对照未在此环境运行。
