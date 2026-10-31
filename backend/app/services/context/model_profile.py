"""版本化协议能力：约束回传语义，不替用户猜测采样值。"""
from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse


def deployment_profile(config) -> dict:
    sampling = getattr(config, "sampling", None) or {}
    if hasattr(sampling, "model_dump"):
        sampling = sampling.model_dump()
    value = sampling.get("deployment_profile") or {}
    return value.model_dump() if hasattr(value, "model_dump") else value


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


def missing_reasoning_messages(config, messages, tools) -> list[dict]:
    profile = resolve_model_profile(config)
    if not profile.requires_reasoning(config, tools):
        return []
    return [message for message in messages if message.get("role") == "assistant"
            and not message.get("projection_only") and not isinstance(message.get("reasoning_content"), str)]


def validate_reasoning_replay(config, messages, tools) -> None:
    profile = resolve_model_profile(config)
    if not profile.requires_reasoning(config, tools):
        return
    name = "DeepSeek" if profile.profile_id == "deepseek-thinking/v1" else "当前部署"
    if (config.sampling or {}).get("reasoning_replay") == "disabled":
        raise ValueError(f"{name}思考模式携带工具时必须回传全部历史思考，不能禁用回传；非思考服务请显式关闭 thinking 或选择通用协议配置")
    if profile.profile_id == "deepseek-thinking/v1" and (config.sampling or {}).get("reasoning_field", "reasoning_content") not in {None, "reasoning_content"}:
        raise ValueError(f"{name}思考回传必须使用 reasoning_content 字段；其他兼容服务请选择通用协议配置")
    if missing_reasoning_messages(config, messages, tools):
        raise ValueError(f"{name}历史缺少完整 reasoning_content 思考字段，需要从原始记录恢复或建立明确的新上下文阶段")
