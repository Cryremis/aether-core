# backend/app/services/context/context_manager.py
"""
上下文管理服务

AetherCore上下文窗口管理的主协调器。
协调Token估算、预算管理、优先级处理和压缩策略，
以防止Token溢出。

此模块实现核心上下文管理循环：
1. 监控Token使用相对于预算阈值
2. 计算警告状态用于用户通知
3. 达到阈值时触发自动压缩
4. 执行压缩并重建压缩后上下文
5. 追踪压缩历史和电路断路器
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services.context.token_estimation import TokenEstimator, estimate_messages_tokens
from app.services.context.context_budget import (
    ContextBudget,
    ContextBudgetConfig,
    TokenWarningState,
    WarningLevel,
)
from app.services.context.priority_manager import PriorityManager, MessagePriority
from app.services.context.message_compaction import (
    MessageCompactor,
    CompactResult,
    CompactBoundaryMarker,
    MicroCompactResult,
    CompactionStrategy,
)


@dataclass
class ContextWindowState:
    """上下文窗口当前状态。"""
    token_usage: int = 0
    context_window: int = 200_000
    effective_window: int = 180_000
    auto_compact_threshold: int = 167_000
    warning_threshold: int = 147_000
    error_threshold: int = 147_000
    blocking_limit: int = 177_000
    percent_used: float = 0.0
    percent_remaining: float = 100.0
    warning_state: TokenWarningState | None = None
    last_compaction_time: float = 0
    consecutive_compact_failures: int = 0
    compact_count: int = 0
    last_usage_from_api: dict[str, int] = field(default_factory=dict)


@dataclass
class AutoCompactTrackingState:
    """自动压缩操作的追踪状态。"""
    compacted: bool = False
    turn_counter: int = 0
    turn_id: str = ""
    consecutive_failures: int = 0
    last_compact_result: CompactResult | None = None


@dataclass
class ContextManagerConfig:
    """上下文管理器行为配置。"""
    auto_compact_enabled: bool = True
    max_consecutive_failures: int = 3
    compact_cooldown_turns: int = 2
    post_compact_file_restore_count: int = 5
    post_compact_token_budget: int = 50_000
    post_compact_max_tokens_per_file: int = 5_000
    post_compact_skills_token_budget: int = 25_000
    post_compact_max_tokens_per_skill: int = 5_000
    session_memory_compact_enabled: bool = True
    max_turns_since_last_compact: int = 50


class ContextManager:
    """
    上下文窗口管理主协调器。
    
    职责：
    - 监控和报告上下文使用
    - 计算阈值和警告
    - 触发和执行压缩
    - 追踪压缩历史
    - 与优先级和压缩服务协调
    """
    
    def __init__(
        self,
        config: ContextManagerConfig | None = None,
        budget_config: ContextBudgetConfig | None = None,
        estimator: TokenEstimator | None = None,
        priority_manager: PriorityManager | None = None,
        compactor: MessageCompactor | None = None,
    ):
        self.config = config or ContextManagerConfig()
        self.budget = ContextBudget(config=budget_config or ContextBudgetConfig())
        self.estimator = estimator or TokenEstimator()
        self.priority_manager = priority_manager or PriorityManager()
        self.compactor = compactor or MessageCompactor(estimator=self.estimator)
        
        self._state: ContextWindowState = ContextWindowState()
        self._tracking: AutoCompactTrackingState = AutoCompactTrackingState(
            turn_id=f"turn_{uuid.uuid4().hex}",
        )
        self._model: str = ""
    
    def set_model(self, model: str) -> None:
        """为上下文窗口计算设置当前模型。"""
        self._model = model
        self._update_thresholds()
    
    def _update_thresholds(self) -> None:
        """基于当前模型更新阈值。"""
        if not self._model:
            return
        
        self._state.context_window = self.budget.get_context_window_for_model(self._model)
        self._state.effective_window = self.budget.get_effective_context_window(self._model)
        self._state.auto_compact_threshold = self.budget.get_auto_compact_threshold(self._model)
        self._state.warning_threshold = self.budget.get_warning_threshold(self._model)
        self._state.error_threshold = self.budget.get_error_threshold(self._model)
        self._state.blocking_limit = self.budget.get_blocking_limit(self._model)
    
    def update_token_usage(self, messages: list[dict[str, Any]]) -> int:
        """从当前消息更新Token使用估算。"""
        token_count = self.estimator.estimate_messages_tokens(messages)
        self._state.token_usage = token_count
        
        if self._state.effective_window > 0:
            self._state.percent_used = token_count / self._state.effective_window * 100
            self._state.percent_remaining = max(0, 100 - self._state.percent_used)
        
        self._state.warning_state = self.budget.calculate_token_warning_state(
            token_count, self._model
        )
        
        return token_count
    
    def update_from_api_usage(self, usage: dict[str, int]) -> None:
        """从API响应使用数据更新上下文状态。"""
        self._state.last_usage_from_api = usage.copy()
        
        input_tokens = usage.get("input_tokens", 0)
        cache_creation = usage.get("cache_creation_input_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        output_tokens = usage.get("output_tokens", 0)
        
        total = input_tokens + cache_creation + cache_read + output_tokens
        self._state.token_usage = total
        
        self._state.warning_state = self.budget.calculate_token_warning_state(
            total, self._model
        )
    
    def get_context_state(self) -> ContextWindowState:
        """获取当前上下文窗口状态。"""
        return self._state
    
    def get_warning_state(self) -> TokenWarningState | None:
        """获取当前Token警告状态。"""
        return self._state.warning_state
    
    def should_auto_compact(
        self,
        messages: list[dict[str, Any]],
        query_source: str | None = None,
    ) -> bool:
        """
        判断是否应触发自动压缩。
        
        检查：
        - 自动压缩已启用
        - 阈值已达到
        - 不在递归源中
        - 电路断路器未触发
        - 冷却期已过
        """
        if not self.config.auto_compact_enabled:
            return False
        
        recursion_sources = {"session_memory", "compact", "marble_origami"}
        if query_source in recursion_sources:
            return False
        
        if self._tracking.consecutive_failures >= self.config.max_consecutive_failures:
            return False
        
        if self._tracking.compacted and self._tracking.turn_counter < self.config.compact_cooldown_turns:
            return False
        
        token_count = self.update_token_usage(messages)
        
        return self.budget.should_auto_compact(token_count, self._model, query_source)
    
    def advance_turn(self) -> None:
        """推进轮次计数器用于追踪。"""
        self._tracking.turn_counter += 1
        self._tracking.turn_id = f"turn_{uuid.uuid4().hex}"
    
    def auto_compact_if_needed(
        self,
        messages: list[dict[str, Any]],
        query_source: str | None = None,
    ) -> tuple[bool, CompactResult | MicroCompactResult | None]:
        """
        如需要则执行自动压缩。
        
        返回 (是否已压缩, 结果)。
        """
        should_compact = self.should_auto_compact(messages, query_source)
        
        if not should_compact:
            return False, None
        
        micro_result = self.compactor.micro_compact_messages(messages, query_source)
        
        if micro_result.tokens_saved > 0:
            self.compactor.suppress_compact_warning()
            self._state.last_compaction_time = time.time()
            self._state.compact_count += 1
            self._tracking.compacted = True
            self._tracking.turn_counter = 0
            self._tracking.consecutive_failures = 0
            return True, micro_result
        
        return False, None
    
    def execute_manual_compact(
        self,
        messages: list[dict[str, Any]],
        custom_instructions: str | None = None,
    ) -> tuple[bool, MicroCompactResult | None]:
        """
        执行手动压缩（由用户命令触发）。
        
        首先尝试微压缩，然后估算节省量。
        """
        micro_result = self.compactor.micro_compact_messages(messages)
        
        if micro_result.tokens_saved > 0:
            self.compactor.suppress_compact_warning()
            self._state.last_compaction_time = time.time()
            self._state.compact_count += 1
            return True, micro_result
        
        savings_estimate = self.compactor.estimate_compaction_savings(
            messages,
            int(self._state.effective_window * 0.5),
        )
        
        return False, None
    
    def get_messages_after_compact_boundary(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """获取最后一个压缩边界之后的消息。"""
        boundary_index = -1
        for i, msg in enumerate(messages):
            if msg.get("is_boundary_marker"):
                boundary_index = i
        
        if boundary_index < 0:
            return messages
        
        return messages[boundary_index + 1:]
    
    def get_last_compact_boundary_info(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        """获取最后一个压缩边界的信息。"""
        for msg in reversed(messages):
            if msg.get("is_boundary_marker"):
                return {
                    "marker_id": msg.get("marker_id"),
                    "timestamp": msg.get("timestamp"),
                    "content": msg.get("content"),
                }
        return None
    
    def build_context_summary(self) -> str:
        """构建当前上下文状态的可读摘要。"""
        lines = [
            f"上下文窗口: {self._state.context_window:,} tokens",
            f"有效窗口: {self._state.effective_window:,} tokens",
            f"当前使用: {self._state.token_usage:,} tokens ({self._state.percent_used:.1f}%)",
            f"剩余: {self._state.percent_remaining:.1f}%",
            f"自动压缩阈值: {self._state.auto_compact_threshold:,} tokens",
        ]
        
        if self._state.warning_state:
            level = self._state.warning_state.warning_level.value
            lines.append(f"警告级别: {level}")
        
        if self._state.compact_count > 0:
            lines.append(f"压缩次数: {self._state.compact_count}")
        
        return "\n".join(lines)
    
    def get_compaction_metrics(self) -> dict[str, Any]:
        """获取监控和遥测的指标。"""
        return {
            "token_usage": self._state.token_usage,
            "context_window": self._state.context_window,
            "effective_window": self._state.effective_window,
            "percent_used": self._state.percent_used,
            "auto_compact_threshold": self._state.auto_compact_threshold,
            "warning_level": self._state.warning_state.warning_level.value if self._state.warning_state else "normal",
            "compact_count": self._state.compact_count,
            "consecutive_failures": self._tracking.consecutive_failures,
            "turns_since_compact": self._tracking.turn_counter,
        }
    
    def reset_state(self) -> None:
        """为新对话重置上下文状态。"""
        self._state = ContextWindowState()
        self._tracking = AutoCompactTrackingState(
            turn_id=f"turn_{uuid.uuid4().hex}",
        )
        self.compactor.clear_compact_warning_suppression()
        self.priority_manager.clear_cache()
    
    def record_compaction_failure(self) -> None:
        """为电路断路器记录压缩失败。"""
        self._tracking.consecutive_failures += 1
    
    def record_compaction_success(self) -> None:
        """记录成功压缩，重置失败计数器。"""
        self._tracking.consecutive_failures = 0
        self._tracking.compacted = True
        self._tracking.turn_counter = 0
        self._state.last_compaction_time = time.time()
        self._state.compact_count += 1


context_manager = ContextManager()