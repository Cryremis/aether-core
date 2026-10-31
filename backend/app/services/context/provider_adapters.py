"""原生协议转换。保持供应商响应 item/块完整，不从展示摘要重建推理状态。"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


def binding(config) -> str:
    value = json.dumps([config.api_format, config.base_url.rstrip("/"), config.model,
                        hashlib.sha256(config.api_key.encode()).hexdigest(), config.extra_headers, config.extra_body,
                        {key: (config.sampling or {}).get(key) for key in ("model_profile", "reasoning_replay", "reasoning_field", "deployment_revision", "deployment_profile")}],
                       sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()


def history_system_rules(messages: list[dict]) -> list[dict]:
    rules = []
    for message in messages:
        if message.get("role") == "system" and not message.get("ephemeral") and not message.get("is_boundary_marker"):
            rules.append({"role": "system", "content": copy.deepcopy(message.get("content", ""))})
        elif (message.get("provider_payload") or {}).get("complete_window"):
            rules.extend(copy.deepcopy(message["provider_payload"].get("system_rules", [])))
    return rules


def native_request(config, messages: list[dict], chat: dict) -> dict:
    protocol = config.api_format
    if protocol == "openai-compatible":
        return chat
    unsupported = {"frequency_penalty", "presence_penalty", "repetition_penalty", "tool_stream"}.intersection(chat)
    unsupported.update({"seed", "stop"}.intersection(chat) if protocol == "openai-responses" else {"seed"}.intersection(chat))
    common = {key: value for key, value in chat.items()
              if key not in {"messages", "tools", "tool_choice", "max_tokens", "frequency_penalty", "presence_penalty", "repetition_penalty", "reasoning_effort"} | unsupported}
    converted = []
    system = []
    for message in messages:
        role = message["role"]
        saved = message.get("provider_payload")
        if saved:
            if saved.get("binding") != binding(config):
                raise ValueError("模型、协议或账户已变化，原生思考状态需要建立新上下文阶段")
            if saved.get("complete_window"):
                if protocol != "openai-responses":
                    raise ValueError("完整原生窗口仅适用于 Responses 协议")
                # /compact 返回完整下一请求窗口，不能在前面重复拼接旧系统前缀。
                converted = []
            converted.extend(copy.deepcopy(saved["items"]))
            continue
        content = message.get("content") or ""
        if not isinstance(content, str):
            # 多模态块的字段随协议而异，不能把 Chat 块直接塞进原生 text 字段。
            raise ValueError("原生协议当前只接受规范化文本消息；请通过文件工具读取附件")
        if protocol == "openai-responses":
            if role == "tool":
                converted.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": content})
                continue
            if content:
                converted.append({"role": role, "content": content})
            for call in message.get("tool_calls", []):
                converted.append({"type": "function_call", "call_id": call["id"],
                                  "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
        else:
            if role in {"system", "developer"}:
                system.append(str(content))
                continue
            if role == "tool":
                converted.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": content}]})
                continue
            blocks = [{"type": "text", "text": content}] if content else []
            for call in message.get("tool_calls", []):
                blocks.append({"type": "tool_use", "id": call["id"], "name": call["function"]["name"],
                               "input": json.loads(call["function"]["arguments"])})
            if blocks:
                converted.append({"role": role, "content": blocks})
    if protocol == "openai-responses":
        common.update(input=converted, max_output_tokens=chat["max_tokens"], store=False)
        from app.services.context.model_profile import resolve_model_profile
        if resolve_model_profile(config).profile_id != "deepseek-thinking/v1":
            common.setdefault("include", ["reasoning.encrypted_content"])
        if chat.get("reasoning_effort") is not None:
            common["reasoning"] = {**common.get("reasoning", {}), "effort": chat["reasoning_effort"]}
        if chat.get("tools"):
            common["tools"] = [{"type": "function", **tool["function"]} for tool in chat["tools"]]
            common["tool_choice"] = "auto"
    elif protocol == "anthropic-messages":
        if "stop" in common:
            stop = common.pop("stop")
            common["stop_sequences"] = [stop] if isinstance(stop, str) else stop
        parallel = common.pop("parallel_tool_calls", None)
        common.update(messages=converted, max_tokens=chat["max_tokens"])
        if system:
            common["system"] = "\n\n".join(system)
        if chat.get("tools"):
            common["tools"] = [{"name": tool["function"]["name"], "description": tool["function"].get("description", ""),
                                "input_schema": tool["function"].get("parameters", {"type": "object"})} for tool in chat["tools"]]
            if parallel is not None:
                common["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": not parallel}
        if chat.get("reasoning_effort") is not None:
            common["output_config"] = {**common.get("output_config", {}), "effort": chat["reasoning_effort"]}
    else:
        raise ValueError(f"不支持模型协议: {protocol}")
    return common


def native_message(config, response: dict) -> dict:
    if config.api_format == "openai-responses":
        items = response.get("output", [])
        texts = [part.get("text", "") for item in items if item.get("type") == "message"
                 for part in item.get("content", []) if part.get("type") == "output_text"]
        calls = [{"id": item["call_id"], "type": "function", "function": {"name": item["name"], "arguments": item["arguments"]}}
                 for item in items if item.get("type") == "function_call"]
        saved_items = items
    else:
        items = response.get("content", [])
        texts = [part.get("text", "") for part in items if part.get("type") == "text"]
        calls = [{"id": item["id"], "type": "function", "function": {"name": item["name"], "arguments": json.dumps(item.get("input", {}), ensure_ascii=False)}}
                 for item in items if item.get("type") == "tool_use"]
        saved_items = [{"role": "assistant", "content": items}]
    result = {"role": "assistant", "content": "".join(texts),
              "provider_payload": {"binding": binding(config), "items": copy.deepcopy(saved_items)}}
    if calls:
        result["tool_calls"] = calls
    return result


class NativeStream:
    def __init__(self, config):
        self.config = config
        self.blocks: dict[int, dict] = {}
        self.inputs: dict[int, str] = {}
        self.items: dict[int, dict] = {}
        self.text_parts: dict[tuple[str, int], str] = {}
        self.reasoning_parts: dict[tuple[str, str, int], str] = {}
        self.closed_blocks: set[int] = set()
        self.calls: set[str] = set()
        self.usage: dict = {}
        self.stop_reason = "stop"

    def _chunk(self, delta: dict | None = None, finish: str | None = None, **extra) -> dict:
        return {"choices": [{"delta": delta or {}, "finish_reason": finish}], **extra}

    def _complete_response_item(self, index: int, item: dict) -> list[dict]:
        if index in self.items:
            if self.items[index] != item:
                raise RuntimeError("原生响应的已完成 item 发生变化")
            return []
        self.items[index] = copy.deepcopy(item)
        output = []
        if item.get("type") == "function_call":
            if item["call_id"] in self.calls:
                raise RuntimeError("原生响应重复调用标识")
            self.calls.add(item["call_id"])
            output.append(self._chunk({"tool_calls": [{"index": index, "id": item["call_id"],
                "function": {"name": item["name"], "arguments": item["arguments"]}}]}))
        elif item.get("type") == "message":
            for content_index, part in enumerate(item.get("content", [])):
                if part.get("type") not in {"output_text", "refusal"}:
                    continue
                text = part.get("text", part.get("refusal", ""))
                key = (str(item.get("id", index)), content_index)
                if key not in self.text_parts:
                    output.append(self._chunk({"content": text}))
                elif self.text_parts[key] != text:
                    raise RuntimeError("原生响应正文与已发布的流片段不一致")
                if part.get("type") == "refusal":
                    self.stop_reason = "refusal"
        elif item.get("type") == "reasoning":
            for field in ("content", "summary"):
                parts = item.get(field) or []
                if isinstance(parts, str):
                    parts = [{"text": parts}]
                for content_index, part in enumerate(parts):
                    text = part.get("text", "")
                    key = (str(item.get("id", index)), field, content_index)
                    if key not in self.reasoning_parts:
                        output.append(self._chunk({"reasoning_content": text}))
                    elif self.reasoning_parts[key] != text:
                        raise RuntimeError("原生思考与已发布的流片段不一致")
        return output

    def decode(self, frame: dict) -> list[dict[str, Any]]:
        kind = frame.get("type", "")
        output = []
        if kind in {"error", "response.failed"}:
            raise RuntimeError(f"模型原生流失败: {frame.get('error') or frame.get('response', {}).get('error')}")
        if self.config.api_format == "openai-responses":
            if kind == "response.output_text.delta":
                key = (str(frame.get("item_id", frame.get("output_index", 0))), int(frame.get("content_index", 0)))
                self.text_parts[key] = self.text_parts.get(key, "") + frame["delta"]
                output.append(self._chunk({"content": frame["delta"]}))
            elif kind in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
                field = "summary" if kind == "response.reasoning_summary_text.delta" else "content"
                key = (str(frame.get("item_id", frame.get("output_index", 0))), field, int(frame.get("summary_index" if field == "summary" else "content_index", 0)))
                self.reasoning_parts[key] = self.reasoning_parts.get(key, "") + frame["delta"]
                output.append(self._chunk({"reasoning_content": frame["delta"]}))
            elif kind == "response.output_item.done":
                output.extend(self._complete_response_item(int(frame.get("output_index", len(self.items))), frame["item"]))
            elif kind in {"response.completed", "response.incomplete"}:
                response = frame.get("response", {})
                items = response["output"] if "output" in response else [self.items[index] for index in sorted(self.items)]
                if not isinstance(items, list) or any(index >= len(items) for index in self.items):
                    raise RuntimeError("原生终态遗漏已完成的 output item")
                for index, item in enumerate(items):
                    output.extend(self._complete_response_item(index, item))
                self.usage = response.get("usage") or {}
                reason = ("length" if response.get("incomplete_details", {}).get("reason") == "max_output_tokens" else "content_filter") if kind == "response.incomplete" else ("tool_calls" if self.calls else "stop")
                if self.stop_reason == "refusal":
                    reason = "refusal"
                output.append(self._chunk(finish=reason, usage=self.usage,
                    provider_payload={"binding": binding(self.config), "items": copy.deepcopy(items)}))
        else:
            index = int(frame.get("index", 0))
            if kind == "message_start":
                self.usage = frame.get("message", {}).get("usage") or {}
            elif kind == "content_block_start":
                if index in self.blocks:
                    raise RuntimeError("原生响应重复内容块")
                self.blocks[index] = copy.deepcopy(frame["content_block"])
                block = self.blocks[index]
                if block.get("type") == "text" and block.get("text"):
                    output.append(self._chunk({"content": block["text"]}))
                elif block.get("type") == "thinking" and block.get("thinking"):
                    output.append(self._chunk({"reasoning_content": block["thinking"]}))
            elif kind == "content_block_delta":
                if index in self.closed_blocks:
                    raise RuntimeError("原生响应在内容块结束后继续发送增量")
                delta = frame["delta"]
                block = self.blocks[index]
                if delta["type"] == "text_delta":
                    block["text"] = block.get("text", "") + delta["text"]
                    output.append(self._chunk({"content": delta["text"]}))
                elif delta["type"] == "thinking_delta":
                    block["thinking"] = block.get("thinking", "") + delta["thinking"]
                    output.append(self._chunk({"reasoning_content": delta["thinking"]}))
                elif delta["type"] == "signature_delta":
                    block["signature"] = block.get("signature", "") + delta["signature"]
                elif delta["type"] == "input_json_delta":
                    self.inputs[index] = self.inputs.get(index, "") + delta["partial_json"]
                elif delta["type"] == "citations_delta":
                    block.setdefault("citations", []).append(copy.deepcopy(delta["citation"]))
                else:
                    raise RuntimeError(f"尚未支持的原生流增量: {delta['type']}")
            elif kind == "content_block_stop":
                block = self.blocks[index]
                if index in self.closed_blocks:
                    raise RuntimeError("原生响应重复结束内容块")
                self.closed_blocks.add(index)
                if block["type"] == "tool_use":
                    raw = self.inputs.get(index, json.dumps(block.get("input", {})))
                    block["input"] = json.loads(raw)
                    output.append(self._chunk({"tool_calls": [{"index": index, "id": block["id"],
                        "function": {"name": block["name"], "arguments": raw}}]}))
            elif kind == "message_delta":
                raw_reason = frame.get("delta", {}).get("stop_reason")
                self.stop_reason = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length"}.get(raw_reason, raw_reason or self.stop_reason)
                self.usage.update(frame.get("usage") or {})
            elif kind == "message_stop":
                if set(self.blocks) != self.closed_blocks:
                    raise RuntimeError("原生响应包含未结束的内容块")
                content = [self.blocks[i] for i in sorted(self.blocks)]
                output.append(self._chunk(finish=self.stop_reason, usage=self.usage,
                    provider_payload={"binding": binding(self.config), "items": [{"role": "assistant", "content": content}]}))
        return output
