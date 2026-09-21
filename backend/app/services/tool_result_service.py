from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal


ToolResultStatus = Literal["success", "partial", "error", "aborted"]


@dataclass(frozen=True)
class ToolExecutionError:
    """Tool errors that the model can reason about without a stack trace."""

    code: str
    message: str
    retryable: bool = False
    next_action: str = ""

    def public_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class ToolExecutionResult:
    """The single boundary contract between tools and the agent runtime."""

    kind: str
    summary: str
    status: ToolResultStatus = "success"
    data: dict[str, Any] = field(default_factory=dict)
    error: ToolExecutionError | None = None
    runtime_events: tuple[dict[str, Any], ...] = ()
    injected_messages: tuple[dict[str, Any], ...] = ()
    control: dict[str, Any] | None = None
    artifact: dict[str, Any] | None = None

    @classmethod
    def success(
        cls,
        kind: str,
        summary: str,
        data: Mapping[str, Any] | None = None,
        *,
        runtime_events: Sequence[dict[str, Any]] = (),
        injected_messages: Sequence[dict[str, Any]] = (),
        control: Mapping[str, Any] | None = None,
        artifact: Mapping[str, Any] | None = None,
    ) -> ToolExecutionResult:
        return cls(
            kind=kind,
            summary=summary,
            status="success",
            data=dict(data or {}),
            runtime_events=tuple(runtime_events),
            injected_messages=tuple(injected_messages),
            control=dict(control) if control is not None else None,
            artifact=dict(artifact) if artifact is not None else None,
        )

    @classmethod
    def partial(
        cls,
        kind: str,
        summary: str,
        data: Mapping[str, Any] | None = None,
    ) -> ToolExecutionResult:
        return cls(kind=kind, summary=summary, status="partial", data=dict(data or {}))

    @classmethod
    def failure(
        cls,
        kind: str,
        summary: str,
        *,
        code: str,
        message: str,
        retryable: bool = False,
        next_action: str = "",
        data: Mapping[str, Any] | None = None,
    ) -> ToolExecutionResult:
        return cls(
            kind=kind,
            summary=summary,
            status="error",
            data=dict(data or {}),
            error=ToolExecutionError(
                code=code,
                message=message,
                retryable=retryable,
                next_action=next_action,
            ),
        )

    @classmethod
    def aborted(cls, summary: str = "工具执行已停止") -> ToolExecutionResult:
        return cls(
            kind="tool.execution",
            summary=summary,
            status="aborted",
            error=ToolExecutionError(
                code="TOOL_EXECUTION_ABORTED",
                message=summary,
                retryable=False,
                next_action="用户已停止本次执行；如需继续，请等待用户重新发起。",
            ),
        )

    def public_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "status": self.status,
            "summary": self.summary,
            "data": copy.deepcopy(self.data),
        }
        if self.error is not None:
            payload["error"] = self.error.public_payload()
        return payload


