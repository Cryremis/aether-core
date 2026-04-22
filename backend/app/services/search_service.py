# backend/app/services/search_service.py
"""
搜索工具服务，提供 Glob 和 Grep 工具。
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Any

from app.services.ripgrep_service import ripgrep_service
from app.services.session_types import AgentSession
from app.sandbox.models import SandboxWorkspace
from app.core.config import settings


GLOB_DESCRIPTION = """- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- When you are doing an open-ended search that may require multiple rounds of globbing and grepping, consider spawning a sub-agent"""

GLOB_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "The glob pattern to match files against (e.g., \"**/*.ts\", \"src/**/*.py\")",
        },
        "path": {
            "type": "string",
            "description": "The directory to search in. Defaults to workspace root if not specified.",
        },
    },
    "required": ["pattern"],
    "additionalProperties": False,
}


GREP_DESCRIPTION = """A powerful search tool built on ripgrep.

Usage:
- ALWAYS use grep for search tasks. NEVER invoke `grep` or `rg` via sandbox_shell.
- Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
- Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type parameter
- Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts
- Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use `interface\\{\\}` to find `interface{}` in Go code)
- Multiline matching: By default patterns match within single lines only. For cross-line patterns, use `multiline: true`"""

GREP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "The regular expression pattern to search for in file contents",
        },
        "path": {
            "type": "string",
            "description": "File or directory to search in. Defaults to workspace root.",
        },
        "glob": {
            "type": "string",
            "description": "Glob pattern to filter files (e.g., \"*.js\", \"**/*.tsx\", \"*.{ts,tsx}\")",
        },
        "type": {
            "type": "string",
            "description": "File type to search (e.g., \"js\", \"py\", \"rust\", \"go\", \"java\")",
        },
        "output_mode": {
            "type": "string",
            "enum": ["content", "files_with_matches", "count"],
            "description": "Output mode. Defaults to \"files_with_matches\".",
        },
        "-i": {
            "type": "boolean",
            "description": "Case insensitive search",
        },
        "-n": {
            "type": "boolean",
            "description": "Show line numbers in output. Defaults to true for content mode.",
        },
        "-B": {
            "type": "integer",
            "description": "Number of lines to show before each match. Requires output_mode: \"content\".",
        },
        "-A": {
            "type": "integer",
            "description": "Number of lines to show after each match. Requires output_mode: \"content\".",
        },
        "-C": {
            "type": "integer",
            "description": "Number of lines to show before and after each match. Requires output_mode: \"content\".",
        },
        "context": {
            "type": "integer",
            "description": "Alias for -C. Number of context lines around each match.",
        },
        "head_limit": {
            "type": "integer",
            "description": "Limit output to first N results. Pass 0 for unlimited. Defaults to 250.",
        },
        "offset": {
            "type": "integer",
            "description": "Skip first N results before applying head_limit. Defaults to 0.",
        },
        "multiline": {
            "type": "boolean",
            "description": "Enable multiline mode where . matches newlines. Default: false.",
        },
    },
    "required": ["pattern"],
    "additionalProperties": False,
}


@dataclass
class GlobArgs:
    pattern: str
    path: str | None = None


@dataclass
class GrepArgs:
    pattern: str
    path: str | None = None
    glob: str | None = None
    file_type: str | None = None
    output_mode: str = "files_with_matches"
    case_insensitive: bool = False
    show_line_numbers: bool = True
    context_before: int | None = None
    context_after: int | None = None
    context: int | None = None
    head_limit: int = 250
    offset: int = 0
    multiline: bool = False


class SearchService:
    """搜索工具服务，处理 glob 和 grep 工具调用。"""

    def _normalize_container_path(self, path: str) -> str:
        normalized = path.replace("\\", "/")
        return posixpath.normpath(normalized)

    def get_schemas(self) -> list[dict[str, Any]]:
        """返回 glob 和 grep 工具的 schema 定义。"""
        return [
            {"name": "glob", "description": GLOB_DESCRIPTION, "parameters": GLOB_SCHEMA},
            {"name": "grep", "description": GREP_DESCRIPTION, "parameters": GREP_SCHEMA},
        ]

    def parse_glob_args(self, arguments: dict[str, Any]) -> GlobArgs:
        """解析 glob 工具参数。"""
        return GlobArgs(
            pattern=str(arguments.get("pattern", "")),
            path=arguments.get("path"),
        )

    def parse_grep_args(self, arguments: dict[str, Any]) -> GrepArgs:
        """解析 grep 工具参数。"""
        return GrepArgs(
            pattern=str(arguments.get("pattern", "")),
            path=arguments.get("path"),
            glob=arguments.get("glob"),
            file_type=arguments.get("type"),
            output_mode=str(arguments.get("output_mode") or "files_with_matches"),
            case_insensitive=bool(arguments.get("-i", False)),
            show_line_numbers=bool(arguments.get("-n", True)),
            context_before=self._parse_int_optional(arguments.get("-B")),
            context_after=self._parse_int_optional(arguments.get("-A")),
            context=self._parse_int_optional(arguments.get("context") or arguments.get("-C")),
            head_limit=self._parse_int_required(arguments.get("head_limit"), 250),
            offset=self._parse_int_required(arguments.get("offset"), 0),
            multiline=bool(arguments.get("multiline", False)),
        )

    def _parse_int_required(self, value: Any, default: int) -> int:
        """解析整数参数，必须返回非 None 值。"""
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _parse_int_optional(self, value: Any) -> int | None:
        """解析整数参数，可返回 None。"""
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def resolve_cwd(self, session: AgentSession, path: str | None) -> str:
        """解析工作目录（容器内路径）。
        
        支持以下路径格式：
        - None 或空：默认 /workspace/work
        - 绝对路径：直接返回（假设是容器内路径）
        - 相对路径：相对于 /workspace 解析
        - 特殊目录名（input/output/skills/logs）：映射到对应容器目录
        """
        if session.workspace is None:
            raise RuntimeError("会话沙箱尚未初始化。")
        
        container_root = settings.sandbox_docker_workspace_mount
        
        if path is None or not path.strip():
            return settings.sandbox_docker_work_dir
        
        path = self._normalize_container_path(path.strip())
        
        if path.startswith("/"):
            return path
        
        special_dirs = {
            "input": settings.sandbox_docker_input_dir,
            "output": settings.sandbox_docker_output_dir,
            "skills": settings.sandbox_docker_skills_dir,
            "logs": settings.sandbox_docker_logs_dir,
            "work": settings.sandbox_docker_work_dir,
        }
        
        first_part = path.split("/")[0].split("\\")[0]
        if first_part in special_dirs:
            remainder = path[len(first_part):].strip("/\\")
            base = special_dirs[first_part]
            if remainder:
                return posixpath.join(base, remainder)
            return base
        
        return posixpath.join(container_root, path)

    async def execute_glob(self, session: AgentSession, arguments: dict[str, Any]) -> dict[str, Any]:
        """执行 glob 工具。"""
        args = self.parse_glob_args(arguments)
        cwd = self.resolve_cwd(session, args.path)

        if session.workspace is None:
            raise RuntimeError("会话沙箱尚未初始化。")

        result = await ripgrep_service.glob(
            workspace=session.workspace,
            pattern=args.pattern,
            cwd=cwd,
            limit=100,
        )

        output_lines = result.files.copy() if result.files else ["No files found"]
        if result.truncated:
            output_lines.extend(["", "(Results are truncated. Consider using a more specific path or pattern.)"])

        return {
            "filenames": output_lines,
            "num_files": result.num_files,
            "duration_ms": result.duration_ms,
            "truncated": result.truncated,
        }

    async def execute_grep(self, session: AgentSession, arguments: dict[str, Any]) -> dict[str, Any]:
        """执行 grep 工具。"""
        args = self.parse_grep_args(arguments)
        cwd = self.resolve_cwd(session, args.path)

        if session.workspace is None:
            raise RuntimeError("会话沙箱尚未初始化。")

        result = await ripgrep_service.grep(
            workspace=session.workspace,
            pattern=args.pattern,
            cwd=cwd,
            mode=args.output_mode,
            glob=args.glob,
            file_type=args.file_type,
            context_before=args.context_before,
            context_after=args.context_after,
            context=args.context,
            show_line_numbers=args.show_line_numbers,
            case_insensitive=args.case_insensitive,
            head_limit=args.head_limit,
            offset=args.offset,
            multiline=args.multiline,
        )

        return self._format_grep_result(result)

    def _format_grep_result(self, result) -> dict[str, Any]:
        """格式化 grep 结果。"""
        if result.mode == "content":
            return self._format_content_result(result)
        if result.mode == "count":
            return self._format_count_result(result)
        return self._format_files_result(result)

    def _format_content_result(self, result) -> dict[str, Any]:
        """格式化 content 模式结果。"""
        content = result.content or "No matches found"
        pagination = self._build_pagination_info(result.applied_limit, result.applied_offset)
        if pagination:
            content += f"\n\n[Showing results with pagination = {pagination}]"
        return {
            "content": content,
            "num_lines": result.num_lines,
            "duration_ms": result.duration_ms,
        }

    def _format_count_result(self, result) -> dict[str, Any]:
        """格式化 count 模式结果。"""
        content = result.content or "No matches found"
        matches = result.num_matches or 0
        files = result.num_files or 0
        pagination = self._build_pagination_info(result.applied_limit, result.applied_offset)
        suffix = f" with pagination = {pagination}" if pagination else ""
        content += f"\n\nFound {matches} total occurrences across {files} files.{suffix}"
        return {
            "content": content,
            "num_matches": matches,
            "num_files": files,
            "duration_ms": result.duration_ms,
        }

    def _format_files_result(self, result) -> dict[str, Any]:
        """格式化 files_with_matches 模式结果。"""
        if result.num_files == 0:
            return {
                "filenames": [],
                "num_files": 0,
                "content": "No files found",
                "duration_ms": result.duration_ms,
            }

        pagination = self._build_pagination_info(result.applied_limit, result.applied_offset)
        header = f"Found {result.num_files} files"
        if pagination:
            header += f" ({pagination})"

        return {
            "filenames": result.filenames,
            "num_files": result.num_files,
            "content": header + "\n" + "\n".join(result.filenames),
            "duration_ms": result.duration_ms,
        }

    def _build_pagination_info(self, limit: int | None, offset: int | None) -> str:
        """构建分页信息字符串。"""
        parts = []
        if limit is not None:
            parts.append(f"limit: {limit}")
        if offset:
            parts.append(f"offset: {offset}")
        return ", ".join(parts)


search_service = SearchService()
