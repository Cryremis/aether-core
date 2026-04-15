# backend/app/runtime/engine.py
from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from app.core.config import settings
from app.runtime.event_protocol import make_event
from app.services.artifact_service import artifact_service
from app.services.llm_client import llm_client
from app.services.session_service import AgentSession
from app.services.tool_service import tool_service


class AgentEngine:
    """AetherCore 运行时主循环。"""

    async def stream_chat(
        self,
        session: AgentSession,
        message: str,
    ) -> AsyncGenerator:
        started_at = time.perf_counter()
        session.messages.append({"role": "user", "content": message})
        session.touch()

        yield make_event(
            session,
            "reasoning_delta",
            delta="已接收请求，正在整理宿主上下文、技能提示词与工具清单。",
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._build_system_message(session)},
            *session.messages,
        ]
        tools = tool_service.list_tool_schemas(session)
        final_answer = ""

        for round_index in range(1, settings.llm_max_steps + 1):
            assistant_content = ""
            tool_calls: dict[int, dict[str, Any]] = {}
            finish_reason = ""

            async for chunk in llm_client.stream_chat_completion(messages=messages, tools=tools):
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                finish_reason = choice.get("finish_reason") or finish_reason

                reasoning_delta = delta.get("reasoning_content") or delta.get("reasoning") or ""
                if reasoning_delta:
                    yield make_event(session, "reasoning_delta", round=round_index, delta=reasoning_delta)

                content_delta = delta.get("content") or ""
                if content_delta:
                    assistant_content += content_delta
                    yield make_event(session, "content_delta", round=round_index, delta=content_delta)

                for tool_call in delta.get("tool_calls") or []:
                    index = int(tool_call.get("index", 0))
                    function = tool_call.get("function") or {}
                    current = tool_calls.setdefault(
                        index,
                        {
                            "id": tool_call.get("id") or f"call_{uuid.uuid4().hex}",
                            "name": "",
                            "arguments": "",
                        },
                    )
                    if tool_call.get("id"):
                        current["id"] = tool_call["id"]
                    if function.get("name"):
                        current["name"] = function["name"]
                    arguments_delta = function.get("arguments") or ""
                    if arguments_delta:
                        current["arguments"] += arguments_delta
                    yield make_event(
                        session,
                        "tool_call_delta",
                        round=round_index,
                        index=index,
                        id=current["id"],
                        tool_name=current["name"],
                        arguments_delta=arguments_delta,
                        arguments=current["arguments"],
                    )

            if assistant_content:
                yield make_event(session, "content_completed", round=round_index)
            for index, tool_call in tool_calls.items():
                yield make_event(
                    session,
                    "tool_call_completed",
                    round=round_index,
                    index=index,
                    id=tool_call["id"],
                    tool_name=tool_call["name"],
                    arguments=tool_call["arguments"],
                )

            assistant_message: dict[str, Any] = {"role": "assistant"}
            if assistant_content:
                assistant_message["content"] = assistant_content

            if tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": tool_call["id"],
                        "type": "function",
                        "function": {
                            "name": tool_call["name"],
                            "arguments": tool_call["arguments"],
                        },
                    }
                    for _, tool_call in sorted(tool_calls.items())
                ]
                messages.append(assistant_message)

                for _, tool_call in sorted(tool_calls.items()):
                    tool_name = tool_call["name"]
                    tool_input = tool_service.parse_tool_arguments(tool_call["arguments"])
                    yield make_event(
                        session,
                        "tool_started",
                        id=tool_call["id"],
                        tool_name=tool_name,
                        input=tool_input,
                    )
                    result = await tool_service.execute(session, tool_name, tool_input)
                    yield make_event(
                        session,
                        "tool_finished",
                        id=tool_call["id"],
                        tool_name=tool_name,
                        output=result,
                    )
                    artifact_payload = result.get("artifact")
                    if artifact_payload:
                        yield make_event(session, "artifact_created", artifact=artifact_payload)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                continue

            final_answer = assistant_content.strip()
            if not final_answer and finish_reason != "tool_calls":
                final_answer = "当前没有拿到明确结论，请补充更具体的任务目标后再试。"
            break

        if not final_answer:
            final_answer = "我已尝试多轮规划和工具调用，但仍不足以给出可靠结论。"

        answer_artifact = artifact_service.create_text_artifact(
            session=session,
            name="assistant-latest.md",
            content=final_answer,
        )
        yield make_event(session, "artifact_created", artifact=answer_artifact.model_dump(mode="json"))

        session.messages.append({"role": "assistant", "content": final_answer})
        yield make_event(session, "message", summary=final_answer)
        yield make_event(session, "completed", elapsed_ms=int((time.perf_counter() - started_at) * 1000))

    def _build_system_message(self, session: AgentSession) -> str:
        return self._safe_prompt(session)

    def _safe_prompt(self, session: AgentSession) -> str:
        from app.services.skill_service import skill_service

        return skill_service.build_system_prompt(session)


agent_engine = AgentEngine()
