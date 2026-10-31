# AetherCore 上下文管理机制详解

> 当前实现说明（2026 年 10 月 31 日）：聊天运行时使用异步 `ContextPipeline`、`ContextMessageAdapter`、原始历史账本及协议适配器。窗口先扣除真实输出预算与 4000 token 安全余量，再采用 75% 预警、95% 强制压缩、65% 压缩目标。压缩使用独立的真实结构化摘要，保留近期完整工具组、当前用户目标、系统／技能规则和原文事实；工具结果与旧消息仍可按稳定 ID 检索。候选失败或遇到新消息时不替换有效窗口。
>
> 已实现的协议、采样、恢复与升级操作见 [上下文运行说明](context-runtime-operations.md)；审计与目标验收见 [可靠性设计](agent-context-reliability-design.md)。下文关于 `ContextManager`、时间触发微压缩、会话记忆及旧阈值的部分保留为早期组件资料，不代表生产主循环行为。真实内网模型质量尚待部署验证。

## 目录

1. [概述](#概述)
2. [核心架构](#核心架构)
3. [触发时机](#触发时机)
4. [压缩策略](#压缩策略)
5. [阈值计算](#阈值计算)
6. [Token估算](#token估算)
7. [优先级管理](#优先级管理)
8. [最佳实践](#最佳实践)

---

## 概述

AetherCore的上下文管理机制旨在防止大语言模型对话超出上下文窗口限制。该机制通过多层防御策略，确保长对话能够持续进行而不因Token超限而失败。

### 设计目标

1. **主动预防**：在接近阈值时提前触发压缩，避免API返回prompt-too-long错误
2. **渐进压缩**：从轻量级微压缩到完整摘要，逐步减少上下文占用
3. **智能保留**：基于优先级判断保留关键消息，裁剪冗余内容
4. **容错机制**：电路断路器、冷却期等机制防止无限重试

---

## 核心架构

### 模块组成

```
app/services/context/
├── token_estimation.py      # Token估算服务
├── context_budget.py        # 上下文预算与阈值管理
├── priority_manager.py      # 消息优先级计算
├── message_compaction.py    # 微压缩策略
├── session_memory_compact.py # 会话记忆压缩
├── reactive_compact.py      # 响应式压缩（API错误触发）
├── context_manager.py       # 主协调器
├── truncate.py              # 截断工具
└── __init__.py              # 模块导出
```

### 数据流图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         ContextManager (主协调器)                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │
│  │TokenEstimator│ │ContextBudget│ │PriorityMgr │ │MessageCompactor│ │
│  │  Token估算   │ │   预算管理   │ │  优先级    │ │   压缩执行   │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
     ┌────────▼────────┐ ┌────▼────┐ ┌────────▼────────┐
     │ MicroCompact    │ │Session  │ │ ReactiveCompact │
     │   微压缩        │ │ Memory  │ │   响应式压缩    │
     │ (时间触发)      │ │ Compact │ │ (API错误触发)   │
     └─────────────────┘ └─────────┘ └─────────────────┘
```

---

## 触发时机

上下文压缩在以下时机触发：

### 1. 主动自动压缩（Auto Compact）

**触发条件**：
- Token使用达到自动压缩阈值（约93%有效窗口）
- 自动压缩功能已启用
- 不在递归源（session_memory、compact、marble_origami）
- 电路断路器未触发（连续失败次数 < 3）
- 冷却期已过（上次压缩后至少2轮）

**检查流程**：
```python
def should_auto_compact(messages, query_source):
    # 1. 功能启用检查
    if not auto_compact_enabled:
        return False

    # 2. 递归源检查（防止死锁）
    if query_source in {"session_memory", "compact", "marble_origami"}:
        return False

    # 3. 电路断路器检查
    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        return False

    # 4. 冷却期检查
    if compacted and turn_counter < COOLDOWN_TURNS:
        return False

    # 5. 阈值检查
    token_count = estimate_messages_tokens(messages)
    return token_count >= auto_compact_threshold
```

**触发阈值计算**：
```
有效上下文窗口 = 模型上下文窗口 - 预留输出Token
自动压缩阈值 = 有效上下文窗口 - 缓冲区Token (13,000)

示例（200K模型）:
有效窗口 = 200,000 - 20,000 = 180,000
自动阈值 = 180,000 - 13,000 = 167,000 (约93%)
```

### 2. 时间触发微压缩（Time-Based Micro Compact）

**触发条件**：
- 自上次助手消息间隔超过配置阈值（默认30分钟）
- 查询来源为主线程（repl_main_thread、agent）
- 存在可压缩的工具结果

**原理**：
当会话长时间暂停后恢复，服务器端的Prompt Cache已过期失效，此时清除旧工具结果可以减少需要重新发送的内容量。

```python
def maybe_time_based_microcompact(messages, query_source):
    # 1. 检查配置启用
    if not time_based_enabled:
        return None

    # 2. 检查来源是否为主线程
    if query_source not in {"repl_main_thread", "agent", None}:
        return None

    # 3. 找到最后助手消息
    last_assistant = find_last_assistant_message(messages)

    # 4. 计算时间间隔
    gap_minutes = (now - last_assistant_timestamp) / 60
    if gap_minutes < GAP_THRESHOLD_MINUTES:
        return None

    # 5. 收集可压缩工具ID
    compactable_ids = collect_compactable_tool_ids(messages)

    # 6. 保留最近N个，清除其余
    keep_set = set(compactable_ids[-keep_recent_count:])
    clear_set = set(compactable_ids[:-keep_recent_count])

    # 7. 替换被清除的内容
    for msg in messages:
        if msg.role == "tool" and msg.tool_call_id in clear_set:
            msg.content = "[旧工具结果内容已清除]"
```

### 3. 响应式压缩（Reactive Compact）

**触发条件**：
- API返回 `prompt is too long` 错误
- 消息组数量足够（至少2组）

**处理流程**：
```python
def reactive_compact_on_prompt_too_long(messages, error_message):
    # 1. 按API轮次分组消息
    groups = group_messages_by_api_round(messages)

    # 2. 从错误消息提取Token差距
    token_gap = calculate_token_gap_from_error(error_message)

    # 3. 从尾部剥离消息组直到满足限制
    remaining_groups, peeled_tokens = peel_groups_from_tail(groups, token_gap)

    # 4. 可选：剥离媒体内容（图片/文档）
    if strip_images_enabled:
        compacted_messages = strip_media_from_messages(compacted_messages)

    # 5. 确保开头不是助手消息（API要求）
    if compacted_messages[0].role == "assistant":
        compacted_messages.insert(0, synthetic_user_message)

    # 6. 返回压缩后消息
    return result_messages
```

### 4. 手动压缩

**触发条件**：
- 用户显式调用 `/compact` 命令
- 用户选择特定消息执行部分压缩

**特点**：
- 用户可提供自定义摘要指令
- 可选择压缩方向（from/up_to）
- 不受自动压缩阈值限制

### 5. 会话记忆压缩（Session Memory Compact）

**触发条件**：
- 会话记忆内容已初始化
- 会话记忆内容非空模板
- 无提取操作正在进行

**特点**：
- 使用预先构建的会话记忆替代传统摘要
- 保留最近的消息，裁剪旧的对话
- 需要正确处理消息边界

---

## 压缩策略

### 1. 微压缩（Micro Compact）

**定义**：轻量级压缩，不生成LLM摘要，直接清除工具结果内容。

**适用场景**：
- 时间间隔触发（缓存失效）
- 轻度上下文压力

**可压缩工具类型**：
```python
COMPACTABLE_TOOLS = {
    "read_file",       # 文件读取
    "write_file",      # 文件写入
    "edit_file",       # 文件编辑
    "glob",            # 文件搜索
    "grep",            # 内容搜索
    "web_search",      # 网络搜索
    "web_fetch",       # 网页获取
    "sandbox_shell",   # Shell执行
    "sandbox_command", # 命令执行
}
```

**处理方式**：
将工具结果内容替换为标记字符串：
```
[旧工具结果内容已清除]
```

**保留策略**：
保留最近N个可压缩工具结果（默认3个），确保模型有足够的上下文继续工作。

### 2. 会话记忆压缩（Session Memory Compact）

**定义**：使用预先构建的会话记忆摘要替代实时LLM摘要生成。

**优势**：
- 无需额外的API调用
- 更快的响应时间
- 更低的成本

**处理流程**：
```python
def try_session_memory_compaction(messages, auto_compact_threshold):
    # 1. 计算保留消息的起始索引
    start_index = calculate_messages_to_keep_index(messages, last_summarized_index)

    # 2. 调整索引以保持tool_use/tool_result配对
    start_index = adjust_index_to_preserve_api_invariants(messages, start_index)

    # 3. 过滤旧的压缩边界消息
    messages_to_keep = [
        msg for msg in messages[start_index:]
        if not msg.is_boundary_marker
    ]

    # 4. 创建边界标记和摘要消息
    boundary_marker = CompactBoundaryMarker(...)
    summary_message = create_summary_message(session_memory_content)

    # 5. 构建结果
    return [boundary_marker, summary_message, *messages_to_keep]
```

**关键算法**：`adjust_index_to_preserve_api_invariants`

此函数确保压缩不会破坏API消息格式的完整性：

1. **tool_use/tool_result配对**：
   - 如果保留的消息中有tool_result，必须包含对应的tool_use消息
   - 向后查找匹配的tool_use块，调整起始索引

2. **thinking块合并**：
   - 流式传输可能产生多个共享相同message.id的助手消息
   - 包含这些消息以便normalizeMessagesForAPI正确合并

```
示例场景：
消息存储（压缩前）：
  Index N:   assistant, message.id: X, content: [thinking]
  Index N+1: assistant, message.id: X, content: [tool_use: ID]
  Index N+2: user, content: [tool_result: ID]

如果 startIndex = N+1：
  - 无调整：thinking块被排除
  - 调整后：startIndex = N，包含thinking块
```

### 3. 完整压缩（Full Compact）

**定义**：调用LLM生成对话摘要，替换大量历史消息。

**触发时机**：
- 自动压缩阈值触发且微压缩无效
- 手动调用 `/compact`
- Token使用达到阻断限制

**处理流程**：
```python
def compact_conversation(messages, context, custom_instructions):
    # 1. 执行PreCompact钩子
    hook_result = execute_pre_compact_hooks(trigger, custom_instructions)

    # 2. 构建摘要请求
    compact_prompt = get_compact_prompt(custom_instructions)
    summary_request = create_user_message(content=compact_prompt)

    # 3. 剥离图片（避免摘要请求超限）
    messages_to_summarize = strip_images_from_messages(messages)

    # 4. 调用LLM生成摘要
    summary_response = stream_compact_summary(messages, summary_request)

    # 5. 创建后压缩附件
    file_attachments = create_post_compact_file_attachments(...)
    skill_attachments = create_skill_attachment_if_needed(...)

    # 6. 执行SessionStart和PostCompact钩子
    hook_messages = process_session_start_hooks('compact')
    post_compact_hook_result = execute_post_compact_hooks(...)

    # 7. 构建结果
    return {
        boundary_marker,
        summary_messages,
        attachments,
        hook_results,
        ...
    }
```

### 4. 部分压缩（Partial Compact）

**定义**：围绕用户选择的消息索引进行部分压缩。

**方向选项**：
- `from`：压缩索引之后的消息，保留之前的消息（保留Prompt Cache）
- `up_to`：压缩索引之前的消息，保留之后的消息（Prompt Cache失效）

**使用场景**：
用户通过消息选择器精确控制压缩范围。

### 5. 响应式压缩（Reactive Compact）

**定义**：在API返回prompt-too-long错误时的紧急处理。

**消息分组**：
```
按API调用轮次分组：
Group 0: [system, user_query_1, assistant_response_1, tool_results_1]
Group 1: [user_query_2, assistant_response_2, tool_results_2]
...
```

**剥离策略**：
从最旧的组开始剥离，保留足够的组供摘要使用。

---

## 阈值计算

### 阈值层级

```
┌─────────────────────────────────────────────────────────────────────┐
│                     模型上下文窗口 (200,000 tokens)                   │
├─────────────────────────────────────────────────────────────────────┤
│  有效窗口 (180,000) = 200K - 预留输出 (20K)                          │
│  ├─────────────────────────────────────────────────────────────────┤│
│  │  自动压缩阈值 (167,000) = 有效窗口 - 缓冲区 (13K)                 ││
│  │  ├─────────────────────────────────────────────────────────────┤│
│  │  │  警告阈值 (147,000) = 自动阈值 - 缓冲区 (20K)                ││
│  │  │  ├─────────────────────────────────────────────────────────┤│
│  │  │  │  错误阈值 (147,000) = 自动阈值 - 缓冲区 (20K)            ││
│  │  │  │  ├───────────────────────────────────────────────────────┤│
│  │  │  │  │  阻断限制 (177,000) = 有效窗口 - 手动缓冲 (3K)        ││
│  │  │  │  └───────────────────────────────────────────────────────┤│
│  │  │  └─────────────────────────────────────────────────────────┤│
│  │  └─────────────────────────────────────────────────────────────┤│
│  └─────────────────────────────────────────────────────────────────┤│
└─────────────────────────────────────────────────────────────────────┘
```

### 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `context_window_default` | 200,000 | 默认上下文窗口大小 |
| `context_window_1m` | 1,000,000 | 1M上下文窗口大小 |
| `max_output_tokens_for_summary` | 20,000 | 压缩操作预留输出Token |
| `auto_compact_buffer_tokens` | 13,000 | 自动压缩缓冲区 |
| `warning_threshold_buffer_tokens` | 20,000 | 警告阈值缓冲区 |
| `error_threshold_buffer_tokens` | 20,000 | 错误阈值缓冲区 |
| `manual_compact_buffer_tokens` | 3,000 | 手动压缩缓冲区 |
| `max_consecutive_compact_failures` | 3 | 最大连续失败次数 |

### 警告状态计算

```python
def calculate_token_warning_state(token_usage, model):
    threshold = auto_compact_threshold if auto_compact_enabled else effective_window

    percent_left = max(0, int((threshold - token_usage) / threshold * 100))

    is_above_warning = token_usage >= warning_threshold
    is_above_error = token_usage >= error_threshold
    is_above_auto_compact = token_usage >= auto_compact_threshold
    is_at_blocking = token_usage >= blocking_limit

    # 确定警告级别
    if is_at_blocking:
        level = WarningLevel.Blocking
    elif is_above_error:
        level = WarningLevel.Error
    elif is_above_warning:
        level = WarningLevel.Warning
    else:
        level = WarningLevel.Normal

    return TokenWarningState(percent_left, level, ...)
```

## 附录：模型上下文窗口映射

### 动态模型能力服务

模型上下文窗口通过 `ModelsRegistry` 动态获取：

**数据来源优先级**：
1. **本地缓存** `~/.cache/aethercore/models.json`（优先）
2. **models.dev API** `https://models.dev/api.json`（自动刷新）
3. **最小内置配置**（完全不可用时的fallback）

**关键特性**：
- **自动刷新**：每小时后台刷新模型配置
- **离线可用**：网络不可用时使用缓存或内置配置
- **环境控制**：`AETHERCORE_DISABLE_MODELS_FETCH=true` 禁用网络请求
- **手动刷新**：调用 `refresh_models()` 立即更新

**核心组件**：

```python
# ModelsDevClient - 负责数据获取和缓存
client = ModelsDevClient()
data = client.fetch_from_models_dev()  # 从API获取
client.save_to_cache(data)             # 保存缓存
cached = client.load_from_cache()      # 加载缓存
client.start_background_refresh()      # 启动后台刷新

# ModelsRegistry - 提供模型查询
registry = ModelsRegistry()
model = registry.get_model("claude-opus-4")
context = registry.get_context_window("claude-sonnet-4")
output = registry.get_max_output_tokens("gpt-4o")
```

**models.dev 数据格式**：

```json
{
  "anthropic": {
    "id": "anthropic",
    "name": "Anthropic",
    "models": {
      "claude-opus-4": {
        "id": "claude-opus-4",
        "name": "Anthropic: Claude Opus 4",
        "limit": {
          "context": 200000,
          "input": null,
          "output": 32000
        },
        "cost": {
          "input": 15,
          "output": 75,
          "cache_read": 1.5,
          "cache_write": 18.75
        },
        "capabilities": {
          "reasoning": true,
          "temperature": true,
          "tool_call": true
        },
        "status": "active",
        "release_date": "2025-05-22"
      }
    }
  }
}
```

**模型匹配策略**（最长匹配优先）：

```python
def get_model(model_id):
    candidates = []
    for provider in providers.values():
        for model_key, model in provider.models.items():
            score = 0
            # 精确匹配（最高优先级）
            if model_key == model_id_lower:
                score = 100
            # 前缀匹配
            elif model_id_lower.startswith(model_key):
                score = len(model_key) + len(model.id)
            # 包含匹配
            elif model_key in model_id_lower:
                score = len(model_key)

            if score > 0:
                candidates.append((score, model))

    # 返回最高分的模型
    return max(candidates, key=lambda x: x[0])[1]
```

**缓存路径**：`~/.cache/aethercore/models.json`

**刷新间隔**：3600秒（1小时）

**请求超时**：10秒

**内置fallback模型**：
- Anthropic: claude-opus-4, claude-sonnet-4, claude-3-5-sonnet
- OpenAI: gpt-4, gpt-4o

### 1M上下文检测

```python
def get_context_window_for_model(model, betas):
    # 1. [1m] 后缀显式选择
    if "[1m]" in model.lower():
        return 1_000_000

    # 2. beta头检测
    if betas and "context-1m" in betas:
        return 1_000_000

    # 3. 模型能力检测
    capability = get_model_capability(model)
    if capability.max_input_tokens >= 100_000:
        return capability.max_input_tokens

    # 4. 默认值
    return context_window_default
```

---

## Token估算

### 估算策略

#### 1. 粗略估算（Rough Token Count）

```python
def rough_token_count(content, bytes_per_token=4):
    """
    基于字符长度的粗略估算。
    默认比例：4字符 ≈ 1 token
    """
    return int(len(content) / bytes_per_token)
```

#### 2. 文件类型感知估算

```python
def bytes_per_token_for_file_type(file_extension):
    """
    密集格式（JSON等）有更多单字符Token。
    JSON: 2字符 ≈ 1 token
    其他: 4字符 ≈ 1 token
    """
    dense_extensions = {"json", "jsonl", "jsonc", "yaml", "xml"}
    if file_extension.lower() in dense_extensions:
        return 2
    return 4
```

#### 3. 内容块估算

```python
def estimate_block_tokens(block):
    block_type = block.get("type", "text")

    if block_type == "text":
        return rough_token_count(block.get("text", ""))

    if block_type in ("image", "document"):
        return IMAGE_TOKEN_ESTIMATE  # 约2000

    if block_type == "tool_use":
        serialized = block.get("name") + json.dumps(block.get("input"))
        return rough_token_count(serialized)

    if block_type == "tool_result":
        return estimate_content_tokens(block.get("content"))

    # ...
```

#### 4. 消息估算

```python
def estimate_message_tokens(message):
    base_tokens = 4  # 角色和格式开销

    role = message.get("role")
    content = message.get("content")

    if role == "system":
        return base_tokens + rough_token_count(content)

    if role == "user":
        if isinstance(content, str):
            return base_tokens + rough_token_count(content)
        if isinstance(content, list):
            return base_tokens + sum(estimate_block_tokens(b) for b in content)

    if role == "assistant":
        tokens = base_tokens + estimate_content_tokens(content)
        for tool_call in message.get("tool_calls", []):
            tokens += rough_token_count(tool_call.function.name + tool_call.function.arguments)
            tokens += 10  # tool_call开销
        return tokens

    # ...
```

#### 5. 填充因子

```python
def estimate_messages_tokens(messages):
    """
    应用4/3填充因子进行保守估算。
    确保估算值略高于实际值，避免误判。
    """
    total = sum(estimate_message_tokens(msg) for msg in messages)
    return int(total * 4.0 / 3.0)
```

### API使用数据

从API响应获取精确Token计数：

```python
def count_from_usage(usage):
    """
    从API使用数据计算总上下文Token。
    包含输入、缓存和输出Token。
    """
    return (
        usage.input_tokens +
        usage.cache_creation_input_tokens +
        usage.cache_read_input_tokens +
        usage.output_tokens
    )
```

---

## 优先级管理

### 优先级级别

| 级别 | 说明 | 保留策略 |
|------|------|----------|
| `Critical` | 系统消息、压缩边界 | 必须保留 |
| `High` | 最近用户消息、活动工具结果 | 尽量保留 |
| `Medium` | 较旧对话、之前摘要 | 可压缩 |
| `Low` | 可压缩工具结果 | 首选压缩 |
| `Compactable` | 超龄工具结果 | 微压缩目标 |

### 优先级计算

```python
def calculate_priority(message, current_turn, file_state_cache):
    role = message.get("role")

    # 1. 系统消息 - Critical
    if role == "system":
        return MessagePriority.Critical

    # 2. 压缩边界 - Critical
    if is_compact_boundary(message):
        return MessagePriority.Critical

    # 3. 压缩摘要 - High
    if is_compact_summary(message):
        return MessagePriority.High

    # 4. 用户消息
    if role == "user":
        if contains_skill_invoke(content):
            return MessagePriority.High
        age = calculate_message_age(message, current_turn)
        if age <= recent_message_count:
            return MessagePriority.High
        return MessagePriority.Medium

    # 5. 助手消息
    if role == "assistant":
        if has_tool_calls(message):
            return MessagePriority.High
        age = calculate_message_age(message, current_turn)
        if age <= recent_message_count:
            return MessagePriority.High
        return MessagePriority.Medium

    # 6. 工具结果
    if role == "tool":
        tool_name = extract_tool_name(message)
        if tool_name in compactable_tool_names:
            age = calculate_message_age(message, current_turn)
            if age > max_tool_result_age_turns:
                return MessagePriority.Compactable
            # 文件状态缓存检查
            if is_file_result(message) and has_newer_file_state(message):
                return MessagePriority.High
            return MessagePriority.Low
        return MessagePriority.Medium
```

### 消息选择算法

```python
def get_messages_to_compact(messages, target_tokens, estimator):
    # 1. 计算所有消息优先级
    priorities = build_priority_map(messages)

    # 2. 按优先级排序（低优先级在前）
    sorted_indices = sorted(
        range(len(messages)),
        key=lambda i: (
            priorities[i] == Compactable,
            priorities[i] == Low,
            priorities[i] == Medium,
            i,  # 年龄
        )
    )

    # 3. 保留Critical消息
    kept_indices = set(i for i, p in priorities.items() if p == Critical)

    # 4. 添加High消息直到达到目标
    current_tokens = sum(estimator.estimate_message_tokens(messages[i]) for i in kept_indices)
    for i in reversed(high_indices):  # 优先保留最近的
        if current_tokens >= target_tokens:
            break
        kept_indices.add(i)
        current_tokens += estimator.estimate_message_tokens(messages[i])

    # 5. 分割结果
    messages_to_keep = [messages[i] for i in sorted(kept_indices)]
    messages_to_compact = [messages[i] for i not in kept_indices]

    return messages_to_compact, messages_to_keep
```

---

## 最佳实践

### 1. 配置建议

```python
# 生产环境推荐配置
ContextBudgetConfig(
    context_window_default=200_000,
    auto_compact_buffer_tokens=13_000,  # 约7%缓冲
    max_consecutive_compact_failures=3,  # 电路断路器
    auto_compact_enabled=True,
)

MicroCompactConfig(
    time_based_gap_threshold_minutes=30.0,  # 30分钟间隔
    keep_recent_count=3,  # 保留最近3个工具结果
)
```

### 2. 监控指标

```python
metrics = context_manager.get_compaction_metrics()
# 返回：
{
    "token_usage": 当前使用,
    "context_window": 模型窗口,
    "percent_used": 使用百分比,
    "warning_level": 警告级别,
    "compact_count": 压缩次数,
    "consecutive_failures": 连续失败次数,
    "turns_since_compact": 上次压缩后轮数,
}
```

### 3. 错误处理

```python
# 电路断路器模式
try:
    result = auto_compact_if_needed(messages)
except Exception as e:
    record_compaction_failure()
    if consecutive_failures >= MAX_FAILURES:
        # 停止自动压缩，等待手动干预
        disable_auto_compact()
```

### 4. 性能优化

- **Token估算**：使用粗略估算而非精确计数，减少计算开销
- **优先级缓存**：缓存文件状态，避免重复计算
- **消息指纹**：使用SHA1指纹检测重复消息

---

## 附录：阈值数值表（200K模型）

| 阈值类型 | Token数 | 百分比 | 说明 |
|----------|---------|--------|------|
| 模型窗口 | 200,000 | 100% | API限制 |
| 有效窗口 | 180,000 | 90% | 减去输出预留 |
| 自动阈值 | 167,000 | 83.5% | 触发自动压缩 |
| 阻断限制 | 177,000 | 88.5% | 用户阻断阈值 |
| 警告阈值 | 147,000 | 73.5% | 显示警告 |
| 错误阈值 | 147,000 | 73.5% | 显示错误 |

---

## 附录：可压缩工具列表

| 工具名称 | 说明 | 压缩策略 |
|----------|------|----------|
| `read_file` | 文件读取 | 清除内容，保留元数据 |
| `write_file` | 文件写入 | 清除内容 |
| `edit_file` | 文件编辑 | 清除内容 |
| `glob` | 文件搜索 | 清除结果 |
| `grep` | 内容搜索 | 清除结果 |
| `web_search` | 网络搜索 | 清除结果 |
| `web_fetch` | 网页获取 | 清除内容 |
| `sandbox_shell` | Shell执行 | 清除输出 |
| `sandbox_command` | 命令执行 | 清除输出 |

---

## 附录：压缩边界标记格式

```xml
<compact_boundary type="auto">
  <timestamp>2026-04-22T10:30:00Z</timestamp>
  <pre_compact_tokens>150000</pre_compact_tokens>
  <messages_summarized>50</messages_summarized>
  <previous_message_id>uuid-xxx</previous_message_id>
</compact_boundary>
```

---

## 附录：会话摘要格式

```xml
<conversation_summary>
[摘要内容]

## 已完成
- 任务1：描述
- 任务2：描述

## 当前状态
- 正在进行：描述
- 待处理：描述

## 关键上下文
- 文件：xxx.py
- 配置：xxx
</conversation_summary>

请继续之前的工作。
```

---

*文档版本：1.0*
*生成日期：2026-04-22*
