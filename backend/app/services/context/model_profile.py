"""版本化协议能力：约束回传语义，不替用户猜测采样值。"""
from __future__ import annotations

from dataclasses import dataclass, replace
import re
import json
from urllib.parse import urlparse


def deployment_profile(config) -> dict:
    sampling = getattr(config, "sampling", None) or {}
    if hasattr(sampling, "model_dump"):
        sampling = sampling.model_dump()
    value = sampling.get("deployment_profile") or {}
    return value.model_dump() if hasattr(value, "model_dump") else value


def model_text(value):
    """兼容网关的文本块表示；不因可规范化的字段形状打断生成。"""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(part, dict) and isinstance(part.get("text"), str) for part in value):
        return "".join(part["text"] for part in value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class ModelProfile:
    profile_id: str
    reasoning_required_with_tools: bool = False
    effort_options: tuple[str, ...] = ()

    def requires_reasoning(self, config, tools) -> bool:
        if config.api_format != "openai-compatible" or not tools or not self.reasoning_required_with_tools:
            return False
        extra = config.extra_body or {}
        if extra.get("tools") == []:
            return False
        thinking = extra.get("thinking") or {}
        template = extra.get("chat_template_kwargs") or {}
        if deployment_profile(config).get("thinking_mode") == "disabled":
            return False
        if thinking.get("type") == "disabled" or template.get("enable_thinking") is False:
            return False
        return True


def resolve_model_profile(config) -> ModelProfile:
    sampling = getattr(config, "sampling", None) or {}
    if hasattr(sampling, "model_dump"):
        sampling = sampling.model_dump()
    selected = sampling.get("model_profile") or "auto"
    if selected not in {"auto", "generic", "deepseek-thinking"}:
        raise ValueError(f"未知模型协议配置: {selected}")
    host = urlparse(config.base_url).hostname or ""
    family = config.model.casefold()
    recognized_thinking_family = "deepseek" in family and bool(re.search(r"v4|v3[._-]?2|flash", family))
    deepseek = selected == "deepseek-thinking" or (selected == "auto" and
        (recognized_thinking_family or host == "api.deepseek.com"))
    efforts = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra") if deepseek else ()
    if deepseek and config.api_format == "openai-responses":
        efforts = ("none", *efforts)
    deployment = deployment_profile(config)
    if deployment.get("served_model_name") and deployment["served_model_name"] != config.model:
        raise ValueError("部署声明的模型与实际配置不一致")
    required = deployment.get("reasoning_required_with_tools")
    return ModelProfile("deepseek-thinking/v1" if deepseek else "generic/v1", deepseek or bool(required),
                        tuple(deployment.get("effort_options") or efforts))


def project_reasoning_replay(config, messages, tools) -> list[dict]:
    """有思考原样回传，无思考补空字段；字段缺失不是任务失败条件。

    只修改请求投影，账本仍区分服务没有返回与实际返回空串。
    """
    required = resolve_model_profile(config).requires_reasoning(config, tools)
    field = (config.sampling or {}).get("reasoning_field") or "reasoning_content"
    if required and resolve_model_profile(config).profile_id == "deepseek-thinking/v1":
        field = "reasoning_content"
    disabled = (config.sampling or {}).get("reasoning_replay") == "disabled" and not required
    result = []
    for message in messages:
        item = dict(message)
        if item.get("role") == "assistant":
            value = item.pop("reasoning_content", None)
            if not isinstance(value, str) and isinstance(item.get("reasoning"), str):
                value = item["reasoning"]
            if not disabled and (isinstance(value, str) or required):
                item[field] = value if isinstance(value, str) else ""
            if field != "reasoning" or disabled:
                item.pop("reasoning", None)
        result.append(item)
    return result


def recover_optional_parameter(config, error: str):
    """仅按服务明确拒绝的可选参数协商；不撤销工具或替换模型、历史。"""
    lowered = error.casefold()
    lowered = lowered.replace("reasoning.effort", "reasoning_effort").replace("output_config.effort", "reasoning_effort")
    if not any(word in lowered for word in ("unsupported", "not support", "unrecognized", "unknown", "unexpected", "extra inputs", "not permitted", "not allowed", "only support", "must be", "invalid value", "out of range", "不支持", "未知参数")):
        return None
    sampling, extra = dict(config.sampling), dict(config.extra_body)
    for name in ("stream_options", "reasoning_effort", "parallel_tool_calls", "temperature", "top_p", "frequency_penalty", "presence_penalty", "repetition_penalty", "seed", "stop"):
        if not re.search(r"\b" + name + r"\b", lowered):
            continue
        key = "stream_usage" if name == "stream_options" else name
        if name == "stream_options" and sampling.get(key) is False and name not in extra:
            continue
        if name != "stream_options" and sampling.get(key) is None and name not in extra:
            continue
        sampling[key] = False if name == "stream_options" else None
        extra.pop(name, None)
        if name == "reasoning_effort":
            for container in ("reasoning", "output_config"):
                if isinstance(extra.get(container), dict):
                    value = {key: value for key, value in extra[container].items() if key != "effort"}
                    if value:
                        extra[container] = value
                    else:
                        extra.pop(container)
        return replace(config, sampling=sampling, extra_body=extra), name
    return None
