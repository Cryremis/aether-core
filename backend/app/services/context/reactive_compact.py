# backend/app/services/context/reactive_compact.py
"""
响应式压缩服务

在API返回prompt-too-long错误时触发响应式压缩，
动态调整压缩策略以确保请求能够成功。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.services.context.token_estimation import TokenEstimator
from app.services.context.context_budget import ContextBudget, ContextBudgetConfig
from app.services.context.message_compaction import (
    MessageCompactor,
    CompactResult,
    CompactBoundaryMarker,
    MicroCompactResult,
)
from app.services.context.session_memory_compact import SessionMemoryCompact
from app.services.context.priority_manager import PriorityManager


class ReactiveCompactOutcome(Enum):
    """响应式压缩结果"""
    Success = "success"  # ok
    TooFewGroups = "too_few_groups"  # 组数太少
    Aborted = "aborted"  # 用户中断
    Exhausted = "exhausted"  # 已耗尽所有选项
    Error = "error"  # 错误
    MediaUnstrippable = "media_unstrippable"  # 媒体无法剥离


@dataclass
class ReactiveCompactConfig:
    """响应式压缩配置"""
    max_peel_attempts: int = 5
    min_groups_to_compact: int = 2
    strip_images_enabled: bool = True
    strip_documents_enabled: bool = True
    fallback_to_full_compact: bool = True


@dataclass
class MessageGroup:
    """
    消息分组（对应一个API轮次）。
    
    将消息按API调用轮次分组，
    每组包含一个助手响应及其对应的工具结果。
    """
    messages: list[dict[str, Any]] = field(default_factory=list)
    token_count: int = 0
    has_assistant_response: bool = False
    has_tool_results: bool = False
    start_index: int = 0
    end_index: int = 0


@dataclass
class ReactiveCompactResult:
    """响应式压缩结果"""
    outcome: ReactiveCompactOutcome
    result: CompactResult | MicroCompactResult | None = None
    messages: list[dict[str, Any]] | None = None
    peeled_groups: int = 0
    tokens_saved: int = 0
    user_display_message: str | None = None


class ReactiveCompact:
    """
    响应式压缩服务。
    
    在API返回prompt-too-long错误时动态触发压缩，
    确保请求能够在限制内成功完成。
    """
    
    def __init__(
        self,
        config: ReactiveCompactConfig | None = None,
        budget_config: ContextBudgetConfig | None = None,
        estimator: TokenEstimator | None = None,
        compactor: MessageCompactor | None = None,
        session_memory: SessionMemoryCompact | None = None,
    ):
        self.config = config or ReactiveCompactConfig()
        self.budget = ContextBudget(config=budget_config or ContextBudgetConfig())
        self.estimator = estimator or TokenEstimator()
        self.compactor = compactor or MessageCompactor(estimator=self.estimator)
        self.session_memory = session_memory or SessionMemoryCompact(estimator=self.estimator)
        
        self._is_reactive_only_mode: bool = False
    
    def set_reactive_only_mode(self, enabled: bool) -> None:
        """
        设置响应式专用模式。
        
        启用后，主动自动压缩被抑制，响应式压缩在API返回
        prompt-too-long时触发。
        """
        self._is_reactive_only_mode = enabled
    
    def is_reactive_only_mode(self) -> bool:
        """检查是否为响应式专用模式"""
        return self._is_reactive_only_mode
    
    def group_messages_by_api_round(self, messages: list[dict[str, Any]]) -> list[MessageGroup]:
        """
        按API调用轮次分组消息。
        """
        if not messages:
            return []
        
        groups: list[MessageGroup] = []
        current_group = MessageGroup(start_index=0)
        
        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            
            if role == "system":
                current_group.messages.append(msg)
                current_group.end_index = i
                continue
            
            if role == "assistant":
                if current_group.has_assistant_response:
                    current_group.token_count = self.estimator.estimate_messages_tokens(current_group.messages)
                    groups.append(current_group)
                    current_group = MessageGroup(start_index=i)
                
                current_group.messages.append(msg)
                current_group.has_assistant_response = True
                current_group.end_index = i
                continue
            
            if role in ("user", "tool"):
                current_group.messages.append(msg)
                if role == "tool":
                    current_group.has_tool_results = True
                current_group.end_index = i
                continue
        
        if current_group.messages:
            current_group.token_count = self.estimator.estimate_messages_tokens(current_group.messages)
            groups.append(current_group)
        
        return groups
    
    def calculate_token_gap_from_error(self, error_message: str) -> int | None:
        """
        从prompt-too-long错误消息中提取Token差距。
        """
        import re
        pattern = r"prompt is too long: (\d+) tokens > (\d+) maximum"
        match = re.search(pattern, error_message)
        if match:
            current_tokens = int(match.group(1))
            max_tokens = int(match.group(2))
            return current_tokens - max_tokens
        return None
    
    def peel_groups_from_tail(
        self,
        groups: list[MessageGroup],
        token_gap: int | None,
    ) -> tuple[list[MessageGroup], int]:
        """
        从尾部剥离消息组直到满足Token限制。
        
        返回（剩余组，剥离的Token数）。
        """
        if len(groups) < self.config.min_groups_to_compact:
            return groups, 0
        
        peeled_tokens = 0
        remaining_groups = list(groups)
        
        if token_gap is None:
            peel_count = max(1, int(len(groups) * 0.2))
            peel_count = min(peel_count, len(groups) - self.config.min_groups_to_compact)
            
            for _ in range(peel_count):
                if remaining_groups:
                    peeled_group = remaining_groups.pop(0)
                    peeled_tokens += peeled_group.token_count
            
            return remaining_groups, peeled_tokens
        
        target_tokens_to_peel = token_gap
        
        while remaining_groups and peeled_tokens < target_tokens_to_peel:
            if len(remaining_groups) <= self.config.min_groups_to_compact:
                break
            
            peeled_group = remaining_groups.pop(0)
            peeled_tokens += peeled_group.token_count
        
        return remaining_groups, peeled_tokens
    
    def strip_media_from_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        从消息中剥离媒体内容以减少Token使用。
        
        当其他压缩方法无法满足要求时的最后手段。
        """
        from app.services.context.truncate import strip_images_from_messages
        return strip_images_from_messages(messages)
    
    def build_reactive_compact_prompt(self, custom_instructions: str | None = None) -> str:
        """构建响应式压缩提示词"""
        base_prompt = """请生成一个简洁的对话摘要，保留以下关键信息：
1. 用户的明确目标和需求
2. 已完成的关键任务和决策
3. 当前进行中的工作状态
4. 重要的上下文信息（文件、配置等）

摘要应该足够简洁以节省Token，同时保留继续工作所需的关键信息。"""
        
        if custom_instructions:
            return f"{base_prompt}\n\n用户补充指令：{custom_instructions}"
        
        return base_prompt
    
    def reactive_compact_on_prompt_too_long(
        self,
        messages: list[dict[str, Any]],
        error_message: str,
        custom_instructions: str | None = None,
        trigger: str = "auto",
    ) -> ReactiveCompactResult:
        """
        在prompt-too-long错误时执行响应式压缩。
        """
        groups = self.group_messages_by_api_round(messages)
        
        if len(groups) < self.config.min_groups_to_compact:
            return ReactiveCompactResult(
                outcome=ReactiveCompactOutcome.TooFewGroups,
                peeled_groups=0,
            )
        
        token_gap = self.calculate_token_gap_from_error(error_message)
        
        remaining_groups, peeled_tokens = self.peel_groups_from_tail(groups, token_gap)
        
        if peeled_tokens == 0:
            return ReactiveCompactResult(
                outcome=ReactiveCompactOutcome.Exhausted,
                peeled_groups=0,
            )
        
        compacted_messages: list[dict[str, Any]] = []
        for group in remaining_groups:
            compacted_messages.extend(group.messages)
        
        if self.config.strip_images_enabled:
            compacted_messages = self.strip_media_from_messages(compacted_messages)
        
        if compacted_messages and compacted_messages[0].get("role") == "assistant":
            synthetic_user = {
                "role": "user",
                "content": "[之前对话已为响应式压缩而截断]",
                "is_meta": True,
            }
            compacted_messages.insert(0, synthetic_user)
        
        post_compact_tokens = self.estimator.estimate_messages_tokens(compacted_messages)
        
        boundary_marker = CompactBoundaryMarker(
            compaction_type="reactive",
            pre_compact_token_count=self.estimator.estimate_messages_tokens(messages),
            messages_summarized=len(groups) - len(remaining_groups),
        )
        
        summary_content = self.build_reactive_compact_prompt(custom_instructions)
        summary_message = {
            "role": "user",
            "content": f"<conversation_summary>\n因上下文限制，已自动压缩 {len(groups) - len(remaining_groups)} 个消息组。\n</conversation_summary>\n{summary_content}",
            "is_compact_summary": True,
        }
        
        result_messages = [
            boundary_marker.to_message(),
            summary_message,
            *compacted_messages,
        ]
        
        return ReactiveCompactResult(
            outcome=ReactiveCompactOutcome.Success,
            messages=result_messages,
            peeled_groups=len(groups) - len(remaining_groups),
            tokens_saved=peeled_tokens,
            user_display_message=f"已响应式压缩 {len(groups) - len(remaining_groups)} 个消息组，节省约 {peeled_tokens} Token",
        )
    
    def is_prompt_too_long_error(self, error_message: str) -> bool:
        """检查是否为prompt-too-long错误"""
        patterns = [
            "prompt is too long",
            "prompt_too_long",
            "context length exceeded",
            "maximum context length",
        ]
        return any(pattern in error_message.lower() for pattern in patterns)
    
    def estimate_prompt_tokens_for_request(self, messages: list[dict[str, Any]]) -> int:
        """估算请求的prompt Token数"""
        return self.estimator.estimate_messages_tokens(messages)


reactive_compact = ReactiveCompact()