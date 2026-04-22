# backend/app/services/context/__init__.py
"""AetherCore 上下文管理模块。

该包统一导出上下文预算、消息规范化、压缩策略、溢出恢复、
完整性校验与运行时装配能力，供运行时和业务服务复用。
"""

from app.services.context.bootstrap import configure_context_runtime
from app.services.context.context_budget import (
    ContextBudget,
    ContextBudgetConfig,
    calculate_token_warning_state,
    get_auto_compact_threshold,
)
from app.services.context.context_manager import (
    CompactBoundaryMarker,
    CompactResult,
    ContextManager,
    ContextWindowState,
)
from app.services.context.context_pipeline import (
    ContextPipeline,
    ContextPipelineConfig,
    context_pipeline,
)
from app.services.context.context_state_store import (
    ContextStateStore,
    context_state_store,
)
from app.services.context.contracts import SessionPersister
from app.services.context.integrity import (
    ContextIntegrityValidator,
    IntegrityReport,
    context_integrity_validator,
)
from app.services.context.message_adapter import (
    ContextMessageAdapter,
    context_message_adapter,
)
from app.services.context.message_compaction import (
    CompactionStrategy,
    MessageCompactor,
    MicroCompactResult,
)
from app.services.context.priority_manager import (
    MessagePriority,
    PriorityConfig,
    PriorityManager,
)
from app.services.context.reactive_compact import (
    ReactiveCompact,
    ReactiveCompactConfig,
    ReactiveCompactOutcome,
    ReactiveCompactResult,
    reactive_compact,
)
from app.services.context.runtime_types import (
    CONTEXT_MESSAGE_SCHEMA_VERSION,
    CompactionStrategyName,
    ContextEventType,
    ContextOverflowError,
    ContextSessionState,
)
from app.services.context.session_memory_compact import (
    SessionMemoryCompact,
    SessionMemoryCompactConfig,
    session_memory_compact,
)
from app.services.context.token_estimation import (
    TokenEstimator,
    estimate_message_tokens,
    estimate_messages_tokens,
    rough_token_count,
)
from app.services.context.truncate import (
    truncate_path_middle,
    truncate_text,
    truncate_to_width,
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
    "SessionPersister",
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
    "configure_context_runtime",
]
