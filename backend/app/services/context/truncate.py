# backend/app/services/context/truncate.py
"""
文本截断工具

提供宽度感知的文本和文件路径截断功能。
正确处理Unicode字符、CJK和emoji。
"""

from __future__ import annotations

import re
from typing import Any


def truncate_text(text: str, max_length: int, ellipsis: str = "...") -> str:
    """截断文本到最大长度，如果截断则添加省略号。"""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(ellipsis)] + ellipsis


def truncate_path_middle(path: str, max_length: int) -> str:
    """
    在中间截断文件路径，保留目录和文件名。
    
    例如："src/components/deeply/nested/MyComponent.tsx"
          -> "src/components/.../MyComponent.tsx"
    """
    if len(path) <= max_length:
        return path
    
    if max_length <= 0:
        return "..."
    
    if max_length < 5:
        return truncate_text(path, max_length)
    
    last_sep_index = max(path.rfind("/"), path.rfind("\\"))
    
    if last_sep_index < 0:
        return truncate_text(path, max_length)
    
    filename = path[last_sep_index:]
    directory = path[:last_sep_index]
    
    if len(filename) >= max_length - 1:
        return truncate_text(path, max_length)
    
    available_for_dir = max_length - 1 - len(filename)
    
    if available_for_dir <= 0:
        return truncate_text(filename, max_length)
    
    truncated_dir = truncate_to_width_no_ellipsis(directory, available_for_dir)
    return truncated_dir + "..." + filename


def truncate_to_width(text: str, max_width: int, ellipsis: str = "...") -> str:
    """截断文本以适应最大显示宽度。截断时添加省略号。"""
    if len(text) <= max_width:
        return text
    
    if max_width <= 1:
        return ellipsis[:max_width]
    
    return text[:max_width - len(ellipsis)] + ellipsis


def truncate_to_width_no_ellipsis(text: str, max_width: int) -> str:
    """截断文本而不添加省略号。当调用者添加自己的分隔符时有用。"""
    if len(text) <= max_width:
        return text
    
    if max_width <= 0:
        return ""
    
    return text[:max_width]


def truncate_start_to_width(text: str, max_width: int, ellipsis: str = "...") -> str:
    """从开头截断，保留尾部。截断时在开头添加省略号。"""
    if len(text) <= max_width:
        return text
    
    if max_width <= 1:
        return ellipsis[:max_width]
    
    start_idx = len(text) - (max_width - len(ellipsis))
    return ellipsis + text[start_idx:]


def truncate_tool_result(
    content: str,
    max_length: int,
    truncation_marker: str = "[旧工具结果内容已清除]",
) -> str:
    """为微压缩截断工具结果内容。用截断标记替换冗长输出。"""
    if len(content) <= max_length:
        return content
    
    return truncation_marker


def truncate_json_content(
    content: str | dict[str, Any] | list[Any],
    max_length: int,
) -> str:
    """
    截断JSON内容同时保留结构指示。
    
    数组：显示前几个项目并附带 "[...] X more items"
    对象：显示前几个键并附带 "{...} X more keys"
    """
    if isinstance(content, str):
        if len(content) <= max_length:
            return content
        return truncate_text(content, max_length)
    
    if isinstance(content, list):
        if len(content) == 0:
            return "[]"
        result = "["
        shown_count = 0
        for i, item in enumerate(content[:5]):
            if i > 0:
                result += ", "
            item_str = truncate_json_content(str(item), 100)
            result += item_str
            shown_count = i + 1
            if len(result) > max_length - 50:
                break
        if len(content) > shown_count:
            remaining = len(content) - shown_count
            result += f", ... {remaining} more items"
        result += "]"
        return result
    
    if isinstance(content, dict):
        if len(content) == 0:
            return "{}"
        result = "{"
        keys = list(content.keys())
        shown_count = 0
        for i, key in enumerate(keys[:5]):
            if i > 0:
                result += ", "
            value_str = truncate_json_content(str(content[key]), 50)
            result += f"{key}: {value_str}"
            shown_count = i + 1
            if len(result) > max_length - 50:
                break
        if len(content) > shown_count:
            remaining = len(content) - shown_count
            result += f", ... {remaining} more keys"
        result += "}"
        return result
    
    return truncate_text(str(content), max_length)


def strip_images_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    在压缩前从用户消息中剥离图片块。
    
    图片在摘要生成时不需要，可能在压缩期间导致prompt-too-long错误。
    """
    return [
        _strip_images_from_message(msg) if msg.get("role") == "user" else msg
        for msg in messages
    ]


def _strip_images_from_message(message: dict[str, Any]) -> dict[str, Any]:
    """从单个消息中剥离图片。"""
    content = message.get("content")
    if not isinstance(content, list):
        return message
    
    new_content: list[dict[str, Any]] = []
    has_media = False
    
    for block in content:
        block_type = block.get("type", "text")
        if block_type == "image":
            has_media = True
            new_content.append({"type": "text", "text": "[image]"})
        elif block_type == "document":
            has_media = True
            new_content.append({"type": "text", "text": "[document]"})
        elif block_type == "tool_result":
            tool_content = block.get("content")
            if isinstance(tool_content, list):
                new_tool_content: list[dict[str, Any]] = []
                tool_has_media = False
                for item in tool_content:
                    item_type = item.get("type", "text")
                    if item_type == "image":
                        tool_has_media = True
                        new_tool_content.append({"type": "text", "text": "[image]"})
                    elif item_type == "document":
                        tool_has_media = True
                        new_tool_content.append({"type": "text", "text": "[document]"})
                    else:
                        new_tool_content.append(item)
                if tool_has_media:
                    has_media = True
                    new_content.append({**block, "content": new_tool_content})
                else:
                    new_content.append(block)
            else:
                new_content.append(block)
        else:
            new_content.append(block)
    
    if not has_media:
        return message
    
    return {**message, "content": new_content}