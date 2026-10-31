# backend/app/services/context/context_budget.py
"""
上下文预算管理

实现上下文窗口预算分配和阈值管理。
包含可配置的阈值和警告状态，确保对话不会超出模型限制。
模型上下文窗口信息通过 ModelsRegistry 动态获取。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.services.provider.models import (
    get_models_registry,
    ModelsRegistry,
)


class WarningLevel(Enum):
    Normal = "normal"
    Warning = "warning"
    Error = "error"
    Blocking = "blocking"


@dataclass
class ContextBudgetConfig:
    """上下文窗口预算管理配置。

    输入预算先扣除真实输出预留和服务商安全余量，再按比例划分：
    75% 进入 Agent 可见的警戒区，95% 触发平台强制压缩，65% 是压缩目标。
    """
    max_output_tokens_for_summary: int = 32_000  # 兼容旧配置；实际调用会传入真实值
    provider_safety_tokens: int = 4_000
    warning_ratio: float = 0.75
    hard_limit_ratio: float = 0.95
    compact_target_ratio: float = 0.65
    # 兼容旧配置构造参数；新阈值计算不再使用这些缓冲值。
    auto_compact_buffer_tokens: int = 13_000
    warning_threshold_buffer_tokens: int = 20_000
    error_threshold_buffer_tokens: int = 20_000
    manual_compact_buffer_tokens: int = 3_000
    max_consecutive_compact_failures: int = 3
    auto_compact_enabled: bool = True
    compact_max_output_tokens: int = 32_000


@dataclass
class TokenWarningState:
    """当前Token使用警告状态。"""
    percent_left: int
    is_above_warning_threshold: bool
    is_above_error_threshold: bool
    is_above_auto_compact_threshold: bool
    is_at_blocking_limit: bool
    warning_level: WarningLevel = WarningLevel.Normal


@dataclass
class ContextBudget:
    """管理上下文窗口预算分配和阈值。"""
    
    config: ContextBudgetConfig = field(default_factory=ContextBudgetConfig)
    models_registry: ModelsRegistry | None = None
    
    def _get_registry(self) -> ModelsRegistry:
        """获取模型注册表实例"""
        return self.models_registry or get_models_registry()
    
    def get_context_window_for_model(self, model: str, betas: list[str] | None = None) -> int:
        """
        获取特定模型的上下文窗口大小。
        
        通过 ModelsRegistry 动态获取模型能力，支持：
        1. [1m] 后缀：显式选择1M上下文
        2. betas 包含 context-1m：启用1M上下文
        3. 模型注册表查询：内置模型配置
        4. 默认值：200,000 tokens
        """
        return self._get_registry().get_context_window(model, betas)
    
    def get_effective_context_window(
        self,
        model: str,
        max_output_tokens: int | None = None,
        betas: list[str] | None = None,
    ) -> int:
        """返回模型可安全使用的输入预算。"""
        full_window = self.get_context_window_for_model(model, betas)
        reserved_output = max(1, max_output_tokens or self.config.max_output_tokens_for_summary)
        budget = full_window - reserved_output - self.config.provider_safety_tokens
        if budget <= 0:
            raise ValueError("模型窗口不能容纳输出预留与安全余量，请调整部署限额或输出预算")
        return budget
    
    def get_auto_compact_threshold(
        self,
        model: str,
        max_output_tokens: int | None = None,
        betas: list[str] | None = None,
    ) -> int:
        """计算自动压缩触发阈值（硬上限）。"""
        effective_window = self.get_effective_context_window(model, max_output_tokens, betas)
        return self.get_hard_limit(model, max_output_tokens, betas)
    
    def get_warning_threshold(
        self,
        model: str,
        max_output_tokens: int | None = None,
        betas: list[str] | None = None,
    ) -> int:
        """计算警戒阈值；到达后仅提示 Agent，不自动压缩。"""
        usable_input = self.get_effective_context_window(model, max_output_tokens, betas)
        return int(usable_input * self.config.warning_ratio)
    
    def get_error_threshold(
        self,
        model: str,
        max_output_tokens: int | None = None,
        betas: list[str] | None = None,
    ) -> int:
        """兼容旧 API：错误阈值等价于硬上限。"""
        return self.get_hard_limit(model, max_output_tokens, betas)
    
    def get_blocking_limit(
        self,
        model: str,
        max_output_tokens: int | None = None,
        betas: list[str] | None = None,
    ) -> int:
        """计算阻断限制；达到后必须先强制压缩。"""
        usable_input = self.get_effective_context_window(model, max_output_tokens, betas)
        return int(usable_input * self.config.hard_limit_ratio)

    def get_compact_target(
        self,
        model: str,
        max_output_tokens: int | None = None,
        betas: list[str] | None = None,
    ) -> int:
        """计算压缩后的目标输入预算。"""
        usable_input = self.get_effective_context_window(model, max_output_tokens, betas)
        return int(usable_input * self.config.compact_target_ratio)

    def get_hard_limit(
        self,
        model: str,
        max_output_tokens: int | None = None,
        betas: list[str] | None = None,
    ) -> int:
        """显式暴露硬限制，供管线和引擎共享同一语义。"""
        return self.get_blocking_limit(model, max_output_tokens, betas)
    
    def calculate_token_warning_state(
        self,
        token_usage: int,
        model: str,
        max_output_tokens: int | None = None,
        betas: list[str] | None = None,
    ) -> TokenWarningState:
        """
        从Token使用数据计算上下文窗口使用百分比。
        返回已使用和剩余百分比，如果没有使用数据则返回null值。
        """
        threshold = (
            self.get_auto_compact_threshold(model, max_output_tokens, betas)
            if self.config.auto_compact_enabled
            else self.get_effective_context_window(model, max_output_tokens, betas)
        )
        
        percent_left = max(0, int((threshold - token_usage) / threshold * 100))
        
        warning_threshold = self.get_warning_threshold(model, max_output_tokens, betas)
        error_threshold = self.get_error_threshold(model, max_output_tokens, betas)
        blocking_limit = self.get_blocking_limit(model, max_output_tokens, betas)
        
        is_above_warning = token_usage >= warning_threshold
        is_above_error = token_usage >= error_threshold
        is_above_auto_compact = (
            self.config.auto_compact_enabled
            and token_usage >= self.get_auto_compact_threshold(model, max_output_tokens, betas)
        )
        is_at_blocking = token_usage >= blocking_limit
        
        if is_at_blocking:
            level = WarningLevel.Blocking
        elif is_above_error:
            level = WarningLevel.Error
        elif is_above_warning:
            level = WarningLevel.Warning
        else:
            level = WarningLevel.Normal
        
        return TokenWarningState(
            percent_left=percent_left,
            is_above_warning_threshold=is_above_warning,
            is_above_error_threshold=is_above_error,
            is_above_auto_compact_threshold=is_above_auto_compact,
            is_at_blocking_limit=is_at_blocking,
            warning_level=level,
        )
    
    def calculate_context_percentages(
        self,
        usage: dict[str, int] | None,
        context_window: int,
    ) -> dict[str, int | None]:
        """计算上下文使用百分比。"""
        if usage is None:
            return {"used": None, "remaining": None}
        
        input_tokens = usage.get("input_tokens", 0)
        cache_creation = usage.get("cache_creation_input_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        
        total_input = input_tokens + cache_creation + cache_read
        used_percent = min(100, max(0, int(total_input / context_window * 100)))
        
        return {
            "used": used_percent,
            "remaining": 100 - used_percent,
        }
    
    def should_auto_compact(
        self,
        token_usage: int,
        model: str,
        query_source: str | None = None,
        max_output_tokens: int | None = None,
        betas: list[str] | None = None,
    ) -> bool:
        """
        通过环境变量检查是否禁用1M上下文。
        由C4E管理员用于HIPAA合规禁用1M上下文。
        """
        if not self.config.auto_compact_enabled:
            return False
        
        recursion_sources = {"session_memory", "compact", "marble_origami"}
        if query_source in recursion_sources:
            return False
        
        threshold = self.get_auto_compact_threshold(model, max_output_tokens, betas)
        return token_usage >= threshold


def calculate_token_warning_state(
    token_usage: int,
    model: str,
    config: ContextBudgetConfig | None = None,
) -> TokenWarningState:
    """计算Token警告状态的便捷函数。"""
    budget_config = config or ContextBudgetConfig()
    budget = ContextBudget(config=budget_config)
    return budget.calculate_token_warning_state(token_usage, model)


def get_auto_compact_threshold(model: str, config: ContextBudgetConfig | None = None) -> int:
    """获取自动压缩阈值的便捷函数。"""
    budget_config = config or ContextBudgetConfig()
    budget = ContextBudget(config=budget_config)
    return budget.get_auto_compact_threshold(model)
