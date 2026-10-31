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
| 重试和工具 | 输出前退避重试；输出后先提交片段再安全接续；空响应、可恢复协议差异与可选参数拒绝自动恢复；不撤掉工具；工具回执防止重复执行，未知副作用不自动重做 |
| 事件恢复 | v2 envelope 含 event / turn / step / attempt / message / part / epoch 与 continuation_of，数据库回放保留；流先持久化再发布；快照携带 last_seq；浏览器按 after_seq 补回，不重发任务 POST；宿主预览按 run/seq 去重并标注 incomplete |
| 停止与时限 | 请求预检、摘要、溢出恢复、退避等待、模型流及工具执行统一响应取消和运行时限；未知工具副作用不自动补执行；失败／取消保留一份展示与规范模型记录 |
| 离线检查／诊断 | 设置面板的离线协议检查走真实编译器，不调用模型；按会话权限查询请求与摘要任务元数据，不返回原文、思考或签名 |
| 历史与分支 | 原始记录和模型活动窗口分开；展示沿当前检查点来源展开，编辑／重跑后不复活旧分支；可在压缩前的消息处继续分支操作 |

这些是工程行为，不代表已在内网完成模型质量对照。模型原始输出本身改写 IP 或混用语言时，运行时不会静默纠正后伪造通过结果。

## 模型可见预算和主动压缩

达到有效输入预算 75% 后，模型收到带具体数值的 `[runtime context]` 状态：使用率 `used_percent`、已用输入、剩余输入、距自动压缩的 `tokens_left`、输出预留、安全余量及窗口来源。百分比的分母是 **部署窗口 − 输出预留 − 4000 token**；95% 是运行时强制压缩线。`tokens_left` 表示距强制压缩还可使用的输入 token，`remaining_input_tokens` 表示距全部有效输入预算的余量，两者不要混为一谈。数值均为估算，不是整个任务的总额度。

提醒明确标注 `snapshot_basis=before_this_notice`：数值计算于本条提醒加入之前；提醒本身仍计入最终请求预算和工作台状态，可能让显示值略高。未声明部署窗口时仍标明未验证，不把启发式估算当服务端精确计数。

Chat/vLLM 将提醒放在本次请求尾部的临时 system 消息，不改写根系统提示或永久历史。Responses／Messages 使用隐藏的有序状态消息，在已闭合的历史末尾追加并持久保留回放；同一 epoch／检查点内按使用率每 5 个百分点更新，重复预检不会不断追加，也不替换签名之前的旧内容。客户端检查点和模型切换会排除旧预算快照，避免把旧余量当任务事实；原生 compact 则仍发送完整窗口，由服务返回规范下一窗口。

只读工具 `get_context_remaining` 让模型主动查询余量，遵循会话工具授权。运行中根据该次实际系统前缀、工具集合、当前已收到的模型正文／思考及工具历史重新估算，标注 `current_history_before_this_tool_result`；不调用模型、不执行外部操作，也不修改主请求的 usage 校准状态。尚未有请求预算时返回 `available=false, tokens_left=null`。提醒只推荐当前可用的查询／压缩工具。

Agent 可以调用 `context_compact`，运行时仍等全部工具结果闭合后执行。客户端 `balanced` 保留最近 4 轮工具原文视图，摘要近期窗口参考比例为 35%、最多 12000 token；`aggressive` 保留最近 2 轮，参考比例为 20%、最多 6000 token，并使用更小的旧工具结果视图。完整 API 依赖组和当前用户任务继续受到保护，这些比例不是允许切断工具循环的硬裁剪线。自动超限恢复使用 aggressive；原生 compact 的压缩程度由服务端决定。

这对齐 Codex 的预算提醒、余量查询及主动管理窗口的原则；不复制仅适用于其特定模型／配置的窗口重置或笔记扩展语义。本次未新增环境变量、端口或存储 schema。回退本轮预算感知实现可使用上一提交的兼容 v3 运行时，原始会话资料仍保留。

## 内网 vLLM 设置

