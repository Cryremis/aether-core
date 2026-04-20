# backend/app/runtime/engine.py
from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from app.core.config import settings
from app.runtime.event_protocol import make_event
from app.services.llm_config_service import llm_config_service
from app.services.llm_client import llm_client
from app.services.session_service import AgentSession, session_service
from app.services.store import store_service
from app.services.tool_service import tool_service


class AgentEngine:
    """AetherCore 运行时主循环。"""

    def _append_assistant_block(self, blocks: list[dict[str, Any]], block: dict[str, Any]) -> None:
        blocks.append(block)

    def _update_assistant_block(self, blocks: list[dict[str, Any]], block_id: str, **updates: Any) -> None:
        for block in blocks:
            if str(block.get("id")) == block_id:
                block.update(updates)
                return

    async def stream_chat(
        self,
        session: AgentSession,
        message: str,
    ) -> AsyncGenerator:
        started_at = time.perf_counter()
        session.messages.append({"role": "user", "content": message})
        session.touch()
        session_service.persist(session)
        conversation = store_service.get_conversation_by_session(session.session_id)
        is_new_conversation = conversation is None or conversation.get("message_count", 0) == 0
        if conversation is None:
            conversation = store_service.create_conversation(
                session_id=session.session_id,
                title=message.strip()[:80] or "新对话",
                host_name=session.host_name or "AetherCore",
                host_type=session.host_type or "standalone",
            )
        new_title = message.strip()[:80] or "新对话" if is_new_conversation else None
        store_service.touch_conversation(
            session.session_id,
            title=new_title,
            message_count=len(session.messages),
        )

        messages: list[dict[str, object]] = [
            {"role": "system", "content": self._build_system_message(session)},
            *session.messages,
        ]
        llm_runtime = llm_config_service.resolve_for_conversation(conversation)
        tools = tool_service.list_tool_schemas(session)
        turn_count = 0
        last_stop_reason: str | None = None
        last_tool_fingerprint: str | None = None
        stall_rounds = 0
        max_turns = settings.agent_max_turns if settings.agent_max_turns > 0 else None
        persisted_assistant_blocks: list[dict[str, Any]] = []

        while True:
            turn_count += 1
            runtime_seconds = int(time.perf_counter() - started_at)
            if settings.agent_max_runtime_seconds > 0 and runtime_seconds >= settings.agent_max_runtime_seconds:
                yield make_event(
                    session,
                    "result",
                    subtype="error_runtime_limit",
                    is_error=True,
                    turn_count=turn_count - 1,
                    runtime_seconds=runtime_seconds,
                    stop_reason=last_stop_reason,
                )
                yield make_event(
                    session,
                    "completed",
                    elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                    subtype="error_runtime_limit",
                    stop_reason=last_stop_reason,
                )
                return

            if max_turns is not None and turn_count > max_turns:
                yield make_event(
                    session,
                    "result",
                    subtype="error_max_turns",
                    is_error=True,
                    turn_count=turn_count,
                    max_turns=max_turns,
                    stop_reason=last_stop_reason,
                )
                yield make_event(
                    session,
                    "completed",
                    elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                    subtype="error_max_turns",
                    stop_reason=last_stop_reason,
                )
                return

            assistant_content = ""
            tool_calls: dict[int, dict[str, str]] = {}
            finish_reason = ""
            active_reasoning_block_id = ""
            active_content_block_id = ""

            async for chunk in llm_client.stream_chat_completion(llm_runtime, messages, tools):
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                finish_reason = choice.get("finish_reason") or finish_reason
                if finish_reason:
                    last_stop_reason = str(finish_reason)

                reasoning_delta = delta.get("reasoning_content") or delta.get("reasoning") or ""
                if reasoning_delta:
                    if not active_reasoning_block_id:
                        active_reasoning_block_id = f"reasoning_{uuid.uuid4().hex}"
                        self._append_assistant_block(
                            persisted_assistant_blocks,
                            {
                                "id": active_reasoning_block_id,
                                "kind": "reasoning",
                                "content": str(reasoning_delta),
                            },
                        )
                    else:
                        for block in persisted_assistant_blocks:
                            if str(block.get("id")) == active_reasoning_block_id:
                                block["content"] = f"{block.get('content', '')}{reasoning_delta}"
                                break
                    yield make_event(session, "reasoning_delta", round=turn_count, delta=reasoning_delta)

                content_delta = delta.get("content") or ""
                if content_delta:
                    assistant_content += content_delta
                    if not active_content_block_id:
                        active_content_block_id = f"content_{uuid.uuid4().hex}"
                        self._append_assistant_block(
                            persisted_assistant_blocks,
                            {
                                "id": active_content_block_id,
                                "kind": "content",
                                "content": assistant_content,
                                "status": "streaming",
                            },
                        )
                    else:
                        self._update_assistant_block(
                            persisted_assistant_blocks,
                            active_content_block_id,
                            content=assistant_content,
                            status="streaming",
                        )
                    yield make_event(session, "content_delta", round=turn_count, delta=content_delta)

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
                        round=turn_count,
                        index=index,
                        id=current["id"],
                        tool_name=current["name"],
                        arguments_delta=arguments_delta,
                        arguments=current["arguments"],
                    )

            if assistant_content:
                if active_content_block_id:
                    self._update_assistant_block(
                        persisted_assistant_blocks,
                        active_content_block_id,
                        content=assistant_content,
                        status="done",
                    )
                yield make_event(session, "content_completed", round=turn_count)
            for index, tool_call in tool_calls.items():
                yield make_event(
                    session,
                    "tool_call_completed",
                    round=turn_count,
                    index=index,
                    id=tool_call["id"],
                    tool_name=tool_call["name"],
                    arguments=tool_call["arguments"],
                )

            assistant_message: dict[str, object] = {"role": "assistant"}
            if assistant_content:
                assistant_message["content"] = assistant_content

            if tool_calls:
                tool_fingerprint = self._fingerprint_tool_calls(tool_calls)
                if tool_fingerprint == last_tool_fingerprint:
                    stall_rounds += 1
                else:
                    stall_rounds = 0
                last_tool_fingerprint = tool_fingerprint

                if settings.agent_max_stall_rounds > 0 and stall_rounds >= settings.agent_max_stall_rounds:
                    yield make_event(
                        session,
                        "result",
                        subtype="error_stalled",
                        is_error=True,
                        turn_count=turn_count,
                        repeated_rounds=stall_rounds + 1,
                        stop_reason=last_stop_reason,
                    )
                    yield make_event(
                        session,
                        "completed",
                        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                        subtype="error_stalled",
                        stop_reason=last_stop_reason,
                    )
                    return

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
                    tool_block_id = tool_call["id"]
                    self._append_assistant_block(
                        persisted_assistant_blocks,
                        {
                            "id": tool_block_id,
                            "kind": "tool",
                            "title": tool_name,
                            "meta": str(tool_input.get("shell", "tool")) if isinstance(tool_input, dict) else "tool",
                            "argumentsText": json.dumps(tool_input, ensure_ascii=False, indent=2),
                            "outputText": "",
                            "status": "running",
                        },
                    )
                    yield make_event(
                        session,
                        "tool_started",
                        id=tool_call["id"],
                        tool_name=tool_name,
                        input=tool_input,
                    )
                    try:
                        result = await tool_service.execute(session, tool_name, tool_input)
                    except Exception as exc:  # noqa: BLE001
                        result = {
                            "error": str(exc),
                            "summary": f"工具执行失败: {exc}",
                        }
                    visible_result = result.get("public_output", result) if isinstance(result, dict) else result
                    yield make_event(
                        session,
                        "tool_finished",
                        id=tool_call["id"],
                        tool_name=tool_name,
                        output=visible_result,
                    )
                    self._update_assistant_block(
                        persisted_assistant_blocks,
                        tool_block_id,
                        outputText=json.dumps(visible_result, ensure_ascii=False, indent=2),
                        status="done",
                    )
                    artifact_payload = visible_result.get("artifact") if isinstance(visible_result, dict) else None
                    if artifact_payload:
                        yield make_event(session, "artifact_created", artifact=artifact_payload)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": json.dumps(visible_result, ensure_ascii=False),
                        }
                    )
                    injected_messages = result.get("injected_messages", []) if isinstance(result, dict) else []
                    if injected_messages:
                        messages.extend(injected_messages)
                continue

            last_tool_fingerprint = None
            stall_rounds = 0
            final_answer = assistant_content.strip()
            if final_answer:
                session.messages.append(
                    {
                        "role": "assistant",
                        "content": final_answer,
                        "blocks": persisted_assistant_blocks,
                    }
                )
                session_service.persist(session)
                store_service.touch_conversation(
                    session.session_id,
                    message_count=len(session.messages),
                )
                yield make_event(session, "message", summary=final_answer)
                yield make_event(
                    session,
                    "result",
                    subtype="success",
                    is_error=False,
                    turn_count=turn_count,
                    stop_reason=last_stop_reason,
                    result=final_answer,
                )
                yield make_event(
                    session,
                    "completed",
                    elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                    subtype="success",
                    stop_reason=last_stop_reason,
                )
                return

            yield make_event(
                session,
                "result",
                subtype="error_empty_response",
                is_error=True,
                turn_count=turn_count,
                stop_reason=last_stop_reason,
            )
            yield make_event(
                session,
                "completed",
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                subtype="error_empty_response",
                stop_reason=last_stop_reason,
            )
            return

    def _fingerprint_tool_calls(self, tool_calls: dict[int, dict[str, str]]) -> str:
        payload = [
            {
                "name": item["name"],
                "arguments": item["arguments"],
            }
            for _, item in sorted(tool_calls.items())
        ]
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(serialized.encode("utf-8")).hexdigest()

    def _build_system_message(self, session: AgentSession) -> str:
        return self._safe_prompt(session)

    def _safe_prompt(self, session: AgentSession) -> str:
        from app.services.skill_service import skill_service

        return skill_service.build_system_prompt(session)


agent_engine = AgentEngine()
