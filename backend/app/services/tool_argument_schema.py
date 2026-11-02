"""One schema contract for model instructions and execution diagnostics."""
from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


def argument_error(error: ValidationError) -> str:
    path = "$"
    for part in error.absolute_path:
        path += f"[{part}]" if isinstance(part, int) else "." + str(part)
    if error.validator == "type":
        expected = error.validator_value
        actual = ("string" if isinstance(error.instance, str) else
                  "object" if isinstance(error.instance, dict) else
                  "array" if isinstance(error.instance, list) else type(error.instance).__name__)
        hint = ""
        if isinstance(error.instance, str) and expected in ("object", "array"):
            noun = "对象" if expected == "object" else "数组"
            hint = f"；直接传 JSON {noun}，不要将其序列化成带引号的字符串"
        return f"{path}: expected {expected}, received {actual}{hint}"
    if error.validator == "required":
        missing = [name for name in error.validator_value if name not in error.instance]
        return f"{path}: missing required fields: {', '.join(missing)}"
    if error.validator == "additionalProperties":
        unexpected = sorted(set(error.instance) - set(error.schema.get("properties", {})))
        return f"{path}: unexpected fields: {', '.join(unexpected)}"
    if error.validator in {"anyOf", "oneOf", "allOf"}:
        # The most specific field error is more useful than dumping whole schemas.
        detail = max(error.context, key=lambda item: len(item.absolute_path), default=None)
        return argument_error(detail) if detail is not None else f"{path}: does not satisfy {error.validator}"
    # Do not echo entire invalid scripts/logs/JSON strings into the next prompt.
    return f"{path}: constraint {error.validator}={error.validator_value!r} is not satisfied"


def validate_arguments(schema: dict[str, Any], arguments: Any) -> str | None:
    error = next(Draft202012Validator(schema).iter_errors(arguments), None)
    return argument_error(error) if error is not None else None
