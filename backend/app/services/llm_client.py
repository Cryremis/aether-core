# backend/app/services/llm_client.py
from __future__ import annotations

import json
import hashlib
import uuid
from typing import Any, AsyncGenerator

import httpx

from app.core.config import settings
from app.core.llm_protocol import RUNTIME_CONTROL_FIELDS
from app.core.model_errors import ModelStreamError
from app.services.llm_config_service import RuntimeLlmConfig
from app.services.context.provider_adapters import NativeStream, native_message, native_request


class LlmClient:
    """Chat Completions、Responses 与 Messages 协议客户端。"""

    def _resolve_effort(self, requested: str | int, supported: list[str]) -> str | int | None:
        """只发送明确支持的力度，不静默映射成另一种思考模式。"""
        if requested is None or requested == "":
            return None
        if requested in supported:
            return requested
        if not isinstance(requested, bool) and (isinstance(requested, int) or str(requested).isdecimal()) and 1 <= int(requested) <= 100:
            return int(requested)
        # 目录可能落后于实际服务；明确的自定义名称交给服务判断，不在本地中断任务。
        return requested if isinstance(requested, str) and requested.strip() else None

    def _endpoint(self, config: RuntimeLlmConfig) -> str:
        if not config.base_url:
            raise RuntimeError("未配置 LLM_BASE_URL，无法调用模型。")
        if not config.model:
            raise RuntimeError("未配置 LLM_MODEL，无法调用模型。")

        base = config.base_url.rstrip("/")
        if config.api_format != "openai-compatible":
            suffix = "/responses" if config.api_format == "openai-responses" else "/messages"
            return base if base.endswith(suffix) else base + suffix
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1") or base.endswith("/v3") or base.endswith("/v4"):
            return f"{base}/chat/completions"
        return f"{base}/chat/completions"

    def _headers(self, config: RuntimeLlmConfig) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        headers.update(config.extra_headers)
        if config.api_format == "anthropic-messages":
            headers.pop("Authorization", None)
            if config.api_key:
                headers.setdefault("x-api-key", config.api_key)
            headers.setdefault("anthropic-version", "2023-06-01")
        return headers

    def _payload(
        self,
        config: RuntimeLlmConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        stream: bool,
        reasoning_effort: str | None = None,
        check_budget: bool = True,
    ) -> dict[str, Any]:
        sampling = config.sampling or {}
        from app.services.context.model_profile import deployment_profile
        deployment = deployment_profile(config)
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "max_tokens": self.output_limit(config),
            "stream": stream,
        }
        for key in ("temperature", "frequency_penalty", "presence_penalty"):
            applicable_global = config.api_format == "openai-compatible" and config.model == settings.llm_model and config.base_url.rstrip("/") == settings.llm_base_url.rstrip("/")
            value = sampling.get(key, getattr(settings, f"llm_{key}") if applicable_global else None)
            if value is not None:
                payload[key] = value
        top_p = sampling.get("top_p")
        if top_p is not None:
            payload["top_p"] = top_p
        repetition_penalty = sampling.get("repetition_penalty")
        if repetition_penalty is not None:
            payload["repetition_penalty"] = repetition_penalty
        for key in ("stop", "seed", "parallel_tool_calls"):
            if sampling.get(key) is not None:
                payload[key] = sampling[key]
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        protected = RUNTIME_CONTROL_FIELDS
        if protected.intersection(config.extra_body):
            raise ValueError(f"扩展请求体不能覆盖运行时字段: {sorted(protected.intersection(config.extra_body))}")
        if "tools" in config.extra_body and config.extra_body["tools"] != []:
            raise ValueError("扩展请求体不能注入或覆盖工具目录")
        for key in ("temperature", "frequency_penalty", "presence_penalty", "top_p", "repetition_penalty", "reasoning_effort", "stop", "seed", "parallel_tool_calls"):
            if key in config.extra_body and (key in sampling.get("model_defaults", []) or
                    (sampling.get(key) is not None and sampling[key] != config.extra_body[key])):
                raise ValueError(f"extra_body.{key} 与生效采样参数冲突，请迁移到标准采样配置")
        payload.update({key: value for key, value in config.extra_body.items()
                        if key not in {"max_tokens", "max_completion_tokens", "max_output_tokens"}})
        payload["messages"] = [{key: value for key, value in message.items() if key != "provider_payload"}
                               for message in messages]
        if config.api_format == "openai-compatible":
            supported = deployment.get("supported_parameters")
            can_request_usage = supported is None or "stream_usage" in supported or "stream_options" in supported
            if not can_request_usage:
                payload.pop("stream_options", None)
            if stream and sampling.get("stream_usage") is not False and can_request_usage:
                stream_options = payload.setdefault("stream_options", {})
                if not isinstance(stream_options, dict):
                    raise ValueError("stream_options 必须是 JSON 对象")
                stream_options.setdefault("include_usage", True)
            from app.services.context.model_profile import project_reasoning_replay
            payload["messages"] = project_reasoning_replay(config, payload["messages"], payload.get("tools"))
            if "max_completion_tokens" in config.extra_body:
                payload["max_completion_tokens"] = payload.pop("max_tokens")
        # reasoning_effort: per-message 优先,其次 config 级,注入在 extra_body 之后
        container = payload.get("reasoning" if config.api_format == "openai-responses" else "output_config", {}) if config.api_format != "openai-compatible" else {}
        nested_effort = container.get("effort") if isinstance(container, dict) else None
        if sampling.get("reasoning_effort") is not None and nested_effort is not None and sampling["reasoning_effort"] != nested_effort:
            raise ValueError("额外请求体中的思考力度与标准配置冲突，请重新保存配置")
        effective_effort = reasoning_effort if reasoning_effort is not None else sampling["reasoning_effort"] if sampling.get("reasoning_effort") is not None else nested_effort
        if effective_effort is not None:
            from app.services.context.model_profile import resolve_model_profile
            profile = resolve_model_profile(config)
            resolved = self._resolve_effort(effective_effort, list(profile.effort_options) or config.reasoning_effort_options)
            if resolved:
                payload["reasoning_effort"] = resolved
        supported = deployment.get("supported_parameters")
        if supported is not None:
            parameters = {"temperature", "frequency_penalty", "presence_penalty", "top_p", "repetition_penalty", "reasoning_effort", "stop", "seed", "parallel_tool_calls"}
            requested = parameters.intersection(payload)
            for key in requested.difference(supported):
                payload.pop(key, None)  # 可选能力缺失时采用服务默认，不能变成 Agent 的故障。
        request = native_request(config, messages, self._strip_tool_fields_when_disabled(payload))
        window = self.context_window(config)
        if window and check_budget:
            input_tokens = self.estimate_payload_tokens(config, request)
            if input_tokens + self.output_limit(config) + 4000 > window:
                raise ValueError("最终模型请求超过部署上下文预算；请先压缩或调整输出预留")
        return request

    def estimate_payload_tokens(self, config, request: dict[str, Any]) -> int:
        """预算消费真正的协议投影，包含所有可进入提示的扩展结构。"""
        from app.services.context.token_estimation import TokenEstimator
        estimator = TokenEstimator()
        if config.api_format == "openai-compatible":
            tokens = estimator.estimate_messages_tokens(request["messages"])
        else:
            tokens = int(estimator.rough_token_count(json.dumps(request.get("input", request.get("messages", [])), ensure_ascii=False)) * estimator.config.padding_factor)
        tokens += estimator.estimate_tools_tokens(request.get("tools"))
        for key in ("system", "instructions", "response_format", "text"):
            if request.get(key):
                tokens += int(estimator.rough_token_count(json.dumps(request[key], ensure_ascii=False)) * estimator.config.padding_factor)
        return tokens

    def compile_request(self, config, messages, tools, *, stream=True, reasoning_effort=None) -> tuple[dict, int]:
        request = self._payload(config, messages, tools or [], stream=stream, reasoning_effort=reasoning_effort, check_budget=False)
        return request, self.estimate_payload_tokens(config, request)

    def output_limit(self, config: RuntimeLlmConfig) -> int:
        from app.services.context.model_profile import deployment_profile
        maximum = deployment_profile(config).get("max_output_tokens")
        values = [config.extra_body[key] for key in ("max_tokens", "max_completion_tokens", "max_output_tokens") if key in config.extra_body]
        if len(values) > 1:
            raise ValueError("输出预算不能同时设置多个协议字段")
        if values and config.sampling.get("max_output_tokens") is not None and values[0] != config.sampling["max_output_tokens"]:
            raise ValueError("额外请求体与标准输出预算冲突")
        value = values[0] if values else (config.sampling.get("max_output_tokens") or maximum or settings.llm_max_tokens)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("输出 token 上限必须为正整数")
        if maximum and value > maximum:
            raise ValueError("输出预算超过部署声明的硬上限")
        return value

    def context_window(self, config) -> int | None:
        from app.services.context.model_profile import deployment_profile
        maximum = deployment_profile(config).get("context_window")
        value = (config.sampling or {}).get("context_window") or maximum
        if maximum and value > maximum:
            raise ValueError("上下文预算超过部署声明的硬上限")
        return value

    def validate_config_contract(self, config) -> dict[str, Any]:
        """离线检查真实编译器和原生回放，不调用模型或执行工具。"""
        import hashlib
        from app.services.context.model_profile import resolve_model_profile
        from app.services.context.provider_adapters import native_message
        profile = resolve_model_profile(config)
        self._endpoint(config)
        tools = [{"type": "function", "function": {"name": "echo_contract", "parameters": {
            "type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}}}]
        call = {"id": "contract-call", "type": "function", "function": {"name": "echo_contract", "arguments": '{"value":"192.0.2.10"}'}}
        assistant = {"role": "assistant", "content": "192.0.2.10", "reasoning_content": "", "tool_calls": [call]}
        if config.api_format == "openai-responses":
            reasoning = {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "offline data"}]} if profile.profile_id == "deepseek-thinking/v1" else {"type": "reasoning", "encrypted_content": "offline-placeholder"}
            assistant = native_message(config, {"output": [reasoning,
                {"type": "function_call", "call_id": "contract-call", "name": "echo_contract", "arguments": call["function"]["arguments"]}]})
        elif config.api_format == "anthropic-messages":
            assistant = native_message(config, {"content": [{"type": "thinking", "thinking": "", "signature": "offline-placeholder"},
                {"type": "tool_use", "id": "contract-call", "name": "echo_contract", "input": {"value": "192.0.2.10"}}]})
        messages = [{"role": "system", "content": "offline contract data"}, {"role": "user", "content": "echo 192.0.2.10"},
                    assistant, {"role": "tool", "tool_call_id": "contract-call", "content": "192.0.2.10"}, {"role": "user", "content": "continue"}]
        request = self._payload(config, messages, tools, stream=True)
        profile = resolve_model_profile(config)
        from app.services.context.integrity import context_integrity_validator
        if not context_integrity_validator.validate(messages).ok:
            raise RuntimeError("离线工具组依赖没有闭合")
        if config.api_format == "openai-compatible":
            replay = next(item for item in request["messages"] if item["role"] == "assistant")
            actual = json.loads(replay["tool_calls"][0]["function"]["arguments"])["value"]
            field = "reasoning_content" if profile.profile_id == "deepseek-thinking/v1" and profile.requires_reasoning(config, tools) else config.sampling.get("reasoning_field") or "reasoning_content"
            if (profile.requires_reasoning(config, tools) or config.sampling.get("reasoning_replay") != "disabled") and replay.get(field) != "":
                raise RuntimeError("离线空思考回传不一致")
        elif config.api_format == "openai-responses":
            actual = next(json.loads(item["arguments"])["value"] for item in request["input"] if item.get("type") == "function_call")
            if not all(item in request["input"] for item in assistant["provider_payload"]["items"]):
                raise RuntimeError("离线原生 item 回放不一致")
        else:
            actual = next(part["input"]["value"] for item in request["messages"] for part in item.get("content", []) if part.get("type") == "tool_use")
            if assistant["provider_payload"]["items"][0] not in request["messages"]:
                raise RuntimeError("离线原生内容块回放不一致")
        if actual != "192.0.2.10":
            raise RuntimeError("离线标识符回放不一致")
        return {"validation_state": "offline_contract_passed", "real_service_verified": False,
                "profile_id": profile.profile_id, "api_format": config.api_format,
                "config_fingerprint": hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                "estimated_input_tokens": self.estimate_payload_tokens(config, request), "output_limit": self.output_limit(config),
                "context_window": self.context_window(config),
                "effective_parameters": self.describe_parameters(config, request),
                "checks": ["actual_request_compiler", "closed_tool_group", "literal_identifiers", "reasoning_replay_projection"],
                "note": "仅检查配置与协议投影；占位签名/密文不验证服务端签名，未连接真实模型"}

    def describe_replay(self, config, request: dict[str, Any]) -> dict[str, Any]:
        """统计真正发送的协议状态，不能把归档中的思考误报成已回传。"""
        if config.api_format == "openai-compatible":
            count = sum(message.get("role") == "assistant" and any(isinstance(message.get(field), str) for field in ("reasoning_content", "reasoning")) for message in request["messages"])
            unit = "assistant_messages"
        elif config.api_format == "openai-responses":
            count = sum(item.get("type") in {"reasoning", "compaction"} for item in request["input"])
            unit = "native_items"
        else:
            count = sum(block.get("type") in {"thinking", "redacted_thinking"} for message in request["messages"]
                        for block in message.get("content", []) if isinstance(block, dict))
            unit = "native_blocks"
        tools = request.get("tools") or []
        names = [(tool.get("function") or {}).get("name") if config.api_format == "openai-compatible" else tool.get("name") for tool in tools]
        return {"reasoning_count": count, "unit": unit, "tool_names": names}

    def describe_parameters(self, config, request: dict[str, Any], *, reasoning_effort=None) -> dict[str, Any]:
        """从最终请求解释映射与已知忽略行为，不把内网服务声明当作实测。"""
        from urllib.parse import urlparse
        result = {}
        official_deepseek = urlparse(config.base_url).hostname == "api.deepseek.com"
        thinking = (config.extra_body.get("thinking") or {}).get("type") != "disabled" and (request.get("reasoning") or {}).get("effort") != "none"
        for name in ("temperature", "frequency_penalty", "presence_penalty", "top_p", "repetition_penalty", "reasoning_effort", "stop", "seed", "parallel_tool_calls"):
            sent_as, value = name, request.get(name)
            if name == "reasoning_effort" and config.api_format != "openai-compatible":
                sent_as = "reasoning.effort" if config.api_format == "openai-responses" else "output_config.effort"
                value = (request.get(sent_as.split(".")[0]) or {}).get("effort")
            elif name == "stop" and config.api_format == "anthropic-messages":
                sent_as, value = "stop_sequences", request.get("stop_sequences")
            elif name == "parallel_tool_calls" and config.api_format == "anthropic-messages":
                sent_as = "tool_choice.disable_parallel_tool_use"
                disabled = (request.get("tool_choice") or {}).get("disable_parallel_tool_use")
                value = None if disabled is None else not disabled
            status = "model_default" if value is None else "sent_unverified"
            reason = "未发送，采用服务默认" if value is None else "已发送；真实服务是否采纳需部署验证"
            if official_deepseek and value is not None and ((thinking and name in {"temperature", "frequency_penalty", "presence_penalty"}) or (config.api_format == "openai-responses" and name == "parallel_tool_calls")):
                status, reason = "ignored_by_service", "DeepSeek 官方接口文档声明此模式忽略该参数"
            if official_deepseek and name == "top_p" and value is not None:
                if "flash" in config.model.casefold():
                    status = "constrained_by_service" if thinking else "ignored_by_service"
                    reason = "DeepSeek Flash 思考模式将 top_p 限制在 0.95–1.0；非思考模式固定为 1.0"
                elif "v4-pro" in config.model.casefold() and thinking:
                    status, reason = "ignored_by_service", "DeepSeek V4 Pro 思考模式固定 top_p（启用工具为 0.95，否则为 1.0）"
            source = "request" if name == "reasoning_effort" and reasoning_effort is not None else config.sampling_sources.get(name, "explicit" if value is not None else "model_default")
            result[name] = {"value": value, "source": source, "sent_as": sent_as if value is not None else None, "status": status, "reason": reason}
        return result

    def _strip_tool_fields_when_disabled(self, payload: dict[str, Any]) -> dict[str, Any]:
        tools = payload.get("tools")
        if tools not in (None, [], False):
            return payload
        payload.pop("tools", None)
        payload.pop("tool_choice", None)
        payload.pop("tool_stream", None)
        payload.pop("parallel_tool_calls", None)
        return payload

    def _response_error_detail(self, response: httpx.Response) -> str:
        try:
            text = response.text.strip()
        except Exception:  # noqa: BLE001
            text = ""
        if text:
            return text
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            payload = None
        if payload is None:
            return ""
        if isinstance(payload, (dict, list)):
            try:
                return json.dumps(payload, ensure_ascii=False)
            except Exception:  # noqa: BLE001
                return str(payload)
        return str(payload).strip()

    def _raise_for_status_with_detail(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = self._response_error_detail(response)
            if not detail:
                raise
            request_url = str(exc.request.url) if exc.request is not None else ""
            url_suffix = f" for url '{request_url}'" if request_url else ""
            raise httpx.HTTPStatusError(
                f"LLM request failed with HTTP {response.status_code}{url_suffix}: {detail}",
                request=exc.request,
                response=exc.response,
            ) from exc

    async def _post_json(self, config: RuntimeLlmConfig, payload: dict[str, Any], *, endpoint: str | None = None) -> dict[str, Any]:
        trace = config.trace_context
        attempt_id = f"attempt_{uuid.uuid4().hex}" if trace.get("session_id") else None
        if attempt_id:
            from app.services.context.history_store import history_store
            history_store.start_attempt(trace["session_id"], attempt_id, {
                **trace, "model": config.model, "api_format": config.api_format,
                "request_hash": hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                "output_limit": self.output_limit(config), "estimated_input_tokens": self.estimate_payload_tokens(config, payload)})
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds, verify=settings.llm_ssl_verify) as client:
                response = await client.post(endpoint or self._endpoint(config), headers=self._headers(config), json=payload)
                self._raise_for_status_with_detail(response)
                try:
                    data = response.json()
                except ValueError as exc:
                    raise ModelStreamError("模型返回了无法解析的 JSON 响应") from exc
                if not isinstance(data, dict):
                    raise ModelStreamError("模型响应必须是 JSON 对象")
            if attempt_id:
                history_store.save_attempt(trace["session_id"], attempt_id,
                    {"usage": data.get("usage") or {}, "api_status": data.get("status"),
                     "finish_reason": (data.get("choices") or [{}])[0].get("finish_reason") or data.get("stop_reason"),
                     "response_hash": hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}, "received")
            return data
        except BaseException as exc:
            if attempt_id:
                history_store.save_attempt(trace["session_id"], attempt_id, {"error_type": type(exc).__name__}, "failed")
            raise

    async def compact_native(self, config: RuntimeLlmConfig, messages: list[dict[str, Any]]) -> dict[str, Any]:
        if config.api_format != "openai-responses":
            raise ValueError("当前协议未启用原生压缩")
        from app.services.context.model_profile import resolve_model_profile, deployment_profile
        if deployment_profile(config).get("native_compaction") is False:
            raise ValueError("部署声明不支持原生压缩")
        if resolve_model_profile(config).profile_id == "deepseek-thinking/v1" and deployment_profile(config).get("native_compaction") is not True:
            raise ValueError("DeepSeek Responses 未声明支持原生压缩；使用本地检查点，或在已验证的部署能力中明确声明支持")
        encoded = native_request(config, messages, {"model": config.model, "max_tokens": self.output_limit(config), "stream": False})
        payload = {"model": config.model, "input": encoded["input"]}
        result = await self._post_json(config, payload, endpoint=self._endpoint(config) + "/compact")
        items = result.get("output")
        if not isinstance(items, list) or not any(item.get("type") == "compaction" for item in items):
            raise RuntimeError("原生压缩没有返回有效检查点，保留原始上下文")
        from app.services.context.provider_adapters import binding
        return {"binding": binding(config), "items": items, "complete_window": True}

    async def _stream_request(
        self,
        config: RuntimeLlmConfig,
        payload: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds, verify=settings.llm_ssl_verify) as client:
            async with client.stream("POST", self._endpoint(config), headers=self._headers(config), json=payload) as response:
                if response.is_error:
                    await response.aread()
                    self._raise_for_status_with_detail(response)
                finished = False
                try:
                    async for data in self._sse_frames(response):
                        if data == "[DONE]":
                            if not finished:
                                raise httpx.RemoteProtocolError("模型发送 DONE 前没有有效结束原因")
                            return
                        parsed = self._decode_frame(data)
                        finished = finished or self._is_terminal_frame(parsed)
                        yield parsed
                        if finished and (config.api_format != "openai-compatible" or parsed.get("usage") is not None
                            or not (payload.get("stream_options") or {}).get("include_usage")):
                            return
                except (httpx.TransportError, RuntimeError, ValueError):
                    # 有效终态已提交时，HTTP 尾部中断不能把完整生成重新标成部分输出。
                    if finished:
                        return
                    raise
                if not finished:
                    raise httpx.RemoteProtocolError("模型流在有效终止事件前结束")

    async def _sse_frames(self, response):
        from app.services.context.stream_parser import SseDataDecoder
        decoder = SseDataDecoder()
        async for piece in response.aiter_bytes():
            for frame in decoder.feed(piece):
                yield frame
        for frame in decoder.feed(b"", final=True):
            yield frame

    def _decode_frame(self, data: str) -> dict[str, Any]:
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as exc:
            raise RuntimeError("模型流包含无法解析的 SSE 数据帧") from exc
        if not isinstance(parsed, dict) or parsed.get("error"):
            raise RuntimeError(f"模型流返回错误: {parsed.get('error') if isinstance(parsed, dict) else 'invalid frame'}")
        choices = parsed.get("choices") or []
        if "choices" in parsed:
            # 兼容网关返回额外候选时只消费主候选，绝不串接不同候选的正文或工具。
            parsed["choices"] = [choice for choice in choices if choice.get("index", 0) == 0][:1]
        return parsed

    def _is_terminal_frame(self, parsed: dict) -> bool:
        return any(choice.get("finish_reason") for choice in parsed.get("choices", [])) or parsed.get("type") in {"response.completed", "response.incomplete", "message_stop"}

    async def create_chat_completion(
        self,
        config: RuntimeLlmConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        payload = self._payload(config, messages, tools, stream=False, reasoning_effort=reasoning_effort)
        data = await self._post_json(config, payload)

        if config.api_format != "openai-compatible":
            if data.get("status") in {"failed", "incomplete"} or data.get("stop_reason") == "max_tokens":
                raise ModelStreamError("模型生成未完整结束")
            return native_message(config, data)
        choices = data.get("choices") or []
        if not choices:
            raise ModelStreamError("LLM 未返回有效结果。")
        choices = [next((choice for choice in choices if choice.get("index", 0) == 0), choices[0])]
        if choices[0].get("finish_reason") == "length":
            raise ModelStreamError("模型生成达到输出上限，不能将部分内容当作完整结果")
        return choices[0].get("message") or {}

    async def stream_chat_completion(
        self,
        config: RuntimeLlmConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        reasoning_effort: str | None = None,
        prepared_payload: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        payload = prepared_payload if prepared_payload is not None else self._payload(config, messages, tools, stream=True, reasoning_effort=reasoning_effort)
        native_stream = NativeStream(config) if config.api_format != "openai-compatible" else None
        try:
            async for item in self._stream_request(config, payload):
                if native_stream:
                    for decoded in native_stream.decode(item):
                        yield decoded
                else:
                    yield item
        except (RuntimeError, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ModelStreamError(str(exc)) from exc


llm_client = LlmClient()
