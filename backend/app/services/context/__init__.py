# backend/app/services/context/__init__.py
"""
AetherCore上下文管理模块

此模块实现完整的上下文窗口管理，防止长运行Agent对话中的Token溢出。
核心策略包括：

- Token估算和计数
- 上下文预算分配
- 自动压缩和手动压缩触发
- 消息优先级和截断
- 压缩边界追踪
"""

from app.services.context.token_estimation import (
    TokenEstimator,
    rough_token_count,
    estimate_message_tokens,
    estimate_messages_tokens,
)
from app.services.context.context_budget import (
    ContextBudget,
    ContextBudgetConfig,
    calculate_token_warning_state,
    get_auto_compact_threshold,
)
from app.services.context.context_manager import (
    ContextManager,
    ContextWindowState,
    CompactResult,
    CompactBoundaryMarker,
)
from app.services.context.message_compaction import (
    MessageCompactor,
    CompactionStrategy,
    MicroCompactResult,
)
from app.services.context.session_memory_compact import (
    SessionMemoryCompact,
    SessionMemoryCompactConfig,
    session_memory_compact,
)
from app.services.context.reactive_compact import (
    ReactiveCompact,
    ReactiveCompactConfig,
    ReactiveCompactOutcome,
    ReactiveCompactResult,
    reactive_compact,
)
from app.services.context.priority_manager import (
    PriorityManager,
    MessagePriority,
    PriorityConfig,
)
from app.services.context.truncate import (
    truncate_text,
    truncate_path_middle,
    truncate_to_width,
)
from app.services.context.runtime_types import (
    CONTEXT_MESSAGE_SCHEMA_VERSION,
    ContextEventType,
    CompactionStrategyName,
    ContextSessionState,
    ContextOverflowError,
)
from app.services.context.message_adapter import (
    ContextMessageAdapter,
    context_message_adapter,
)
from app.services.context.context_state_store import (
    ContextStateStore,
    context_state_store,
)
from app.services.context.integrity import (
    ContextIntegrityValidator,
    IntegrityReport,
    context_integrity_validator,
)
from app.services.context.context_pipeline import (
    ContextPipeline,
    ContextPipelineConfig,
    context_pipeline,
)

__all__ = [
    "TokenEstimator",
    "rough_token_count",
    "estimate_message_tokens",
    "estimate_messages_tokens",
    "ContextBudget",
    "ContextBudgetConfig",
    "calculate_token_warning_state",
    "get_auto_compact_threshold",
    "ContextManager",
    "ContextWindowState",
    "CompactResult",
    "CompactBoundaryMarker",
    "MessageCompactor",
    "CompactionStrategy",
    "MicroCompactResult",
    "SessionMemoryCompact",
    "SessionMemoryCompactConfig",
    "session_memory_compact",
    "ReactiveCompact",
    "ReactiveCompactConfig",
    "ReactiveCompactOutcome",
    "ReactiveCompactResult",
    "reactive_compact",
    "PriorityManager",
    "MessagePriority",
    "PriorityConfig",
    "truncate_text",
    "truncate_path_middle",
    "truncate_to_width",
    "CONTEXT_MESSAGE_SCHEMA_VERSION",
    "ContextEventType",
    "CompactionStrategyName",
    "ContextSessionState",
    "ContextOverflowError",
    "ContextMessageAdapter",
    "context_message_adapter",
    "ContextStateStore",
    "context_state_store",
    "ContextIntegrityValidator",
    "IntegrityReport",
    "context_integrity_validator",
    "ContextPipeline",
    "ContextPipelineConfig",
    "context_pipeline",
]