class ToolResultRenderer:
    """Render the minimum useful text for model consumption.

    Structured results remain the contract for UI, persistence, and auditing.
    This projection omits protocol decorations and arguments the model already
    supplied in its tool call.
    """

    max_output_chars = 16_000
    max_scalar_chars = 4_000

    _sensitive_keys = {"api_key", "authorization", "password", "refresh_token", "secret", "token"}

    # Dynamic host and MCP payloads can include timing; it has no model value.
    _ignored_fields = {"duration_ms"}

    def render(self, result: ToolExecutionResult) -> str:
        if result.error is not None:
            return self._bound_output("\n".join(self._render_error(result.error)))

        lines = self._render_success(result)
        return self._bound_output("\n".join(line for line in lines if line.strip()))

    def _render_error(self, error: ToolExecutionError) -> list[str]:
        lines = [f"error: {error.message}"]
        if error.retryable:
            lines.append("retryable: yes")
        if error.next_action:
            lines.append(f"next: {error.next_action}")
        return lines

    def _render_success(self, result: ToolExecutionResult) -> list[str]:
        match result.kind:
            case "artifact.created":
                return self._render_artifact(result.data.get("artifact"))
            case "file.read":
                return self._render_text_block(
                    result.data.get("content"),
                    empty_message="(empty file)",
                    truncation_note=self._file_truncation_note(result.data),
                )
            case "search.glob":
                return self._render_string_list(result.data.get("files"), "No files found")
            case "search.grep":
                return self._render_text_block(result.data.get("content"), "No matches found")
            case "shell.executed":
                return self._render_shell(result.data)
            case "skills.listed":
                return self._render_skill_items(result.data.get("skills"))
            case "skill.loaded":
                return self._render_skill(result.data)
            case "subagent.created":
                return self._render_optional_value("subagent_id", result.data.get("subagent_id"))
            case "subagent.listed":
                return self._render_subagents(result.data.get("subagents"))
            case "subagent.waited":
                return [
                    *self._render_optional_value("status", result.data.get("status")),
                    *self._render_subagents(result.data.get("subagents")),
                ]
            case "subagent.message_sent":
                return self._render_optional_value("status", result.data.get("status"))
            case "subagent.cancelled":
                return [
                    *self._render_optional_value("status", result.data.get("status")),
                    *self._render_optional_value("cancel_outcome", result.data.get("cancel_outcome")),
                ]
            case "subagent.result":
                return self._render_subagent_result(result.data)
            case "web.fetch" | "web.fetched":
                return self._render_text_block(result.data.get("content"), "(empty response)")
            case "web.searched":
                return self._render_web_search(result.data)
            case "workspace.listed":
                return self._render_directory_items(result.data.get("items"))
            case "workboard.updated":
                return [
                    *self._render_optional_value("revision", result.data.get("revision")),
                    *self._render_optional_value("open_items", result.data.get("open_item_count")),
                ]
            case "runtime.rebuilt":
                return [
                    *self._render_optional_value("runtime", result.data.get("status")),
                    *self._render_optional_value("generation", result.data.get("generation")),
                ]
            case "elicitation.requested":
                return self._render_optional_value("request_id", result.data.get("request_id"))
            case _:
                return self._render_payload(result.data)

    def _render_payload(self, data: Mapping[str, Any]) -> list[str]:
        lines: list[str] = []
        for key, value in data.items():
            if key in self._ignored_fields or value is None or value == "" or value == []:
                continue
            lines.extend(self._render_value(key, value, indent=0))
        return lines

    def _render_artifact(self, value: Any) -> list[str]:
        if not isinstance(value, Mapping):
            return []
        fields = ("file_id", "relative_path", "size")
        return [
            line
            for key in fields
            for line in self._render_optional_value(key, value.get(key))
        ]

    def _render_skill(self, data: Mapping[str, Any]) -> list[str]:
        lines = self._render_optional_value("description", data.get("description"))
        allowed_tools = data.get("allowed_tools")
        if isinstance(allowed_tools, list) and allowed_tools:
            lines.append("allowed_tools:")
            lines.extend(f"  {self._clip_line(str(item), 240)}" for item in allowed_tools[:30])
            if len(allowed_tools) > 30:
                lines.append(f"  [truncated: {len(allowed_tools) - 30} more tools]")
        return lines

    def _render_text_block(
        self,
        value: Any,
        empty_message: str,
        truncation_note: str = "",
    ) -> list[str]:
        if not isinstance(value, str) or not value.strip():
            return [empty_message]
        lines = self._clip_block(value.rstrip("\n"))
        if truncation_note:
            lines.append(truncation_note)
        return lines

    def _file_truncation_note(self, data: Mapping[str, Any]) -> str:
        if not bool(data.get("truncated")):
            return ""
        start_line = data.get("start_line")
        end_line = data.get("end_line")
        total_lines = data.get("total_lines")
        if all(isinstance(item, int) for item in (start_line, end_line, total_lines)):
            return f"[truncated: lines {start_line}-{end_line} of {total_lines}]"
        return "[truncated]"

    def _render_string_list(self, value: Any, empty_message: str) -> list[str]:
        rows = value if isinstance(value, list) else []
        if not rows:
            return [empty_message]
        return [self._clip_line(str(item), 240) for item in rows]

    def _render_shell(self, data: Mapping[str, Any]) -> list[str]:
        lines: list[str] = []
        stdout = data.get("stdout")
        stderr = data.get("stderr")
        if isinstance(stdout, str) and stdout.strip():
            lines.extend(self._clip_block(stdout.rstrip("\n")))
        if isinstance(stderr, str) and stderr.strip():
            lines.append("stderr:")
            lines.extend(f"  {line}" for line in self._clip_block(stderr.rstrip("\n")))
        exit_code = data.get("exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            lines.append(f"exit_code: {exit_code}")
        if not lines:
            return ["(no output)"]
        return lines

    def _render_optional_value(self, label: str, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        return [f"{label}: {self._format_scalar(value)}"]

    def _render_subagent_result(self, data: Mapping[str, Any]) -> list[str]:
        lines = self._render_optional_value("status", data.get("status"))
        result = data.get("result")
        if isinstance(result, str) and result.strip():
            lines.extend(self._clip_block(result.rstrip("\n")))
        elif isinstance(result, Mapping):
            lines.extend(self._render_payload(result))
        elif result is not None:
            lines.extend(self._render_value("result", result, indent=0))
        error = data.get("error")
        if isinstance(error, str) and error.strip():
            lines.append(f"error: {self._clip_line(error, self.max_scalar_chars)}")
        return lines

    def _render_web_search(self, data: Mapping[str, Any]) -> list[str]:
        answer = data.get("answer")
        answer_lines = (
            self._render_text_block(answer, "(no search answer)")
            if isinstance(answer, str) and answer.strip()
            else []
        )
        return [
            *answer_lines,
            *self._render_web_results(data.get("results")),
        ]

    def _render_subagents(self, value: Any) -> list[str]:
        rows = value if isinstance(value, list) else []
        if not rows:
            return ["No subagents"]
        lines: list[str] = []
        for row in rows[:30]:
            if not isinstance(row, dict):
                continue
            subagent_id = str(row.get("subagent_id") or "-")
            status = str(row.get("status") or "-")
            name = str(row.get("name") or "-")
            action = row.get("current_action")
            action_label = str(action.get("label") or "") if isinstance(action, dict) else ""
            suffix = f"  {action_label}" if action_label else ""
            lines.append(self._clip_line(f"{subagent_id}  {status:<12}  {name}{suffix}", 240))
        if len(rows) > 30:
            lines.append(f"[truncated: {len(rows) - 30} more subagents]")
        return lines

    def _render_web_results(self, value: Any) -> list[str]:
        rows = value if isinstance(value, list) else []
        if not rows:
            return ["No results found"]
        lines: list[str] = []
        for row in rows[:20]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or row.get("url") or "-")
            url = str(row.get("url") or "")
            snippet = str(row.get("snippet") or row.get("description") or "")
            line = f"- {title}" + (f"  {url}" if url else "")
            if snippet:
                line += f"  {snippet}"
            lines.append(self._clip_line(line, 240))
        if len(rows) > 20:
            lines.append(f"[truncated: {len(rows) - 20} more results]")
        return lines

    def _render_directory_items(self, value: Any) -> list[str]:
        rows = value if isinstance(value, list) else []
        if not rows:
            return ["(empty directory)"]
        lines: list[str] = []
        for row in rows[:100]:
            if not isinstance(row, dict):
                continue
            entry_type = str(row.get("type") or "-")
            name = str(row.get("name") or "-")
            size = row.get("size")
            prefix = "dir" if entry_type == "dir" else "file"
            suffix = "  " + self._format_size(size) if isinstance(size, int) else ""
            lines.append(self._clip_line(f"{prefix} {name}{suffix}", 240))
        if len(rows) > 100:
            lines.append(f"[truncated: {len(rows) - 100} more items]")
        return lines

    def _render_skill_items(self, value: Any) -> list[str]:
        rows = value if isinstance(value, list) else []
        if not rows:
            return ["No skills found"]
        lines: list[str] = []
        for row in rows[:50]:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "-")
            description = str(row.get("description") or "")
            lines.append(self._clip_line(f"- {name}" + (f"  {description}" if description else ""), 240))
        if len(rows) > 50:
            lines.append(f"[truncated: {len(rows) - 50} more skills]")
        return lines

    def _format_size(self, value: int) -> str:
        if value < 1024:
            return f"{value}B"
        for unit in ("KB", "MB", "GB"):
            value /= 1024
            if value < 1024:
                return f"{value:.1f}{unit}"
        return f"{value:.1f}TB"

    def _render_value(self, key: str, value: Any, *, indent: int) -> list[str]:
        prefix = "  " * indent
        if key.lower() in self._sensitive_keys:
            return [f"{prefix}{key}: [redacted]"]
        if isinstance(value, Mapping):
            lines = [f"{prefix}{key}:"]
            for child_key, child_value in value.items():
                lines.extend(self._render_value(str(child_key), child_value, indent=indent + 1))
            return lines
        if isinstance(value, list):
            if not value:
                return [f"{prefix}{key}: []"]
            lines = [f"{prefix}{key}:"]
            for item in value[:20]:
                if isinstance(item, Mapping):
                    lines.append(f"{prefix}  -")
                    for child_key, child_value in item.items():
                        lines.extend(self._render_value(str(child_key), child_value, indent=indent + 2))
                else:
                    lines.append(f"{prefix}  - {self._format_scalar(item)}")
            if len(value) > 20:
                lines.append(f"{prefix}  truncated: {len(value) - 20} more")
            return lines
        return [f"{prefix}{key}: {self._format_scalar(value)}"]

    def _format_scalar(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "yes" if value else "no"
        text = str(value)
        return self._clip_line(text, self.max_scalar_chars)

    def _clip_block(self, value: str) -> list[str]:
        if len(value) <= self.max_scalar_chars:
            return value.splitlines() or [""]
        keep = self.max_scalar_chars // 2
        omitted = len(value) - keep * 2
        head = value[:keep].splitlines()
        tail = value[-keep:].splitlines()
        return [*head, f"[truncated: {omitted} characters omitted]", *tail]

    def _clip_line(self, value: str, max_length: int) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n")
        if "\n" in normalized:
            normalized = " ".join(normalized.split())
        if len(normalized) <= max_length:
            return normalized
        return f"{normalized[: max_length - 1]}…"

    def _bound_output(self, value: str) -> str:
        if len(value) <= self.max_output_chars:
            return value
        keep = self.max_output_chars // 2
        omitted = len(value) - keep * 2
        return f"{value[:keep]}\n[output truncated: {omitted} characters omitted]\n{value[-keep:]}"


tool_result_renderer = ToolResultRenderer()
