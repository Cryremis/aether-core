# backend/app/services/context/session_memory_compact.py
"""
会话记忆压缩服务

EXPERIMENT: 会话记忆压缩
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services.context.token_estimation import TokenEstimator
from app.services.context.message_compaction import (
    CompactResult,
    CompactBoundaryMarker,
    MessageCompactor,
)
from app.services.context.priority_manager import PriorityManager


@dataclass
class SessionMemoryCompactConfig:
    """
    会话记忆压缩阈值配置
    """
    min_tokens: int = 10_000  # 压缩后保留的最小Token数
    min_text_block_messages: int = 5  # 保留的含文本块消息最小数量
    max_tokens: int = 40_000  # 压缩后保留的最大Token数（硬上限）


DEFAULT_SM_COMPACT_CONFIG: SessionMemoryCompactConfig = SessionMemoryCompactConfig()


@dataclass
class SessionMemoryState:
    """会话记忆状态追踪。"""
    last_summarized_message_id: str | None = None
    extraction_in_progress: bool = False
    extraction_completed_time: float | None = None
    session_memory_content: str | None = None
    initialized: bool = False


class SessionMemoryCompact:
    """
    EXPERIMENT: 使用会话记忆替代传统压缩
    """
    
    def __init__(
        self,
        config: SessionMemoryCompactConfig | None = None,
        estimator: TokenEstimator | None = None,
    ):
        self.config = config or DEFAULT_SM_COMPACT_CONFIG
        self.estimator = estimator or TokenEstimator()
        self._state: SessionMemoryState = SessionMemoryState()
    
    def should_use_session_memory_compact(self) -> bool:
        """检查是否应使用会话记忆进行压缩"""
        if not self._state.initialized:
            return False
        if not self._state.session_memory_content:
            return False
        if self._state.extraction_in_progress:
            return False
        return True
    
    def set_session_memory_content(self, content: str) -> None:
        """设置会话记忆内容"""
        self._state.session_memory_content = content
        self._state.initialized = True
    
    def set_last_summarized_message_id(self, message_id: str | None) -> None:
        """设置上次压缩后的最后消息ID（由sessionMemory.ts调用）"""
        self._state.last_summarized_message_id = message_id
    
    def mark_extraction_started(self) -> None:
        """标记提取开始（由sessionMemory.ts调用）"""
        self._state.extraction_in_progress = True
    
    def mark_extraction_completed(self) -> None:
        """标记提取完成（由sessionMemory.ts调用）"""
        self._state.extraction_in_progress = False
        self._state.extraction_completed_time = time.time()
    
    def has_text_blocks(self, message: dict[str, Any]) -> bool:
        """检查消息是否包含文本块（用于用户/助手交互的文本内容）"""
        role = message.get("role", "")
        content = message.get("content")
        
        if role == "assistant":
            if isinstance(content, list):
                return any(block.get("type") == "text" for block in content)
            return bool(content)
        
        if role == "user":
            if isinstance(content, str):
                return len(content) > 0
            if isinstance(content, list):
                return any(block.get("type") == "text" for block in content)
        
        return False
    
    def get_tool_result_ids(self, message: dict[str, Any]) -> list[str]:
        """检查消息是否包含tool_result块并返回其tool_use_ids"""
        if message.get("role") != "user":
            return []
        
        content = message.get("content")
        if not isinstance(content, list):
            return []
        
        ids: list[str] = []
        for block in content:
            if block.get("type") == "tool_result":
                ids.append(block.get("tool_use_id", ""))
        return ids
    
    def has_tool_use_with_ids(self, message: dict[str, Any], tool_use_ids: set[str]) -> bool:
        """检查消息是否包含指定ID的tool_use块"""
        if message.get("role") != "assistant":
            return False
        
        content = message.get("content")
        if not isinstance(content, list):
            return False
        
        return any(
            block.get("type") == "tool_use" and block.get("id") in tool_use_ids
            for block in content
        )
    
    def adjust_index_to_preserve_api_invariants(
        self,
        messages: list[dict[str, Any]],
        start_index: int,
    ) -> int:
        """
        调整起始索引以确保我们不会分割tool_use/tool_result配对
        或与保留助手消息共享同一message.id的thinking块。
        
        如果我们保留的ANY消息包含tool_result块，我们需要
        包含之前的包含匹配tool_use块的助手消息。
        
        此外，如果保留范围内的ANY助手消息与之前的助手消息
        有相同的message.id（可能包含thinking块），我们需要
        包含这些消息以便normalizeMessagesForAPI正确合并。
        
        这处理流式传输产生每个内容块单独消息
        （thinking、tool_use等）共享同一message.id但不同uuid的情况。
        如果startIndex落在这些流式消息之一，我们需要查看
        ALL保留消息的tool_results，而不仅是第一个。
        
        此修复解决的示例bug场景：
        
        工具配对场景：
          会话存储（压缩前）：
            Index N:   assistant, message.id: X, content: [thinking]
            Index N+1: assistant, message.id: X, content: [tool_use: ORPHAN_ID]
            Index N+2: assistant, message.id: X, content: [tool_use: VALID_ID]
            Index N+3: user, content: [tool_result: ORPHAN_ID, tool_result: VALID_ID]
          
          如果startIndex = N+2：
            - 旧代码：只检查消息N+2的tool_results，未找到，返回N+2
            - 切片后normalizeMessagesForAPI按message.id合并：
              msg[1]: assistant with [tool_use: VALID_ID]  (ORPHAN tool_use被排除！)
              msg[2]: user with [tool_result: ORPHAN_ID, tool_result: VALID_ID]
            - API错误：orphan tool_result引用不存在的tool_use
        
        Thinking块场景：
          会话存储（压缩前）：
            Index N:   assistant, message.id: X, content: [thinking]
            Index N+1: assistant, message.id: X, content: [tool_use: ID]
            Index N+2: user, content: [tool_result: ID]
          
          如果startIndex = N+1：
            - 无此修复：N处的thinking块被排除
            - normalizeMessagesForAPI后：thinking块丢失（无消息可合并）
          
          修复后代码：检测消息N+1与N有相同message.id，调整到N。
        """
        if start_index <= 0 or start_index >= len(messages):
            return start_index
        
        adjusted_index = start_index
        
        # 第一步：处理tool_use/tool_result配对
        # 收集保留范围内ALL消息的tool_result IDs
        all_tool_result_ids: list[str] = []
        for i in range(start_index, len(messages)):
            all_tool_result_ids.extend(self.get_tool_result_ids(messages[i]))
        
        if all_tool_result_ids:
            # 收集保留范围内已有的tool_use IDs
            tool_use_ids_in_kept_range: set[str] = set()
            for i in range(adjusted_index, len(messages)):
                msg = messages[i]
                if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
                    for block in msg.get("content", []):
                        if block.get("type") == "tool_use":
                            tool_use_ids_in_kept_range.add(block.get("id", ""))
            
            # 只查找不在保留范围内的tool_uses
            needed_tool_use_ids = set(
                id for id in all_tool_result_ids if id not in tool_use_ids_in_kept_range
            )
            
            # 向后查找包含匹配tool_use块的助手消息
            for i in range(adjusted_index - 1, -1, -1):
                if not needed_tool_use_ids:
                    break
                message = messages[i]
                if self.has_tool_use_with_ids(message, needed_tool_use_ids):
                    adjusted_index = i
                    # 从集合移除已找到的tool_use_ids
                    if message.get("role") == "assistant" and isinstance(message.get("content"), list):
                        for block in message.get("content", []):
                            if block.get("type") == "tool_use" and block.get("id") in needed_tool_use_ids:
                                needed_tool_use_ids.discard(block.get("id"))
        
        # 第二步：处理与保留范围内助手消息共享message.id的thinking块
        # 收集保留范围内所有助手消息的message.ids
        message_ids_in_kept_range: set[str] = set()
        for i in range(adjusted_index, len(messages)):
            msg = messages[i]
            if msg.get("role") == "assistant" and msg.get("message", {}).get("id"):
                message_ids_in_kept_range.add(msg.get("message", {}).get("id"))
        
        # 向后查找不在保留范围内但具有相同message.id的助手消息
        # 这些可能包含需要normalizeMessagesForAPI合并的thinking块
        for i in range(adjusted_index - 1, -1, -1):
            message = messages[i]
            if (
                message.get("role") == "assistant"
                and message.get("message", {}).get("id")
                and message.get("message", {}).get("id") in message_ids_in_kept_range
            ):
                # 此消息与保留范围内消息有相同message.id
                # 包含它以便thinking块可正确合并
                adjusted_index = i
        
        return adjusted_index
    
    def calculate_messages_to_keep_index(
        self,
        messages: list[dict[str, Any]],
        last_summarized_index: int,
    ) -> int:
        """
        计算压缩后应保留消息的起始索引。
        从lastSummarizedMessageId开始，向后扩展以满足最小值：
        - 至少config.minTokens个Token
        - 至少config.minTextBlockMessages条文本块消息
        如果达到config.maxTokens则停止扩展。
        同时确保tool_use/tool_result配对不被分割。
        """
        if not messages:
            return 0
        
        config = self.config
        
        # 从lastSummarizedIndex之后的消息开始
        # 如果lastSummarizedIndex为-1（未找到）或messages.length（无摘要id）
        # 我们开始时不保留消息
        start_index = last_summarized_index + 1 if last_summarized_index >= 0 else len(messages)
        
        # 计算从startIndex到末尾的当前Token数和文本块消息数
        total_tokens = 0
        text_block_message_count = 0
        for i in range(start_index, len(messages)):
            msg = messages[i]
            total_tokens += self.estimator.estimate_message_tokens(msg)
            if self.has_text_blocks(msg):
                text_block_message_count += 1
        
        # 检查是否已达到最大上限
        if total_tokens >= config.max_tokens:
            return self.adjust_index_to_preserve_api_invariants(messages, start_index)
        
        # 检查是否已满足两个最小值
        if total_tokens >= config.min_tokens and text_block_message_count >= config.min_text_block_messages:
            return self.adjust_index_to_preserve_api_invariants(messages, start_index)
        
        # 向后扩展直到满足两个最小值或达到上限。
        # 以最后一个边界为底限：保留段链在此有磁盘
        # 不连续（att[0]→summary shortcut from dedup-skip），这会
        # 让加载器的tail→head walk绕过内部保留消息
        # 然后裁剪。响应式压缩已通过getMessagesAfterCompactBoundary
        # 在边界切片；这是相同的不变量。
        floor_index = 0
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("is_boundary_marker"):
                floor_index = i + 1
                break
        
        for i in range(start_index - 1, floor_index - 1, -1):
            msg = messages[i]
            msg_tokens = self.estimator.estimate_message_tokens(msg)
            total_tokens += msg_tokens
            if self.has_text_blocks(msg):
                text_block_message_count += 1
            start_index = i
            
            # 达到上限则停止
            if total_tokens >= config.max_tokens:
                break
            
            # 满足两个最小值则停止
            if total_tokens >= config.min_tokens and text_block_message_count >= config.min_text_block_messages:
                break
        
        # 调整工具配对
        return self.adjust_index_to_preserve_api_invariants(messages, start_index)
    
    def try_session_memory_compaction(
        self,
        messages: list[dict[str, Any]],
        auto_compact_threshold: int | None = None,
    ) -> CompactResult | None:
        """
        尝试使用会话记忆替代传统压缩。
        如果会话记忆压缩无法使用则返回null。
        
        处理两种场景：
        1. 正常情况：lastSummarizedMessageId已设置，只保留该ID之后的消息
        2. 恢复会话：lastSummarizedMessageId未设置但会话记忆有内容，
           保留所有消息但使用会话记忆作为摘要
        """
        if not self.should_use_session_memory_compact():
            return None
        
        session_memory = self._state.session_memory_content
        if not session_memory:
            return None
        
        # 会话记忆存在但匹配模板（无实际内容提取）
        # 回退到传统压缩行为
        if self._is_session_memory_empty(session_memory):
            return None
        
        try:
            last_summarized_index: int
            
            if self._state.last_summarized_message_id:
                # 正常情况：我们确切知道哪些消息已被摘要
                last_summarized_index = -1
                for i, msg in enumerate(messages):
                    if msg.get("uuid") == self._state.last_summarized_message_id:
                        last_summarized_index = i
                        break
                
                if last_summarized_index == -1:
                    # 摘要消息ID不存在于当前消息
                    # 这可能在消息被修改时发生 - 回退到传统压缩
                    # 因为无法确定已摘要和未摘要消息的边界
                    return None
            else:
                # 恢复会话情况：会话记忆有内容但不知道边界
                # 将lastSummarizedIndex设为最后消息使startIndex成为messages.length（初始不保留消息）
                last_summarized_index = len(messages) - 1
            
            # 计算应保留消息的起始索引
            # 从lastSummarizedIndex开始，扩展以满足最小值，
            # 调整以不分割tool_use/tool_result配对
            start_index = self.calculate_messages_to_keep_index(messages, last_summarized_index)
            
            # 从保留消息中过滤旧的压缩边界消息。
            # REPL裁剪后，从保留消息中重新yield的旧边界会
            # 触发不想要的第二次裁剪（isCompactBoundaryMessage返回true），
            # 丢弃新边界和摘要。
            messages_to_keep = [
                msg for msg in messages[start_index:]
                if not msg.get("is_boundary_marker")
            ]
            
            # 创建压缩边界标记
            pre_compact_token_count = sum(self.estimator.estimate_message_tokens(msg) for msg in messages)
            boundary_marker = CompactBoundaryMarker(
                compaction_type="session_memory",
                pre_compact_token_count=pre_compact_token_count,
                messages_summarized=start_index,
            )
            
            # 创建摘要消息
            truncated_content = self._truncate_session_memory_for_compact(session_memory)
            summary_message = {
                "role": "user",
                "content": self._build_summary_content(truncated_content),
                "is_compact_summary": True,
                "is_visible_in_transcript_only": True,
            }
            
            # 构建压缩结果
            result_messages = [
                boundary_marker.to_message(),
                summary_message,
                *messages_to_keep,
            ]
            
            post_compact_token_count = sum(self.estimator.estimate_message_tokens(msg) for msg in result_messages)
            
            # 如果提供阈值，检查是否超出
            if auto_compact_threshold is not None and post_compact_token_count >= auto_compact_threshold:
                return None
            
            return CompactResult(
                boundary_marker=boundary_marker,
                summary_messages=[summary_message],
                messages_to_keep=messages_to_keep,
                pre_compact_token_count=pre_compact_token_count,
                post_compact_token_count=post_compact_token_count,
                true_post_compact_token_count=post_compact_token_count,
            )
            
        except Exception:
            return None
    
    def _is_session_memory_empty(self, content: str) -> bool:
        """检查会话记忆是否为空模板"""
        if len(content) < 100:
            return True
        template_markers = ["<session_memory>", "No conversation history"]
        return all(marker in content for marker in template_markers) and "## " not in content
    
    def _truncate_session_memory_for_compact(self, content: str) -> str:
        """为压缩截断过大会话记忆段"""
        max_length = 50_000
        if len(content) <= max_length:
            return content
        return content[:max_length] + "\n\n[某些会话记忆段因长度被截断。完整会话记忆可查看会话记忆文件]"
    
    def _build_summary_content(self, session_memory: str) -> str:
        """构建摘要内容"""
        return f"<conversation_summary>\n{session_memory}\n</conversation_summary>\n\n请继续之前的工作。"
    
    def reset_state(self) -> None:
        """重置会话记忆状态"""
        self._state = SessionMemoryState()


session_memory_compact = SessionMemoryCompact()