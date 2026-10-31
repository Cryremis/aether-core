# 上下文可靠性二次审计与验收记录

日期：2026-10-31。范围：设计 C01–C24、模型配置到真实 HTTP 请求、流式归并、摘要、持久化恢复、前端与 universal 宿主预览。代码已按用户要求提交到 master；本地验证不等于内网部署，未重启生产服务。实施前基线为 `f287cb57a4f517998fc38a42e0f3e978a8605e99`。

## 对照依据

按用户要求依次阅读本地 Claude Code → Codex → OpenCode 源码，而非只比较功能文案：

| 项目与本地 commit | 本次对照文件 | 对齐的不变量 |
| --- | --- | --- |
| Claude Code `4b9d30f7953273e567a18eb819f4eddd45fcc877` | `src/services/compact/grouping.ts`、`compact.ts`、`src/utils/messages.ts` | 按完整 API 响应组切分；工具调用和结果闭合；固定规则与任务摘要分离 |
| Codex `f752c97f7d0be0cbef5cece327f52a16ad6fb1c6` | `codex-rs/core/src/context_manager/history.rs`、`compact.rs`、`compact_remote_v2.rs` | 保留事实、历史／输入修订、成对裁剪、原生状态和检查点替换；摘要不能覆盖并发新输入 |
| OpenCode `c10134729dd2ce00beb18604ec91f10319f59a78` | `packages/opencode/src/provider/transform.ts`、`session/compaction.ts` | 全部 assistant 思考按兼容字段回放，空字符串也是有效状态；展示思考与协议状态分开 |

Claude Code 目录为 README 明示的公开 source-map 研究镜像，不能作为官方当前版本证明。Codex 与 OpenCode 为本地官方仓库快照。AetherCore 对齐行为和失败语义，未复制整套提示词或声称实现结构相同。

