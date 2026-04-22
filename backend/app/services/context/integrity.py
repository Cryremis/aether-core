# backend/app/services/context/integrity.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class IntegrityReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ContextIntegrityValidator:
    """Validate provider-facing message invariants after compaction."""

    def validate(self, messages: list[dict[str, Any]]) -> IntegrityReport:
        errors: list[str] = []
        warnings: list[str] = []
        seen_tool_calls: set[str] = set()
        seen_message_ids: set[str] = set()

        for index, message in enumerate(messages):
            message_id = str(message.get("message_id") or "")
            if message_id:
                if message_id in seen_message_ids:
                    errors.append(f"duplicate message_id at index {index}: {message_id}")
                seen_message_ids.add(message_id)

            role = message.get("role")
            if role not in {"system", "user", "assistant", "tool"}:
                errors.append(f"invalid role at index {index}: {role}")

            if role == "assistant":
                for tool_call in message.get("tool_calls", []) or []:
                    tool_call_id = tool_call.get("id")
                    if tool_call_id:
                        seen_tool_calls.add(str(tool_call_id))

            if role == "tool":
                tool_call_id = message.get("tool_call_id")
                if not tool_call_id:
                    errors.append(f"tool result without tool_call_id at index {index}")
                elif str(tool_call_id) not in seen_tool_calls:
                    errors.append(f"orphan tool result at index {index}: {tool_call_id}")

        if messages and messages[-1].get("role") == "tool":
            warnings.append("message sequence ends with a tool result; next request must include a matching assistant continuation")

        return IntegrityReport(ok=not errors, errors=errors, warnings=warnings)


context_integrity_validator = ContextIntegrityValidator()

