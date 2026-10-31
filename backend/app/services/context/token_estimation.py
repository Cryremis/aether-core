# backend/app/services/context/token_estimation.py
"""
Token估算服务

提供消息和内容的精确与粗略Token估算功能。
实现多种估算策略：
- 粗略估算：字符数 / bytes_per_token（默认为4）
- API精确计数：调用LLM获取准确的Token计数
- 文件类型感知估算：针对JSON等密集格式调整估算比例
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ContentType(Enum):
    """内容类型枚举"""
    TEXT = "text"
    IMAGE = "image"
    DOCUMENT = "document"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"
    REASONING = "reasoning"
    SYSTEM = "system"


@dataclass
class TokenEstimationConfig:
    """Token估算配置"""
    bytes_per_token_default: int = 4
    bytes_per_token_json: int = 2
    image_token_estimate: int = 2000
    document_token_estimate: int = 2000
    padding_factor: float = 4.0 / 3.0


class TokenEstimator:
    """Token估算服务，提供多种估算策略。"""

    def __init__(self, config: TokenEstimationConfig | None = None):
        self.config = config or TokenEstimationConfig()

    def rough_token_count(self, content: str, bytes_per_token: int | None = None) -> int:
        """基于字符类型的保守Token估算。

        ASCII 文本沿用每 4 字符约 1 token；CJK 与其他多字节字符按每字符 1 token
        估算，避免中文输入被明显低估。调用方可继续传入更保守的密集 JSON 比例。
        """
        if not content:
            return 0
        if bytes_per_token is None:
            ascii_chars = sum(1 for char in content if ord(char) < 128)
            multibyte_chars = len(content) - ascii_chars
            return int(ascii_chars / self.config.bytes_per_token_default + multibyte_chars)
        return int(len(content) / bytes_per_token)

    def bytes_per_token_for_file_type(self, file_extension: str) -> int:
        """
        返回特定文件类型的每Token字节数比例。
        密集格式如JSON有更多单字符Token（`{`, `}`, `:`, `,`, `"`）。
        """
        dense_extensions = {"json", "jsonl", "jsonc", "yaml", "xml"}
        if file_extension.lower() in dense_extensions:
            return self.config.bytes_per_token_json
        return self.config.bytes_per_token_default

    def estimate_content_tokens(
        self,
        content: str | list[dict[str, Any]] | None,
        file_type: str | None = None,
    ) -> int:
        """估算各种内容类型的Token数量。"""
        if content is None:
            return 0
        if isinstance(content, str):
            if file_type:
                return self.rough_token_count(
                    content,
                    self.bytes_per_token_for_file_type(file_type),
                )
            return self.rough_token_count(content)
        if isinstance(content, list):
            return sum(self._estimate_block_tokens(block) for block in content)
        return 0

    def _estimate_block_tokens(self, block: dict[str, Any] | str) -> int:
        """估算单个内容块的Token数量。"""
        if isinstance(block, str):
            return self.rough_token_count(block)
        
        block_type = block.get("type", "text")
        
        if block_type == "text":
            text = block.get("text", "")
            return self.rough_token_count(text)
        
        if block_type in ("image", "document"):
            return self.config.image_token_estimate
        
        if block_type == "tool_use":
            name = block.get("name", "")
            input_data = block.get("input", {})
            serialized = name + json.dumps(input_data, ensure_ascii=False)
            return self.rough_token_count(serialized)
        
        if block_type == "tool_result":
            result_content = block.get("content", "")
            if isinstance(result_content, str):
                return self.rough_token_count(result_content)
            if isinstance(result_content, list):
                return sum(self._estimate_block_tokens(item) for item in result_content)
            return 0
        
        if block_type in ("thinking", "reasoning"):
            thinking_content = block.get("thinking", "") or block.get("content", "")
            return self.rough_token_count(thinking_content)
        
        return self.rough_token_count(json.dumps(block, ensure_ascii=False))

    def estimate_message_tokens(self, message: dict[str, Any]) -> int:
        """估算单个消息的Token数量。"""
        role = message.get("role", "")
        content = message.get("content")
        
        base_tokens = 4
        
        if role == "system":
            system_content = content if isinstance(content, str) else ""
            return base_tokens + self.rough_token_count(system_content)
        
        if role == "user":
            if isinstance(content, str):
                return base_tokens + self.rough_token_count(content)
            if isinstance(content, list):
                return base_tokens + sum(self._estimate_block_tokens(block) for block in content)
            return base_tokens
        
        if role == "assistant":
            tokens = base_tokens
            if message.get("provider_payload"):
                # 原生请求以保存的 item 为准；展示正文/思考是其投影，不能重复计算。
                return tokens + self.rough_token_count(json.dumps(message["provider_payload"].get("items", []), ensure_ascii=False))
            tokens += self.rough_token_count(str(message.get("reasoning_content") or ""))
            tokens += self.rough_token_count(str(message.get("reasoning") or ""))
            if isinstance(content, str):
                tokens += self.rough_token_count(content)
            elif isinstance(content, list):
                tokens += sum(self._estimate_block_tokens(block) for block in content)
            
            tool_calls = message.get("tool_calls", [])
            for tool_call in tool_calls:
                function = tool_call.get("function", {})
                name = function.get("name", "")
                arguments = function.get("arguments", "")
                tokens += self.rough_token_count(name + arguments) + 10
            
            return tokens
        
        if role == "tool":
            tool_content = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            return base_tokens + self.rough_token_count(tool_content)
        
        return base_tokens

    def estimate_messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        """估算消息列表的总Token数量。应用填充因子进行保守估算。"""
        total = sum(self.estimate_message_tokens(msg) for msg in messages)
        return int(total * self.config.padding_factor)

    def estimate_tools_tokens(self, tools: list[dict[str, Any]] | None) -> int:
        """估算函数工具 schema 的请求开销，避免只统计消息导致预算失真。"""
        if not tools:
            return 0
        total = 0
        for tool in tools:
            try:
                serialized = json.dumps(tool, ensure_ascii=False, separators=(",", ":"))
                total += 8 + self.rough_token_count(serialized, self.config.bytes_per_token_json)
            except (TypeError, ValueError):
                total += self.config.image_token_estimate
        return int(total * self.config.padding_factor)

    def count_from_usage(self, usage: dict[str, int]) -> int:
        """
        从API响应的使用数据计算总上下文Token数。
        包含 input_tokens + cache tokens + output_tokens。
        
        这表示该API调用时的完整上下文大小。
        当你需要从消息计算上下文大小时，请使用 tokenCountWithEstimation()。
        """
        input_tokens = usage.get("input_tokens", 0)
        cache_creation = usage.get("cache_creation_input_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        return input_tokens + cache_creation + cache_read + output_tokens


def rough_token_count(content: str | None, bytes_per_token: int = 4) -> int:
    """粗略Token计数的便捷函数。"""
    if content is None:
        return 0
    return TokenEstimator().rough_token_count(content, bytes_per_token)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """消息Token估算的便捷函数。"""
    return TokenEstimator().estimate_message_tokens(message)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """消息列表Token估算的便捷函数。"""
    return TokenEstimator().estimate_messages_tokens(messages)
