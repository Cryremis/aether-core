# backend/app/services/context/message_compaction.py
"""
消息压缩服务

实现多种上下文管理压缩策略：
- MicroCompact：剥离旧的工具结果内容而不生成完整摘要
- SessionMemoryCompact：基于年龄/优先级裁剪消息
- FullCompact：通过LLM生成对话摘要
- PartialCompact：仅压缩部分消息
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
from app.services.context.priority_manager import MessagePriority, PriorityManager
from app.services.context.truncate import truncate_tool_result


class CompactionStrategy(Enum):
    Micro = "micro"
    SessionMemory = "session_memory"
    Full = "full"
    Partial = "partial"
    TimeBased = "time_based"


@dataclass
class MicroCompactConfig:
    """微压缩操作配置。"""
    enabled: bool = True
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
    time_based_enabled: bool = True
    time_based_gap_threshold_minutes: float = 30.0
    keep_recent_count: int = 3
    trigger_threshold: int = 10
    cleared_message: str = "[旧工具结果内容已清除]"
    image_token_estimate: int = 2000


@dataclass
class CompactBoundaryMarker:
    """
    标记对话中压缩边界的标记。
    在压缩后放置，标记旧上下文被摘要的位置。
    """
    marker_id: str = field(default_factory=lambda: f"boundary_{uuid.uuid4().hex}")
    compaction_type: str = "auto"
    pre_compact_token_count: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    previous_message_id: str | None = None
    messages_summarized: int = 0
    custom_instructions: str | None = None
    pre_compact_discovered_tools: list[str] = field(default_factory=list)
    
    def to_message(self) -> dict[str, Any]:
        """将边界标记转换为系统消息格式。"""
        return {
            "role": "system",
            "content": self._build_boundary_content(),
            "is_boundary_marker": True,
            "marker_id": self.marker_id,
            "timestamp": self.timestamp.isoformat(),
        }
    
    def _build_boundary_content(self) -> str:
        """构建边界标记内容字符串。"""
        parts = [
            f"<compact_boundary type=\"{self.compaction_type}\">",
            f"  <timestamp>{self.timestamp.isoformat()}</timestamp>",
            f"  <pre_compact_tokens>{self.pre_compact_token_count}</pre_compact_tokens>",
            f"  <messages_summarized>{self.messages_summarized}</messages_summarized>",
        ]
        if self.previous_message_id:
            parts.append(f"  <previous_message_id>{self.previous_message_id}</previous_message_id>")
        parts.append("</compact_boundary>")
        return "\n".join(parts)


@dataclass
class MicroCompactResult:
    """微压缩操作结果。"""
    messages: list[dict[str, Any]]
    tools_cleared: int = 0
    tokens_saved: int = 0
    trigger_type: str = "auto"
    cleared_tool_ids: list[str] = field(default_factory=list)


@dataclass
class CompactResult:
    """完整压缩操作结果。"""
    boundary_marker: CompactBoundaryMarker
    summary_messages: list[dict[str, Any]]
    messages_to_keep: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    pre_compact_token_count: int = 0
    post_compact_token_count: int = 0
    true_post_compact_token_count: int = 0
    compaction_usage: dict[str, int] = field(default_factory=dict)
    user_display_message: str | None = None
    was_successful: bool = True


class MessageCompactor:
    """
    处理各种消息压缩策略。
    
    实现：
    1. 微压缩：剥离旧工具结果而不生成完整摘要
    2. 会话记忆压缩：基于年龄/优先级裁剪
    3. 完整压缩：生成基于LLM的摘要
    4. 部分压缩：压缩部分消息
    """
    
    def __init__(
        self,
        micro_config: MicroCompactConfig | None = None,
        estimator: TokenEstimator | None = None,
        priority_manager: PriorityManager | None = None,
    ):
        self.micro_config = micro_config or MicroCompactConfig()
        self.estimator = estimator or TokenEstimator()
        self.priority_manager = priority_manager or PriorityManager()
        self._compact_warning_suppressed: bool = False
        self._time_based_last_compact: float = 0
    
    def micro_compact_messages(
        self,
        messages: list[dict[str, Any]],
        query_source: str | None = None,
    ) -> MicroCompactResult:
        """
        对消息执行微压缩。
        剥离旧工具结果内容而不生成摘要。
        使用时间触发和可压缩工具识别。
        """
        if not self.micro_config.enabled:
            return MicroCompactResult(messages=messages)
        
        time_based_result = self._maybe_time_based_microcompact(messages, query_source)
        if time_based_result:
            return time_based_result
        
        return MicroCompactResult(messages=messages)
    
    def _maybe_time_based_microcompact(
        self,
        messages: list[dict[str, Any]],
        query_source: str | None = None,
    ) -> MicroCompactResult | None:
        """
        时间触发的微压缩：当间隔超过阈值时清除旧工具结果。
        
        时间触发的微压缩：当自上次主循环助手消息以来的间隔超过配置阈值时，
        清除除最近N个可压缩工具结果外的所有内容。
        
        当触发未触发时返回null（禁用、错误源、间隔低于阈值、无内容可清除）。
        
        与缓存MC不同，这直接修改消息内容。缓存是冷的，
        因此没有可通过cache_edits保留的缓存前缀。
        """
        if not self.micro_config.time_based_enabled:
            return None
        
        main_thread_sources = {"repl_main_thread", "agent", None}
        if query_source not in main_thread_sources:
            return None
        
        last_assistant = self._find_last_assistant_message(messages)
        if not last_assistant:
            return None
        
        last_timestamp = self._get_message_timestamp(last_assistant)
        if not last_timestamp:
            return None
        
        gap_minutes = (datetime.now(timezone.utc) - last_timestamp).total_seconds() / 60
        if gap_minutes < self.micro_config.time_based_gap_threshold_minutes:
            return None
        
        compactable_ids = self._collect_compactable_tool_ids(messages)
        
        keep_count = max(1, self.micro_config.keep_recent_count)
        keep_set = set(compactable_ids[-keep_count:])
        clear_set = set(compactable_ids[:-keep_count]) if len(compactable_ids) > keep_count else set()
        
        if not clear_set:
            return None
        
        tokens_saved = 0
        cleared_count = 0
        cleared_ids: list[str] = []
        
        result_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") != "tool":
                result_messages.append(msg)
                continue
            
            tool_call_id = msg.get("tool_call_id", "")
            content = msg.get("content", "")
            
            if tool_call_id in clear_set and content != self.micro_config.cleared_message:
                tokens_saved += self._calculate_tool_result_tokens(msg)
                cleared_count += 1
                cleared_ids.append(tool_call_id)
                result_messages.append({
                    **msg,
                    "content": self.micro_config.cleared_message,
                })
            else:
                result_messages.append(msg)
        
        if tokens_saved == 0:
            return None
        
        self._compact_warning_suppressed = True
        self._time_based_last_compact = time.time()
        
        return MicroCompactResult(
            messages=result_messages,
            tools_cleared=cleared_count,
            tokens_saved=tokens_saved,
            trigger_type="time_based",
            cleared_tool_ids=cleared_ids,
        )
    
    def _collect_compactable_tool_ids(self, messages: list[dict[str, Any]]) -> list[str]:
        """收集属于可压缩工具的工具调用ID。"""
        ids: list[str] = []
        for msg in messages:
            if msg.get("role") == "assistant":
                tool_calls = msg.get("tool_calls", [])
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        func = tc.get("function", {})
                        name = func.get("name", "")
                        if name in self.micro_config.compactable_tool_names:
                            ids.append(tc.get("id", ""))
        return ids
    
    def _find_last_assistant_message(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        """查找消息列表中最后一个助手消息。"""
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                return msg
        return None
    
    def _get_message_timestamp(self, message: dict[str, Any]) -> datetime | None:
        """解析消息时间戳。"""
        ts = message.get("timestamp")
        if ts:
            try:
                if isinstance(ts, str):
                    return datetime.fromisoformat(ts)
                if isinstance(ts, datetime):
                    return ts
            except ValueError:
                pass
        return None
    
    def _calculate_tool_result_tokens(self, message: dict[str, Any]) -> int:
        """计算工具结果消息的Token数量。"""
        content = message.get("content", "")
        if isinstance(content, str):
            return self.estimator.rough_token_count(content)
        if isinstance(content, list):
            total = 0
            for item in content:
                if isinstance(item, dict):
                    item_type = item.get("type", "text")
                    if item_type in ("image", "document"):
                        total += self.micro_config.image_token_estimate
                    elif item_type == "text":
                        total += self.estimator.rough_token_count(item.get("text", ""))
                    else:
                        total += self.estimator.rough_token_count(json.dumps(item))
                else:
                    total += self.estimator.rough_token_count(str(item))
            return total
        return 0
    
    def strip_images_from_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        在压缩前剥离用户消息中的图片块。
        
        图片在摘要生成时不需要，可能在压缩期间导致prompt-too-long错误。
        """
        from app.services.context.truncate import strip_images_from_messages
        return strip_images_from_messages(messages)
    
    def estimate_compaction_savings(
        self,
        messages: list[dict[str, Any]],
        target_tokens: int,
    ) -> dict[str, int]:
        """估算压缩可能节省的Token数量。"""
        current_tokens = self.estimator.estimate_messages_tokens(messages)
        
        messages_to_compact, messages_to_keep = self.priority_manager.get_messages_to_compact(
            messages, target_tokens, self.estimator
        )
        
        compactable_tokens = self.estimator.estimate_messages_tokens(messages_to_compact)
        kept_tokens = self.estimator.estimate_messages_tokens(messages_to_keep)
        
        summary_estimate = min(int(compactable_tokens * 0.1), 8000)
        
        return {
            "current_tokens": current_tokens,
            "compactable_tokens": compactable_tokens,
            "kept_tokens": kept_tokens,
            "estimated_post_compact_tokens": kept_tokens + summary_estimate,
            "estimated_savings": current_tokens - (kept_tokens + summary_estimate),
        }
    
    def create_boundary_marker(
        self,
        compaction_type: str,
        pre_compact_token_count: int,
        previous_message_id: str | None = None,
        messages_summarized: int = 0,
    ) -> CompactBoundaryMarker:
        """创建压缩边界标记。"""
        return CompactBoundaryMarker(
            compaction_type=compaction_type,
            pre_compact_token_count=pre_compact_token_count,
            previous_message_id=previous_message_id,
            messages_summarized=messages_summarized,
        )
    
    def create_summary_message(
        self,
        summary_text: str,
        suppress_follow_up: bool = False,
    ) -> dict[str, Any]:
        """为压缩后上下文创建摘要消息。"""
        content = f"<conversation_summary>\n{summary_text}\n</conversation_summary>"
        if not suppress_follow_up:
            content += "\n\n请继续之前的工作。"
        
        return {
            "role": "user",
            "content": content,
            "is_compact_summary": True,
        }
    
    def build_post_compact_messages(
        self,
        result: CompactResult,
    ) -> list[dict[str, Any]]:
        """
        从CompactResult构建压缩后消息数组。
        
        顺序：边界标记、摘要消息、保留消息、附件。
        """
        messages: list[dict[str, Any]] = []
        
        messages.append(result.boundary_marker.to_message())
        messages.extend(result.summary_messages)
        messages.extend(result.messages_to_keep)
        messages.extend(result.attachments)
        
        return messages
    
    def suppress_compact_warning(self) -> None:
        """在成功压缩后抑制压缩警告。"""
        self._compact_warning_suppressed = True
    
    def clear_compact_warning_suppression(self) -> None:
        """在开始新的压缩尝试时清除抑制标志。"""
        self._compact_warning_suppressed = False
    
    def is_warning_suppressed(self) -> bool:
        """检查压缩警告是否被抑制。"""
        return self._compact_warning_suppressed