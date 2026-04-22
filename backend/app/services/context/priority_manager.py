# backend/app/services/context/priority_manager.py
"""
消息优先级管理

实现智能消息优先级排序用于上下文压缩。
确定当上下文压力需要裁剪时应保留还是摘要哪些消息。

优先级级别：
- Critical：系统消息、当前任务指令
- High：最近用户查询、活动任务的工具结果
- Medium：较旧的对话上下文、之前的摘要
- Low：可微压缩的工具结果、冗长输出
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MessagePriority(Enum):
    Critical = "critical"
    High = "high"
    Medium = "medium"
    Low = "low"
    Compactable = "compactable"


@dataclass
class PriorityConfig:
    """消息优先级计算配置。"""
    critical_roles: set[str] = field(default_factory=lambda: {"system"})
    high_roles: set[str] = field(default_factory=lambda: {"user"})
    compactable_tool_names: set[str] = field(default_factory=lambda: {
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "web_search",
        "web_fetch",
        "sandbox_shell",
        "sandbox_command",
    })
    recent_message_count: int = 5
    max_tool_result_age_turns: int = 10
    skill_invoke_priority_boost: bool = True
    file_state_cache_weight: float = 0.5


@dataclass
class MessageMetadata:
    """附加到消息的优先级计算元数据。"""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    turn_index: int = 0
    is_compact_summary: bool = False
    is_boundary_marker: bool = False
    tool_name: str | None = None
    file_paths: list[str] = field(default_factory=list)
    priority: MessagePriority = MessagePriority.Medium
    token_count: int = 0


class PriorityManager:
    """
    管理上下文压缩决策的消息优先级。
    
    基于：
    - 消息角色和内容类型
    - 相对于当前轮次的年龄
    - 工具结果重要性
    - 文件状态追踪
    - 压缩边界标记
    """
    
    def __init__(self, config: PriorityConfig | None = None):
        self.config = config or PriorityConfig()
        self._file_state_cache: dict[str, tuple[datetime, int]] = {}
        self._turn_counter: int = 0
    
    def advance_turn(self) -> int:
        """推进轮次计数器用于年龄优先级计算。"""
        self._turn_counter += 1
        return self._turn_counter
    
    def calculate_priority(
        self,
        message: dict[str, Any],
        current_turn: int | None = None,
        file_state_cache: dict[str, tuple[datetime, int]] | None = None,
    ) -> MessagePriority:
        """计算单个消息的优先级级别。"""
        role = message.get("role", "")
        
        if role in self.config.critical_roles:
            return MessagePriority.Critical
        
        if self._is_compact_boundary(message):
            return MessagePriority.Critical
        
        if self._is_compact_summary(message):
            return MessagePriority.High
        
        if role in self.config.high_roles:
            content = message.get("content", "")
            
            if self._contains_skill_invoke(content):
                return MessagePriority.High
            
            age = self._calculate_message_age(message, current_turn)
            if age <= self.config.recent_message_count:
                return MessagePriority.High
            
            return MessagePriority.Medium
        
        if role == "assistant":
            return self._calculate_assistant_priority(message, current_turn)
        
        if role == "tool":
            return self._calculate_tool_result_priority(message, current_turn, file_state_cache)
        
        return MessagePriority.Medium
    
    def _is_compact_boundary(self, message: dict[str, Any]) -> bool:
        """检查消息是否为压缩边界标记。"""
        content = message.get("content", "")
        if isinstance(content, str):
            return (
                "compact_boundary" in content.lower()
                or message.get("is_boundary_marker", False)
            )
        return message.get("is_boundary_marker", False)
    
    def _is_compact_summary(self, message: dict[str, Any]) -> bool:
        """检查消息是否为压缩摘要。"""
        content = message.get("content", "")
        if isinstance(content, str):
            return (
                "conversation_summary" in content.lower()
                or message.get("is_compact_summary", False)
            )
        return message.get("is_compact_summary", False)
    
    def _contains_skill_invoke(self, content: str | list[Any]) -> bool:
        """检查内容是否包含技能调用标记。"""
        if isinstance(content, str):
            return "aether_skill" in content or "invoke_skill" in content
        return False
    
    def _calculate_message_age(self, message: dict[str, Any], current_turn: int | None = None) -> int:
        """计算消息的年龄（以轮次计）。"""
        turn_index = message.get("turn_index", 0)
        current = current_turn or self._turn_counter
        return current - turn_index
    
    def _calculate_assistant_priority(
        self,
        message: dict[str, Any],
        current_turn: int | None = None,
    ) -> MessagePriority:
        """计算助手消息的优先级。"""
        tool_calls = message.get("tool_calls", [])
        if tool_calls:
            return MessagePriority.High
        
        age = self._calculate_message_age(message, current_turn)
        if age <= self.config.recent_message_count:
            return MessagePriority.High
        
        return MessagePriority.Medium
    
    def _calculate_tool_result_priority(
        self,
        message: dict[str, Any],
        current_turn: int | None = None,
        file_state_cache: dict[str, tuple[datetime, int]] | None = None,
    ) -> MessagePriority:
        """计算工具结果消息的优先级。"""
        tool_name = self._extract_tool_name(message)
        
        if tool_name and tool_name in self.config.compactable_tool_names:
            age = self._calculate_message_age(message, current_turn)
            if age > self.config.max_tool_result_age_turns:
                return MessagePriority.Compactable
            
            if file_state_cache and self._is_file_result(message, tool_name):
                file_paths = self._extract_file_paths(message)
                for path in file_paths:
                    if path in file_state_cache:
                        cache_time, _ = file_state_cache[path]
                        msg_time = self._get_message_timestamp(message)
                        if msg_time and msg_time > cache_time:
                            return MessagePriority.High
            
            return MessagePriority.Low
        
        return MessagePriority.Medium
    
    def _extract_tool_name(self, message: dict[str, Any]) -> str | None:
        """从工具结果消息中提取工具名称。"""
        tool_call_id = message.get("tool_call_id", "")
        if tool_call_id:
            return tool_call_id.split("_")[0] if "_" in tool_call_id else None
        
        content = message.get("content", "")
        if isinstance(content, str):
            match = re.search(r"tool_name[=:]\s*['\"]?(\w+)['\"]?", content)
            if match:
                return match.group(1)
        
        return None
    
    def _is_file_result(self, message: dict[str, Any], tool_name: str) -> bool:
        """检查工具结果是否包含文件内容。"""
        file_tools = {"read_file", "write_file", "edit_file", "glob", "grep"}
        return tool_name in file_tools
    
    def _extract_file_paths(self, message: dict[str, Any]) -> list[str]:
        """从工具结果内容中提取文件路径。"""
        content = message.get("content", "")
        if isinstance(content, str):
            pattern = r'(?:file_path|path)[=:]\s*["\']([^"\']+)["\']'
            matches = re.findall(pattern, content)
            return matches
        return []
    
    def _get_message_timestamp(self, message: dict[str, Any]) -> datetime | None:
        """从消息元数据获取时间戳。"""
        timestamp_str = message.get("timestamp")
        if timestamp_str:
            try:
                return datetime.fromisoformat(timestamp_str)
            except ValueError:
                pass
        return None
    
    def build_priority_map(
        self,
        messages: list[dict[str, Any]],
        current_turn: int | None = None,
    ) -> dict[int, MessagePriority]:
        """
        为所有消息构建优先级映射。
        
        返回消息索引到优先级级别的字典映射。
        """
        priorities: dict[int, MessagePriority] = {}
        for i, msg in enumerate(messages):
            priorities[i] = self.calculate_priority(msg, current_turn, self._file_state_cache)
        return priorities
    
    def get_messages_to_compact(
        self,
        messages: list[dict[str, Any]],
        target_tokens: int,
        estimator: Any,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        确定应压缩哪些消息与保留哪些。
        
        返回 (要压缩的消息, 要保留的消息)。
        """
        priorities = self.build_priority_map(messages)
        
        sorted_indices = sorted(
            range(len(messages)),
            key=lambda i: (
                priorities[i] == MessagePriority.Compactable,
                priorities[i] == MessagePriority.Low,
                priorities[i] == MessagePriority.Medium,
                i,
            ),
        )
        
        kept_indices: set[int] = set()
        current_tokens = 0
        
        critical_indices = [i for i, p in priorities.items() if p == MessagePriority.Critical]
        for i in critical_indices:
            kept_indices.add(i)
            current_tokens += estimator.estimate_message_tokens(messages[i])
        
        high_indices = [i for i, p in priorities.items() if p == MessagePriority.High]
        high_indices.reverse()
        for i in high_indices:
            if current_tokens >= target_tokens:
                break
            kept_indices.add(i)
            current_tokens += estimator.estimate_message_tokens(messages[i])
        
        kept_indices = set(sorted(kept_indices))
        compact_indices = set(range(len(messages))) - kept_indices
        
        messages_to_keep = [messages[i] for i in sorted(kept_indices)]
        messages_to_compact = [messages[i] for i in sorted(compact_indices)]
        
        return messages_to_compact, messages_to_keep
    
    def update_file_state_cache(self, file_path: str, token_count: int) -> None:
        """在文件读取操作后更新文件状态缓存。"""
        self._file_state_cache[file_path] = (datetime.now(timezone.utc), token_count)
    
    def clear_cache(self) -> None:
        """清除文件状态缓存（通常在压缩后）。"""
        self._file_state_cache.clear()
    
    def fingerprint_messages(self, messages: list[dict[str, Any]]) -> str:
        """生成消息指纹用于检测重复。"""
        payload = [
            {
                "role": msg.get("role"),
                "content": str(msg.get("content", ""))[:100],
            }
            for msg in messages
        ]
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(serialized.encode("utf-8")).hexdigest()[:16]