协议同时对照 [DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[DeepSeek Responses](https://api-docs.deepseek.com/guides/responses_api/)、[OpenAI 推理状态](https://developers.openai.com/api/docs/guides/reasoning)。文档规则与内网 vLLM 实际能力分别记录，避免将官方 API 行为直接套给自部署服务。

## 逐项链路复查

下表“完成”指代码接入实际运行路径并通过离线契约／集成回归，不代表已完成真实模型质量验收。测试文件缩写：R=`backend/tests/test_context_reliability.py`，A=`backend/tests/test_context_reliability_audit.py`；其他原有测试继续运行。

| 设计项 | 生产接入点与完成行为 | 主要证据 |
| --- | --- | --- |
| C01 思考回传 | Engine → MessageAdapter → LlmClient → provider adapter；正常回复、工具步骤、跨用户轮次保留思考，空值不丢；原始思考保真；缺失文本思考在请求投影补空串，不打断运行 | A `test_deepseek_wire_replays_empty_and_all_prior_reasoning_after_tool_steps`；R 原生签名／加密块回放 |
| C02 截断续写 | 先提交正文与思考，独立 attempt 与 continuation_of；有界续写、规范消息和单份 UI 聚合分开 | A `test_engine_events_identify_continuation_and_canonical_message`、`test_length_continuation_has_one_persisted_ui_projection` |
| C03 传输中断 | 持久化部分响应后有界安全续跑；保留已完成工具回执，不重复执行；有效终态后的尾部断线不降级 | A `test_protocol_error_commits_partial_before_next_turn`、`test_http_tail_failure_after_valid_terminal_does_not_replay_or_downgrade` |
| C04 工具降级 | 不通过移除工具重试掩盖协议错误；tools=[] 明确关闭时模型幻觉调用不执行 | A `test_explicit_tools_disabled_cannot_execute_hallucinated_calls`；既有 LlmClient 错误用例 |
| C05 控制字段 | schema 与真实编译器共享保护列表；禁止覆盖历史、身份、服务端截断、background、previous_response_id 等 | A 参数化 `test_runtime_owned_fields_cannot_bypass_context_compiler`；R 额外体冲突迁移 |
| C06 协议完整性 | 请求前验证闭合组、重复 id、缺失结果；必需思考字段允许为空；未完成参数不执行 | A `test_invalid_tool_group_is_not_hidden_by_compaction`、`test_deepseek_missing_response_reasoning_executes_valid_tool_and_continues` |
| C07 真实摘要 | Engine 等待异步 CompactionSummarizer；独立无工具请求，全源覆盖 map/reduce，保留原文事实，预算与收敛有界 | A `test_summary_is_independent_of_output_schema_and_reduces_multiple_chunks`；R 摘要失败保留原历史 |
| C08 工具结果视图 | 大结果先归档，再生成带原文引用的受限视图；conversation_read/search 可恢复且支持版本摘要引用 | R 工具结果原文分页；A `test_history_search_respects_small_total_budget_and_version_refs` |
| C09 事实来源 | HistoryStore 原始版本账本 + CAS 快照；展示与活动窗口分开，摘要不删除源数据 | R `test_history_survives_compaction_snapshot_and_is_session_scoped` |
| C10 多次检查点 | 新检查点替代旧活动摘要；来源图展开当前分支；跨模型从原生不透明检查点恢复可读来源 | R 12 次检查点；A `test_epoch_from_opaque_checkpoint_recovers_original_task_and_ui_sources` |
| C11 参数默认 | 不注入旧温度／惩罚／high；继承、模型默认、明确值三态，0/false 保留；主任务 stop 不污染摘要 | R 参数默认用例；A 独立摘要用例；前端 llmOptions 测试 |
| C12 部署适用域 | 全局／平台继承匹配地址、模型、协议；部署 metadata 和 revision 参与 binding，切换建立 epoch | A `test_global_sampling_does_not_leak_to_another_deployment_or_protocol`、`test_profile_summary_and_deployment_revision_are_effective` |
| C13 原生协议 | Chat、Responses、Messages 单独编译／归并；DeepSeek 明文 reasoning 与 OpenAI 加密状态分开 | A `test_deepseek_responses_plain_reasoning_is_displayed_and_replayed`；R Anthropic 空签名思考 |
| C14 能力参数 | 版本化 generic／deepseek profile；部署支持参数与 effort 声明；stop、seed、parallel 映射，不支持的可选参数省略；服务明确拒绝时按字段协商 | A `test_stop_seed_parallel_controls_are_mapped_and_unsupported_optional_values_are_omitted`、原生嵌套 effort 迁移 |
| C15 实际限额 | 部署硬上限优先，不因内网别名强制获取公网目录；配置超限拒绝；未知窗口使用最多 64K 未验证保守估算并在实时／刷新状态提示 | A `test_deployment_limits_precede_catalog_and_bind_metadata`、`test_unknown_deployment_window_is_conservative_and_explicitly_unverified` |
| C16 最终预算 | Pipeline 调用 LlmClient 实际协议编译估算，包含真实回传思考／原生 items／schema；发送前同额度校验 | A `test_context_budget_uses_compiled_replay_policy_and_final_payload_limit` |
| C17 usage | Chat 请求流式 usage；每个工具轮更新，OpenAI 缓存不重复计数，Anthropic 分离缓存规范化；摘要 attempt 单独记录 | A `test_chat_requests_usage_and_records_it_during_tool_rounds`、`test_usage_semantics_does_not_count_openai_cache_twice` |
| C18 SSE | 严格 UTF-8 增量字节解析、BOM、多行 data、CR/LF、有效终态、固定消费主候选，避免混合多份回答、内容一致性校验 | A 每个 UTF-8 字节切点和坏编码用例；R 最终原生块去重；前端 sse 测试 |
| C19 投影与顺序 | API 字段允许列表；隐藏 UI 聚合不进入模型；Chat 系统历史保留时序；宿主资料与模板占位内容有界 | R `test_runtime_notices_keep_chronological_position`；A 宿主超大 metadata 用例 |
| C20 原生前缀 | 系统／技能规则及工具目录变更检测；旧签名不在新前缀回放；native compact 在整批工具结果闭合后执行 | A `test_native_rule_injection_enters_epoch_before_replaying_signature`、`test_native_compaction_closes_an_entire_multi_call_batch` |
| C21 稳定事件 | v2 envelope 包含 turn/step/attempt/message/part/epoch/continuation；数据库回放保留；旧行按 v1 读取 | A `test_event_envelope_survives_database_replay_and_legacy_rows`；前端／宿主游标测试 |
| C22 重连恢复 | 持久化先于发布；快照 last_seq、after_seq 回放、重复去除、有界补回；重连不重发任务 POST | R SSE 路由缺口／跨会话用例；前端 `sse.test.ts`；宿主过期预览测试 |
| C23 崩溃一致性 | ledger revision/CAS、未提交 attempt 恢复、工具执行回执；unknown 不自动重跑；结束失败／取消时重建展示 | R 原文 CAS、纯工具恢复；A `test_live_tool_cancel_retains_unknown_receipt_and_does_not_rerun`、失败展示用例 |
| C24 生产路径验收 | 实际 Engine → LlmClient → 捕获 HTTP 请求测试；配置表单保存／加载及按钮真实端点；兼容 ContextManager 不被生产调用 | 全量回归；A `test_offline_validation_uses_compiler_without_model_calls`；ModelOptionsFields 组件测试 |

## 本轮额外发现并补齐

初版审计新增 7 个失败的端到端契约，证明上一轮测试通过仍有实际缺口。继续追查后的修复包括：

- DeepSeek 空思考漏回传、旧记录缺失思考（原先错误的严格中断策略已由下文生产故障修订取代）；区分旧 DeepSeek coder 与当前 V3.2/V4/Flash 自动 profile，自定义别名可显式选择。
- 预算与最终回传策略不一致；部署限额被目录遮盖；stream usage 未请求；缓存 token 重复计数；全局参数跨模型／协议泄漏。
- 主任务输出 schema、stop 污染摘要；超大源只做一轮摘要而无法收敛；摘要请求缺少独立诊断记录。
- 模型调用 context_compact 时尚未闭合本轮工具组；native 多调用批次也必须等全部结果；技能注入后原生签名前缀未重建。
- 不透明检查点切换模型时使用占位文本交接；压缩重新引入已经过滤的子 Agent 数据；失败／取消后存在模型部分回复和 UI 展示不一致。
- 多字节／换行边界 SSE 的不完整处理；DeepSeek Responses 明文推理事件遗漏；多 choices 需要始终固定主候选而不能混入其他回答；合法终态后的 HTTP 尾部错误降级。
- 摘要、预检、溢出恢复和退避未统一接入取消及运行时限；取消后工具效果未知的操作不允许自动重做。
- 实时 envelope 在数据库回放中丢失；宿主预览缺少去重与 incomplete 状态；诊断只有内部记录，缺少按会话权限读取的实际 API。
- 截断内容块在快照／历史中被归并为 done；浏览器终态仍等待 HTTP EOF；已补跨层 incomplete 与终态主动关闭读流，并覆盖 CR-only 分帧。
- 上层模型默认模式在下层明确赋值后仍残留；未知内网别名默认窗口仍沿用 200K；已修正有效模式列表，并公开未验证的保守窗口来源。
- 诊断原先统计归档中可回传思考，而非最终请求实际发送量；现在按最终 Chat 消息／Responses items／Messages blocks 分别计数并标注单位，显式禁用工具时不误报已发送工具。

新增 A 文件目前共 **57 个参数化后测试**，覆盖上述实际链路。模型 HTTP 使用固定协议夹具，工具只使用本地可控测试实现；没有调用内网模型或伪造实测报告。

## DeepSeek 回传要求的具体结论

带工具的思考模式会把全部历史思考加入上下文，因此只保留最近一次工具思考不够；普通最终回复即使没有调用工具，下轮也要连同 reasoning_content 回传。AetherCore 现在保存原始文本思考、区分缺失与空串、跨轮回放，并在真正发出请求时统一投影。

有原始文本思考就原样回传；服务未返回时，在需要该字段的 assistant 请求中发送 reasoning_content=""。原始账本继续区分缺失与空串，不伪造非空思考，不因缺失文本思考中断工具或建立 epoch。已压缩掉的步骤由检查点及原文引用替代。原生签名／加密状态的兼容处理独立于文本思考字段。

原生协议另行处理：OpenAI 的加密 item、Anthropic 的完整 signed/redacted blocks 保持结构和顺序；DeepSeek Responses 回放明文 reasoning_text，既处理增量也处理仅最终 item，避免重复展示。工具／系统前缀发生不兼容变化时先建立 epoch。

这与主流 Agent 在协议保真、闭合工具组、完整原生状态和显式检查点方面的原则一致。服务模板是否实际使用这些字段、模型能否稳定复制 IP 和保持中文，必须由内网实际部署验证。

## 本地验收与仅剩的部署验证

二次审计验收：后端 **535 passed, 1 skipped**；前端 Vitest **15 passed**；universal embed **9 passed**；TypeScript 类型检查、Python 编译检查、`git diff --check` 和前端生产构建通过。后端使用 `python -m pytest tests -n 4`，耗时 87.76 秒；跳过项为既有、默认关闭的 models.dev 公网实时测试，与上下文协议回归无关。sandbox 的本地 socket 限制会阻塞 asyncio，离线测试经授权在沙箱外运行。前端保留原有 vendor 循环分包及大 chunk 提示。

下列事项必须在内网完成，当前均明确标为未实测：

1. 核对具体权重／量化、vLLM 与 tokenizer 版本、chat_template、reasoning/tool parser、实际上下文及输出上限；将实际值写入部署声明。
2. 用 [运行说明](context-runtime-operations.md) 的协议探针执行 3、10、30、50 轮，确认必需思考回传、工具参数和原样标识符；随后通过真实 AetherCore 会话验证多次压缩、刷新重连、取消恢复与文件工具任务。
3. 在相同模型／推理服务／输入与采样下对照 Claude Code → Codex → OpenCode，分别测固定请求和完整 Agent 工作流；记录 IP 严格匹配、中文稳定性、工具正确率、任务完成率、延迟及 token 用量。模型生成的错误不能靠 UI 静默替换来掩盖。
4. 实际使用 Responses／Messages 时，再验证服务端真实签名、加密状态、usage 和原生 compact 能力；离线占位签名检查不是服务端验签。

没有把上述待实测事项包装为“效果已全面达到主流 Agent”。其余已发现的本地实现与接线问题已补齐；没有遗留依赖新增部署旗标或独立框架才能启动的半成品路径。配置、升级和整体回滚步骤见运行说明。

## 预算感知补充复查

后续针对模型主动管理上下文的复查发现：此前百分比主要进入工作台，Chat 模型只收到笼统警戒提示，原生协议没有相应数值提醒；工具声明的 balanced／aggressive 参数未真正区分异步执行策略。上述差异不能以“已全面对齐”概括。

本轮对照同一本地 Codex commit 的 `context/token_budget_context.rs`、`session/token_budget.rs`、`tools/spec_plan.rs` 与 `tools/handlers/get_context_remaining.rs`：其数值提醒和查询工具受 TokenBudget 功能／模型配置控制，不是所有 Codex 会话统一默认开启。AetherCore 按兼容服务实际能力实现相应行为：

| 项目 | 补齐后的真实接入 |
| --- | --- |
| 数值提醒 | Pipeline 在警戒区生成使用率、剩余输入、距自动压缩余量和预留额度，提醒自身计入最终预算；Chat 临时尾部消息，原生有序追加回放 |
| 原生前缀 | 提醒不改写根系统提示和已有状态；每 5 个百分点追加一次；实际下一请求保留上一输入前缀和 signed／encrypted items |
| 主动查询 | 注册 get_context_remaining，通过 Engine → ToolService → 实际前缀／工具投影重新估算，模型得到数值结果；查询不改写校准记录 |
| 压缩策略 | balanced／aggressive 分别控制旧工具视图保护范围和近期完整组预算，自动超限使用 aggressive；过期预算状态不进入新摘要 |
| 授权与失败 | 未授权工具不被推荐；未知预算返回 null；加入原生提醒后超过硬上限不先提交部分历史 |

新增 `backend/tests/test_context_budget_awareness.py`，捕获三种协议实际请求及下一步回传，覆盖预算数值、提示开销、原生前缀、跨阈值追加与去重、工具授权、查询校准隔离、策略差异及硬上限原子性。内网真实服务验签、tokenizer 精度和任务质量仍按前述清单验收。

预算感知补充后的最终验证：后端 **547 passed, 1 skipped**（100.13 秒）；新增预算回归 **12 项**；前端 Vitest **15 passed**；宿主适配器 **9 passed**；TypeScript、Python 编译检查、差异空白检查和生产构建通过。构建保留既有 vendor 循环分包和大 chunk 提示。

## 生产故障修订：恢复优先与用户错误边界

先前“缺少思考就拒绝执行工具”的策略有误，虽然相应断言曾通过，但它破坏了持续工作能力；本节明确取代该策略。有内容原样回传，无内容补空字段，工具参数完整且合法即可继续执行。

新增 test_agent_recovery.py 的 20 项参数化回归，覆盖十轮无思考工具循环、空响应、损坏流安全续跑、截断工具参数重生成、可选参数协商、跨工具轮重置重试预算、401 立即停止、退避提示、摘要重试、原生压缩降级及两类原生协议续跑。每个故障阶段默认最多重试 6 次、120 秒窗口，通过 AGENT_MODEL_RETRY_ATTEMPTS 和 AGENT_MODEL_RETRY_WINDOW_SECONDS 调整；回退配置可设为 3／60。不会重做已有回执的工具，未知副作用仍保留待核实状态。

运行失败、旧 error 事件回放及工作台错误卡片仅展示通用可操作提示；技术细节进入服务日志。最终全量后端验证为 **567 passed, 1 skipped**（97.94 秒），前端 **15 passed**、宿主 **9 passed**，类型检查与构建通过。未连接内网 vLLM，内网验收要求保持不变。
