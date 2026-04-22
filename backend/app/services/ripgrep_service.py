# backend/app/services/ripgrep_service.py
"""
Ripgrep 封装服务，通过 sandbox_shell 在容器内执行搜索。
"""
from __future__ import annotations

import shlex
import time
from dataclasses import dataclass, field

from app.sandbox.runner import sandbox_runner
from app.sandbox.models import SandboxWorkspace


@dataclass
class GlobResult:
    files: list[str] = field(default_factory=list)
    truncated: bool = False
    duration_ms: int = 0
    num_files: int = 0


@dataclass
class GrepResult:
    mode: str = "files_with_matches"
    filenames: list[str] = field(default_factory=list)
    content: str = ""
    num_files: int = 0
    num_lines: int = 0
    num_matches: int = 0
    truncated: bool = False
    duration_ms: int = 0
    applied_limit: int | None = None
    applied_offset: int | None = None


VCS_DIRECTORIES = [".git", ".svn", ".hg", ".bzr", ".jj", ".sl"]
DEFAULT_HEAD_LIMIT = 250
DEFAULT_GLOB_LIMIT = 100
MAX_COLUMNS = 500


class RipgrepService:
    """通过沙箱容器执行 ripgrep 搜索。"""

    def _build_command(self, cwd: str, args: list[str]) -> str:
        quoted_args = " ".join(shlex.quote(arg) for arg in args)
        return f"cd {shlex.quote(cwd)} && {quoted_args}"

    async def glob(
        self,
        workspace: SandboxWorkspace,
        pattern: str,
        cwd: str,
        *,
        limit: int = DEFAULT_GLOB_LIMIT,
        offset: int = 0,
        hidden: bool = True,
        no_ignore: bool = True,
        ignore_patterns: list[str] | None = None,
    ) -> GlobResult:
        """在沙箱内执行 glob 搜索。"""
        start = time.perf_counter()

        args = ["rg", "--files", "--glob", pattern, "--sort=modified"]
        if hidden:
            args.append("--hidden")
        if no_ignore:
            args.append("--no-ignore")

        for vcs_dir in VCS_DIRECTORIES:
            args.extend(["--glob", f"!{vcs_dir}"])

        for ignore_pattern in ignore_patterns or []:
            args.extend(["--glob", f"!{ignore_pattern}"])

        cmd = self._build_command(cwd, args)

        result = await sandbox_runner.run_shell(
            workspace=workspace,
            command=cmd,
            shell="bash",
        )

        lines = result.stdout.strip().split("\n") if result.stdout else []
        lines = [line for line in lines if line]

        total = len(lines)
        truncated = total > offset + limit
        files = lines[offset:offset + limit]

        duration_ms = int((time.perf_counter() - start) * 1000)

        return GlobResult(
            files=files,
            truncated=truncated,
            duration_ms=duration_ms + result.duration_ms,
            num_files=len(files),
        )

    async def grep(
        self,
        workspace: SandboxWorkspace,
        pattern: str,
        cwd: str,
        *,
        mode: str = "files_with_matches",
        glob: str | None = None,
        file_type: str | None = None,
        context_before: int | None = None,
        context_after: int | None = None,
        context: int | None = None,
        show_line_numbers: bool = True,
        case_insensitive: bool = False,
        head_limit: int = DEFAULT_HEAD_LIMIT,
        offset: int = 0,
        multiline: bool = False,
        hidden: bool = True,
        ignore_patterns: list[str] | None = None,
    ) -> GrepResult:
        """在沙箱内执行 grep 搜索。"""
        start = time.perf_counter()

        args = ["rg", "--max-columns", str(MAX_COLUMNS)]

        if hidden:
            args.append("--hidden")

        for vcs_dir in VCS_DIRECTORIES:
            args.extend(["--glob", f"!{vcs_dir}"])

        if multiline:
            args.extend(["-U", "--multiline-dotall"])

        if case_insensitive:
            args.append("-i")

        if mode == "files_with_matches":
            args.append("-l")
        elif mode == "count":
            args.append("-c")
        elif mode == "content":
            if show_line_numbers:
                args.append("-n")

        if mode == "content":
            if context is not None:
                args.extend(["-C", str(context)])
            elif context_before is not None:
                args.extend(["-B", str(context_before)])
            if context_after is not None:
                args.extend(["-A", str(context_after)])

        if pattern.startswith("-"):
            args.extend(["-e", pattern])
        else:
            args.append(pattern)

        if file_type:
            args.extend(["--type", file_type])

        if glob:
            for glob_pattern in self._parse_glob_patterns(glob):
                args.extend(["--glob", glob_pattern])

        for ignore_pattern in ignore_patterns or []:
            args.extend(["--glob", f"!{ignore_pattern}"])

        cmd = self._build_command(cwd, args)

        result = await sandbox_runner.run_shell(
            workspace=workspace,
            command=cmd,
            shell="bash",
        )

        if result.exit_code == 1:
            return GrepResult(
                mode=mode,
                duration_ms=int((time.perf_counter() - start) * 1000),
            )

        output = result.stdout.strip() if result.stdout else ""
        lines = output.split("\n") if output else []

        if mode == "content":
            return self._process_content_mode(lines, head_limit, offset, start)
        elif mode == "count":
            return self._process_count_mode(lines, head_limit, offset, start)
        else:
            return self._process_files_mode(lines, head_limit, offset, start)

    def _parse_glob_patterns(self, glob: str) -> list[str]:
        """解析 glob 模式。"""
        patterns = []
        raw_patterns = glob.split()
        for raw_pattern in raw_patterns:
            if "{" in raw_pattern and "}" in raw_pattern:
                patterns.append(raw_pattern)
            else:
                for pattern in raw_pattern.split(","):
                    if pattern:
                        patterns.append(pattern)
        return patterns

    def _process_content_mode(
        self,
        lines: list[str],
        head_limit: int,
        offset: int,
        start: float,
    ) -> GrepResult:
        """处理 content 模式。"""
        effective_limit = head_limit if head_limit != 0 else None

        if effective_limit:
            sliced = lines[offset:offset + effective_limit]
            truncated = len(lines) > offset + effective_limit
        else:
            sliced = lines[offset:]
            truncated = False

        duration_ms = int((time.perf_counter() - start) * 1000)

        return GrepResult(
            mode="content",
            content="\n".join(sliced),
            num_lines=len(sliced),
            truncated=truncated,
            duration_ms=duration_ms,
            applied_limit=effective_limit if truncated else None,
            applied_offset=offset if offset > 0 else None,
        )

    def _process_count_mode(
        self,
        lines: list[str],
        head_limit: int,
        offset: int,
        start: float,
    ) -> GrepResult:
        """处理 count 模式。"""
        effective_limit = head_limit if head_limit != 0 else None

        if effective_limit:
            sliced = lines[offset:offset + effective_limit]
            truncated = len(lines) > offset + effective_limit
        else:
            sliced = lines[offset:]
            truncated = False

        total_matches = 0
        file_count = 0

        for line in sliced:
            colon_idx = line.rfind(":")
            if colon_idx > 0:
                count_str = line[colon_idx + 1:]
                count = int(count_str) if count_str.isdigit() else 0
                total_matches += count
                file_count += 1

        duration_ms = int((time.perf_counter() - start) * 1000)

        return GrepResult(
            mode="count",
            content="\n".join(sliced),
            num_matches=total_matches,
            num_files=file_count,
            truncated=truncated,
            duration_ms=duration_ms,
            applied_limit=effective_limit if truncated else None,
            applied_offset=offset if offset > 0 else None,
        )

    def _process_files_mode(
        self,
        lines: list[str],
        head_limit: int,
        offset: int,
        start: float,
    ) -> GrepResult:
        """处理 files_with_matches 模式。"""
        effective_limit = head_limit if head_limit != 0 else None

        if effective_limit:
            sliced = lines[offset:offset + effective_limit]
            truncated = len(lines) > offset + effective_limit
        else:
            sliced = lines[offset:]
            truncated = False

        duration_ms = int((time.perf_counter() - start) * 1000)

        return GrepResult(
            mode="files_with_matches",
            filenames=sliced,
            num_files=len(sliced),
            truncated=truncated,
            duration_ms=duration_ms,
            applied_limit=effective_limit if truncated else None,
            applied_offset=offset if offset > 0 else None,
        )


ripgrep_service = RipgrepService()
