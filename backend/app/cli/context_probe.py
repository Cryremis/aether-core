"""在实际部署上验证流式、多轮复制与思考/工具回传；只执行内存 echo 工具。"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from app.services.llm_client import LlmClient
from app.services.llm_config_service import RuntimeLlmConfig


PROBE_TOOL = {"type": "function", "function": {
    "name": "echo_probe", "description": "在内存中原样回显 value，无外部副作用。",
    "parameters": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False},
}}


async def run_probe(config: RuntimeLlmConfig, turns: int = 3) -> dict:
    client = LlmClient()
    history = [{"role": "system", "content": "使用中文理解任务。严格按用户要求复制标识符，不改写标点。不执行任何外部操作。"}]
    checks = []
    literals = ["192.0.2.10", "https://example.invalid/a-b?q=1.2", "C:\\Work\\AetherCore\\file_v1.2.txt"]
    for turn in range(turns):
        expected = literals[turn % len(literals)]
        history.append({"role": "user", "content": f"请先调用 echo_probe，value 为 {expected}，再只返回该值，不添加解释、引号或代码围栏。"})
        tool_count = 0
        reasoning_chars = 0
        answer = ""
        for step in range(4):
            message = {"role": "assistant", "content": ""}
            calls = {}
            finish = None
            async for chunk in client.stream_chat_completion(config, history, [PROBE_TOOL]):
                if chunk.get("provider_payload"):
                    message["provider_payload"] = chunk["provider_payload"]
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    finish = choice.get("finish_reason") or finish
                    message["content"] += delta.get("content") or ""
                    reasoning = delta.get("reasoning_content") if isinstance(delta.get("reasoning_content"), str) else delta.get("reasoning")
                    if isinstance(reasoning, str):
                        message["reasoning_content"] = message.get("reasoning_content", "") + reasoning
                        reasoning_chars += len(reasoning)
                    for call in delta.get("tool_calls") or []:
                        state = calls.setdefault(call.get("index", 0), {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if call.get("id"):
                            state["id"] = call["id"]
                        function = call.get("function") or {}
                        if function.get("name"):
                            state["function"]["name"] += function["name"]
                        state["function"]["arguments"] += function.get("arguments") or ""
            if finish not in {"stop", "tool_calls"}:
                raise RuntimeError(f"诊断请求未完整结束: {finish}; 可调高 --max-output 后重新测试")
            if calls:
                message["tool_calls"] = [calls[index] for index in sorted(calls)]
            history.append(message)
            if not calls:
                answer = message["content"]
                break
            for call in message["tool_calls"]:
                if call["function"]["name"] != "echo_probe":
                    raise RuntimeError("模型调用了诊断允许范围之外的工具，未执行")
                arguments = json.loads(call["function"]["arguments"])
                if set(arguments) != {"value"} or not isinstance(arguments["value"], str):
                    raise RuntimeError("echo 参数不符合约定，未执行")
                tool_count += 1
                history.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"value": arguments["value"]}, ensure_ascii=False)})
        checks.append({"turn": turn + 1, "expected": expected, "actual": answer,
                       "exact_match": answer == expected, "tool_calls": tool_count, "reasoning_chars": reasoning_chars})
    return {"model": config.model, "api_format": config.api_format, "checks": checks,
            "passed": all(check["exact_match"] and check["tool_calls"] > 0 for check in checks)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-format", choices=["openai-compatible", "openai-responses", "anthropic-messages"], default="openai-compatible")
    parser.add_argument("--api-key-env", default="AETHERCORE_PROBE_API_KEY", help="只从此环境变量读取密钥；不会打印或写入报告")
    parser.add_argument("--context-window", type=int, default=131072)
    parser.add_argument("--max-output", type=int, default=4096)
    parser.add_argument("--model-profile", choices=["auto", "generic", "deepseek-thinking"], default="auto")
    parser.add_argument("--thinking", choices=["enabled", "disabled"], help="仅在服务支持 thinking.type 时指定；不猜测 vLLM 模板开关")
    parser.add_argument("--turns", type=int, choices=range(1, 51), default=3)
    parser.add_argument("--output", default=".runtime/model-context-report.json")
    args = parser.parse_args()
    config = RuntimeLlmConfig(scope="user", provider_kind="litellm", api_format=args.api_format,
        base_url=args.base_url, model=args.model, api_key=os.environ.get(args.api_key_env, ""),
        sampling={"context_window": args.context_window, "max_output_tokens": args.max_output, "model_profile": args.model_profile},
        extra_body={"thinking": {"type": args.thinking}} if args.thinking else {})
    try:
        report = asyncio.run(run_probe(config, args.turns))
    except Exception as exc:
        report = {"model": args.model, "passed": False, "error": str(exc).replace(config.api_key, "[REDACTED]") if config.api_key else str(exc)}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