1. 平台或个人模型设置选择 **Chat Completions / vLLM**，填写地址与服务实际 model 别名。未设置鉴权的本地服务可不填 key。地址以 `/v1` 为例，客户端使用 `/v1/chat/completions`。
2. “部署上下文上限”填写实际 `max_model_len`，不要填模型宣传窗口。输出上限与服务允许值匹配，包含该协议计入输出的思考 token。输入预算还会预留 4000 token 安全余量；过小窗口需要调整部署／输出设置。未知模型未配置窗口时，生产管线不再采用旧 200K 假设，使用最多 65536 token 的 `unverified_fallback`，工作台明确提示未验证；这不保证服务支持 64K，实际部署限制更小时必须填写真实值。目录匹配窗口标为 `catalog_unverified`，同样不能代替 max_model_len；显式配置优先于两者，回退时也可明确配置经过验证的旧额度。
3. 未确认模型需要哪些采样参数时勾选对应“模型默认”。本版本不会自动清掉已有环境、平台或个人明确保存的惩罚值。旧配置中的受管 extra_body 参数会在读取时纳入有效预算，重新保存时迁入 sampling；矛盾配置明确拒绝，避免两个来源互相覆盖。
4. 默认回传文本思考。DeepSeek V3.2／V4／Flash 或官方地址自动选择 DeepSeek 兼容语义；内网自定义别名可选择 **deepseek-thinking**。要求回传字段不等于要求每次必须产生思考：有内容原样回传，未返回／null 时在请求投影补 `reasoning_content=""`，合法工具照常执行，不报错、不因此建立 epoch。原始账本保留缺失与空串的区别。DeepSeek 思考工具模式会自动兼容旧配置中的关闭回传／字段别名冲突；其他服务仍按 generic 配置执行。原生协议保留其实际返回的状态。
5. `reasoning_effort` 只接受服务支持的名称或 1–100 数值，不自动把未知力度映射成另一档。服务特有的 thinking 开关／budget 仍可通过合法 extra_body 指定。配置本身不能证明模板和 reasoning/tool parser 正确，需核对推理服务启动信息。

Responses 使用 `/responses`；Messages 使用 `/messages` 和 Anthropic 请求头。原生协议目前接受规范化文本和函数工具，附件通过现有文件工具读取；不会把 Chat 多模态块直接塞成原生文本。原生压缩不可用或暂时失败时自动尝试客户端检查点；保留归档来源，并建立适合新窗口的协议状态。

## 恢复优先与用户错误边界

思考缺失／空值是正常兼容情况，绝不能触发用户错误、停止工具或强制重建历史。可规范化的文本块、工具参数对象，以及额外候选均进行兼容处理；多候选只消费主候选，不串接不同候选的正文或工具。

空响应、断流、畸形 SSE 等故障由运行时退避恢复。已有片段先归档并作为下一次接续输入，未完成的工具提议不执行；原生响应缺少完整状态时基于原文来源重建阶段继续。服务明确拒绝 stream_options、reasoning_effort 等可选参数时，按实际错误协商并重编译下一请求，不更换模型、删除历史或撤掉工具。目录未声明的可选参数使用兼容默认，不在本地直接升级为任务故障。

`AGENT_MODEL_RETRY_ATTEMPTS` 默认 **6**（0–16，0 表示管理员显式关闭自动故障恢复），`AGENT_MODEL_RETRY_WINDOW_SECONDS` 默认 **120 秒**（1–1800）。采用 1、3、5、10 秒等退避，并尊重数值型 Retry-After；每次有效生成结束后重新提供故障恢复额度，避免长任务共用少数几次机会。用户取消和运行总时限仍优先生效。摘要每个片段可恢复 3 次，重试计入既有 compaction_max_calls；不会无限请求或重复执行已完成工具。

只有外部服务在恢复预算内持续不可用、鉴权／配额／必要能力无法满足，或者容量、授权与未知副作用等条件无法安全继续时才结束当前尝试。普通用户只收到保留进度的友好提示；parser、上游原始错误、密钥相关信息和堆栈仅用于服务端诊断，不通过错误 SSE、运行详情或前端错误卡片输出。历史错误 SSE 也经过同一过滤边界。

本轮未改变默认端口、存储 schema 或沙箱行为。回退重试节奏可显式设置旧上限 `AGENT_MODEL_RETRY_ATTEMPTS=3`、`AGENT_MODEL_RETRY_WINDOW_SECONDS=60`；不要回退成“没有思考就中断”的旧逻辑。

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
