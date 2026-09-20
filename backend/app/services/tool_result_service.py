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
    """Render compact, CLI-style text for model consumption.

    The renderer is intentionally independent of tool execution. Tools own
    structured data; this class owns the stable model-facing text protocol.
    """

    max_output_chars = 16_000
    max_scalar_chars = 4_000

    _sensitive_keys = {"api_key", "authorization", "password", "refresh_token", "secret", "token"}

    _scalar_fields: dict[str, tuple[str, ...]] = {
        "artifact.created": ("artifact_id", "name", "media_type", "size_bytes"),
        "elicitation.requested": ("request_id", "blocking", "title"),
        "file.read": ("file_path", "start_line", "end_line", "total_lines", "truncated"),
        "runtime.rebuilt": ("status", "reason", "generation", "previous_generation"),
        "search.glob": ("pattern", "num_files", "truncated", "duration_ms"),
        "search.grep": ("mode", "num_lines", "num_matches", "num_files", "truncated", "duration_ms"),
        "shell.executed": ("shell", "executor", "exit_code", "duration_ms", "log_path"),
        "skill.loaded": ("skill_name", "source"),
        "skills.listed": ("count",),
        "subagent.cancel_requested": ("subagent_run_id", "status"),
        "subagent.created": ("subagent_run_id", "child_session_id", "name", "status", "task"),
        "subagent.message_sent": ("subagent_run_id", "child_session_id", "name", "status"),
        "subagent.result": ("subagent_run_id", "status"),
        "web.fetch": ("url", "format", "content_type", "truncated"),
        "web.searched": ("query", "provider", "result_count"),
        "workspace.listed": ("path", "item_count", "truncated"),
        "workboard.updated": ("revision", "status", "item_count", "open_item_count"),
    }

    _content_fields: dict[str, tuple[str, ...]] = {
        "file.read": ("content",),
        "search.grep": ("content",),
        "shell.executed": ("stdout", "stderr"),
        "web.searched": ("answer",),
        "web.fetch": ("content",),
    }

    def render(self, result: ToolExecutionResult) -> str:
        lines = [f"{result.kind}: {result.status}", f"summary: {result.summary}"]

        if result.error is not None:
            lines.extend(
                [
                    f"code: {result.error.code}",
                    f"message: {result.error.message}",
                    f"retryable: {'yes' if result.error.retryable else 'no'}",
                ]
            )
            if result.error.next_action:
                lines.append(f"next: {result.error.next_action}")

        lines.extend(self._render_specialized(result))
        lines.extend(self._render_remaining_fields(result))
        rendered = "\n".join(line for line in lines if line.strip())
        return self._bound_output(rendered)

    def _render_specialized(self, result: ToolExecutionResult) -> list[str]:
        if result.kind == "subagent.listed":
            return self._render_subagents(result.data.get("subagents"))
        if result.kind == "subagent.waited":
            return [
                f"status: {result.data.get('status', 'unknown')}",
                *self._render_subagents(result.data.get("subagents")),
            ]
        if result.kind == "web.searched":
            return self._render_web_results(result.data.get("results"))
        if result.kind == "workspace.listed":
            return self._render_directory_items(result.data.get("items"))
        if result.kind == "skills.listed":
            return self._render_skill_items(result.data.get("skills"))
        return []

    def _render_remaining_fields(self, result: ToolExecutionResult) -> list[str]:
        content_fields = self._content_fields.get(result.kind, ())
        scalar_fields = self._scalar_fields.get(result.kind, ())
        handled = set(content_fields) | set(scalar_fields) | {
            "subagents",
            "status",
            "results",
            "items",
            "skills",
        }

        lines: list[str] = []
        for key in scalar_fields:
            if key in result.data:
                lines.append(f"{key}: {self._format_scalar(result.data[key])}")

        for key in content_fields:
            value = result.data.get(key)
            if isinstance(value, str) and value.strip():
                lines.append(f"{key}:")
                lines.extend(self._clip_block(value.rstrip("\n")))

        for key, value in result.data.items():
            if key in handled or value is None or value == "" or value == []:
                continue
            lines.extend(self._render_value(key, value, indent=0))
        return lines

    def _render_subagents(self, value: Any) -> list[str]:
        rows = value if isinstance(value, list) else []
        if not rows:
            return ["subagents: none"]
        lines = [f"subagents: {len(rows)}"]
        for row in rows[:30]:
            if not isinstance(row, dict):
                continue
            run_id = str(row.get("subagent_run_id") or row.get("child_run_id") or "-")
            status = str(row.get("status") or "-")
            name = str(row.get("name") or "-")
            action = row.get("current_action")
            action_label = str(action.get("label") or "") if isinstance(action, dict) else ""
            suffix = f"  {action_label}" if action_label else ""
            lines.append(self._clip_line(f"{run_id}  {status:<12}  {name}{suffix}", 240))
        if len(rows) > 30:
            lines.append(f"truncated: {len(rows) - 30} more subagents")
        return lines

    def _render_web_results(self, value: Any) -> list[str]:
        rows = value if isinstance(value, list) else []
        if not rows:
            return ["results: none"]
        lines = [f"results: {len(rows)}"]
        for row in rows[:20]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or row.get("url") or "-")
            url = str(row.get("url") or "")
            lines.append(self._clip_line(f"- {title}" + (f"  {url}" if url else ""), 240))
        if len(rows) > 20:
            lines.append(f"truncated: {len(rows) - 20} more results")
        return lines

    def _render_directory_items(self, value: Any) -> list[str]:
        rows = value if isinstance(value, list) else []
        if not rows:
            return ["items: none"]
        lines = [f"items: {len(rows)}"]
        for row in rows[:100]:
            if not isinstance(row, dict):
                continue
            entry_type = str(row.get("type") or "-")
            name = str(row.get("name") or "-")
            size = row.get("size")
            suffix = f"  {size} bytes" if isinstance(size, int) else ""
            lines.append(self._clip_line(f"{entry_type:<8}  {name}{suffix}", 240))
        if len(rows) > 100:
            lines.append(f"truncated: {len(rows) - 100} more items")
        return lines

    def _render_skill_items(self, value: Any) -> list[str]:
        rows = value if isinstance(value, list) else []
        if not rows:
            return ["skills: none"]
        lines = [f"skills: {len(rows)}"]
        for row in rows[:50]:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "-")
            description = str(row.get("description") or "")
            lines.append(self._clip_line(f"- {name}" + (f"  {description}" if description else ""), 240))
        if len(rows) > 50:
            lines.append(f"truncated: {len(rows) - 50} more skills")
        return lines

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
