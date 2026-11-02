"""Read complete retained tool receipts without relying on rendered history."""
from __future__ import annotations
import json
import re
from typing import Any


def read_tool_output(receipt: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    path = str(arguments.get("path") or "")
    value: Any = receipt
    if not path:
        payload = receipt.get("data") or {}
        if isinstance(payload.get("public_output"), str):
            value = payload["public_output"]
            path = "/data/public_output"
    else:
        if not path.startswith("/"):
            raise ValueError("path 必须是 JSON Pointer，例如 /data/public_output。")
        for part in path.lstrip("/").split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    start = max(0, int(arguments.get("offset", 0)))
    if start > len(text):
        raise ValueError("offset 超过完整结果长度。")
    limit = max(1, min(12000, int(arguments.get("limit", 6000))))
    query = str(arguments.get("query") or "")
    if query:
        match = re.search(query if arguments.get("regex") else re.escape(query), text[start:])
        if not match:
            return {"path": path, "content": "", "offset": len(text), "next_offset": len(text),
                    "total_chars": len(text), "has_more": False, "matched": False}
        start += match.start()
    end = min(len(text), start + limit)
    return {"path": path, "content": text[start:end], "offset": start, "next_offset": end,
            "total_chars": len(text), "has_more": end < len(text), "matched": True if query else None}
