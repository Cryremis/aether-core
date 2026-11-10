# 上下文压缩与故障恢复

## 调研

| Agent | 触发与目标 | 裁剪、摘要与保留 | 失败路径 |
|---|---|---|---|
| Codex CLI / OpenAI Responses | CLI 有 `/compact` 和 `model_auto_compact_token_limit`。Responses 另有服务端阈值和独立 compact 请求。 | Responses 由服务端返回可继续使用的压缩项。公开文档未给出具体裁剪对象、分块、输出额度或客户端无模型检查点算法。 | 独立 compact 的输入也必须能装入窗口。公开文档未承诺失败后继续任务。 [CLI 命令](https://learn.chatgpt.com/docs/developer-commands?surface=cli) · [配置](https://learn.chatgpt.com/docs/config-file/config-reference) · [Responses 压缩](https://developers.openai.com/api/docs/guides/compaction) |
| Gemini CLI | `/compress` 可强制运行。所查源码默认达到模型窗口 50% 开始压缩，并按大小切分，目标保留约 30% 最近历史。 | 先给工具结果设预算并把大结果写到临时文件，再总结旧段、保留近期上下文。摘要与近期历史重新估算。 | 上次摘要失败后，自动路径只尝试无模型截断。若无收益则返回原历史和状态，源文件没有承诺下一次模型请求一定成功。 [压缩实现](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/context/chatCompressionService.ts) |
| opencode Core v2 | 手动、自动或供应商溢出触发，按模型窗口预留摘要和输出空间。下述行为以 `dev` 分支 v2 为准。 | 可以用原生压缩或滚动摘要，保持完整归档、有限的近期原文。摘要输入逐级剪短工具输出、工具参数，必要时只留用户文本或省略旧交换。 | 摘要请求溢出后有限次降低目标，发布压缩结果。同一个模型步骤再次溢出可终止。源码里的确定性裁剪主要服务于摘要输入，v2 规范未承诺无模型摘要恢复。 [压缩实现](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/compaction.ts) · [会话规范](https://github.com/anomalyco/opencode/blob/dev/specs/v2/session.md) |
| Aider | 历史 token 超过 `max_chat_history_tokens` 时总结历史，预算独立于主模型完整窗口。 | 尽量留最近约半个历史预算，旧段按摘要模型输入上限取样，摘要只收用户和助手正文。摘要和尾部仍过大时有限深度递归。 | 摘要逐个试配置的模型，全部失败时该函数抛错。该函数没有无模型压缩回退，所引源码没有规定最终 UI 文案。 [历史实现](https://github.com/Aider-AI/aider/blob/main/aider/history.py) · [配置](https://github.com/Aider-AI/aider/blob/main/aider/website/docs/config/aider_conf.md) |

旧 AetherCore 给旧历史反复分块并递归合并。每块都依赖模型给出完整正文，任何一次失败都可能废掉整轮结果。现网报告的三次重试使用相同的 4096 输出额度，均被思考占满。旧流程又在模型摘要之后无限量拼回精确事实，并且压缩候选只需低于硬上限。手动命令未接住模型截断异常。

## 预算和状态

请求估算使用最终协议消息、工具定义和会话校准系数。输入硬上限为 `模型窗口 - 主请求输出预留 - 安全余量`，默认安全余量 4000。软触发在输入上限的 85%，压缩目标为 80%。若系统指令、工具定义和最新用户消息本身超过软目标，目标抬到可保留原文的最小值。供应商已经拒绝过请求时，本次目标额外受原估算值 85% 约束。最新用户消息完整保留，不可裁剪部分超过硬上限时返回结构化预算错误。

| 阶段 | 工作 | 继续条件 |
|---|---|---|
| L0 | 将较旧的大工具输出替换成带工具名、参数摘录、行数、错误片段和 message ID 的回执。相同读取或命令折叠旧回执。对允许裁剪思考的协议去掉旧思考正文，保持工具调用与结果配对。 | 独立测量缩减，达到目标即可提交。 |
| L1 | 按 API 工具依赖组切割旧历史，从用户、助手、工具记录提取有界的原文交接，并保存来源 ID。近期尾部和最新用户原文留在活动上下文。 | 达到目标即可提交。 |
| L2 | 仅在本地交接过大、但仍有空间容纳更短摘要时调用。摘要使用裁剪过的单个输入，正常只发一次。专用请求关闭 DeepSeek 思考或选择协议支持的最低强度。截断时先使用剩余输出空间，再缩小输入，不递归合并。 | 最多 3 次请求和 20 秒。默认给足部署允许的输出额度，显式指定初始值时从至少 8192 起步，仍受部署额度和窗口约束。精确引用最多占目标 5%，并有 256 token 上限。候选只有实际达到目标才采用。 |
| L3 | 按依赖组逐步收缩近期历史，在能放下时保留本地交接。原文保留在账本，活动窗口用归档边界引用。 | 优先达到目标。如果最小工作集和交接无法同放，优先保证原文输入与可执行预算。没有收益则不提交。 |

显式配置原生压缩的部署继续使用原生检查点。原生端点失败后回到确定性路径。DeepSeek 工具协议若要求回放思考，L0 保留其仍在活动窗口中的必要字段，L1/L3 可以按组移出旧历史。OpenAI Responses 和 Anthropic 的客户端替换会开启新的 context epoch，避免把旧签名当成新上下文。

## 代码与可观测性

- `context_pipeline.py` 中的 `compact_session_async` 统一预算、选择候选并拒绝过期写入。`_prune_history` 和 `_handoff` 负责本地路径。`_compact_to_fit` 负责极端兜底和归档边界。成功事件记录策略、节省量、影响条数、降级代码和耗时。
- `summarizer.py` 中的 `CompactionSummarizer.summarize` 编译摘要请求并限制次数。截断异常携带供应商 usage，下一次请求据此上调输入校准。每次模型调用的 prompt、completion、reasoning usage 和 finish reason 仍记录在 `context_attempts`。
- `engine.py` 中的 `_stream_user_compaction` 报告裁剪、摘要、结束阶段。模型失败降级到本地候选。没有可提交候选时手动命令返回具体提示，用户可继续对话。取消沿原 run 取消路径传播。
- `history_store.py` 保留同一 message ID 的原始版本，并避免把已经主动裁剪的推理恢复到模型窗口。模型窗口、审计转录与原文恢复仍走原有 v4 账本。

## 灰度和回退

先灰度少量宿主，使用脱敏的长会话验证手动、软阈值、供应商溢出和断线重连。默认 `CONTEXT_COMPACTION_MODEL_ENABLED=true`。设为 `false` 后重启可切换到纯本地压缩，便于独立验证裁剪效果和停用出问题的模型。无需迁移历史数据。回滚应用版本前保留原账本，使用同样的旧会话回读检查确认归档连续。

在日志中按策略和错误码聚合压缩次数、降级比例、耗时和节省比例。从 `context_attempts` 对照摘要失败时的输出额度与 reasoning usage。验收应包含同一会话压缩后紧接着发一条消息、全文按 ID 回读、重复压缩不重复创建检查点。需要单独统计仍被供应商拒绝的请求和真正不可裁剪的输入。
