# backend/app/services/context/context_pipeline.py
"""生产级上下文管线。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import copy
from datetime import datetime, timezone
import logging
import sqlite3
from time import perf_counter

import httpx
from typing import Any

from app.core.config import settings
from app.services.context.contracts import SessionPersister
from app.services.context.context_budget import ContextBudget, ContextBudgetConfig, calibrated_tokens, input_budget
from app.services.context.context_state_store import ContextStateStore, context_state_store
from app.services.context.integrity import context_integrity_validator
from app.services.context.message_groups import api_groups, latest_user_message
from app.services.context.message_adapter import context_message_adapter
from app.services.context.provider_adapters import history_system_rules
from app.services.context.runtime_types import (
    CompactionPlan,
    CompactionStrategyName,
    ContextEvent,
    ContextEventType,
    ContextOverflowError,
    ContextIntegrityError,
    ContextSessionState,
    PreparedContext,
    utc_now_iso,
)
from app.services.context.token_estimation import TokenEstimator
from app.services.context.truncate import truncate_json_content, truncate_tool_result
from app.services.session_types import AgentSession

logger = logging.getLogger(__name__)

@dataclass
class ContextPipelineConfig:
    compaction_cooldown_seconds: int = 60
    keep_recent_turns: int = 4
    keep_recent_turns_reactive: int = 2
    tool_result_token_threshold: int = 2_500
    tool_result_max_chars: int = 1_500
    summary_max_lines: int = 24
    summary_line_max_chars: int = 320
    minimum_messages_for_summary: int = 6
    reactive_max_retries: int = 2
    emit_status_each_turn: bool = True


class ContextPipeline:
    """AetherCore 运行时历史消息的生产级上下文管线。"""

    def __init__(
        self,
        config: ContextPipelineConfig | None = None,
        budget_config: ContextBudgetConfig | None = None,
        estimator: TokenEstimator | None = None,
        state_store: ContextStateStore | None = None,
        persister: SessionPersister | None = None,
    ):
        self.config = config or ContextPipelineConfig()
        self.budget = ContextBudget(config=budget_config or ContextBudgetConfig())
        self.estimator = estimator or TokenEstimator()
        self.state_store = state_store or ContextStateStore(persister=persister)
        self._persister = persister

    def bind_persister(self, persister: SessionPersister) -> None:
        self._persister = persister
        self.state_store.bind_persister(persister)

    def prepare_for_llm(
        self,
        *,
        session: AgentSession,
        llm_runtime: Any,
        messages: list[dict[str, Any]],
        turn_index: int,
        tools: list[dict[str, Any]] | None = None,
        max_output_tokens: int | None = None,
        allow_compaction: bool = True,
        allow_soft_compaction: bool = True,
    ) -> PreparedContext:
        changed = context_message_adapter.normalize_session(session)
        if changed:
            self._persist_session(session)

        state = self.state_store.get(session)
        state = self._refresh_state_thresholds(state, str(llm_runtime.model), max_output_tokens, runtime=llm_runtime)

        runtime_messages = [
            context_message_adapter.ensure_runtime_metadata(message, turn_index=message.get("turn_index", 0))
            for message in messages
        ]
        self._validate_or_raise(context_message_adapter.to_api_messages(runtime_messages), state, 0)
        runtime_messages, warning, notice = self._with_budget_notice(
            session, runtime_messages, state, llm_runtime, tools)
        token_estimate = state.last_known_token_estimate

        hard = token_estimate > state.blocking_limit or not state.blocking_limit
        last_compaction = max(filter(None, (state.last_compaction_at, state.last_compaction_attempt_at)), default=None)
        cooling_down = False
        if last_compaction:
            try:
                previous = datetime.fromisoformat(last_compaction)
                cooling_down = (datetime.now(timezone.utc) - previous).total_seconds() < self.config.compaction_cooldown_seconds
            except (ValueError, TypeError):
                pass
        soft = allow_soft_compaction and self.budget.config.auto_compact_enabled and token_estimate >= state.warning_threshold and not cooling_down
        if hard or soft:
            cause = "hard_limit" if hard else "soft_limit"
            if not allow_compaction:
                raise ContextOverflowError("上下文需要压缩", state=state, token_estimate=token_estimate, reason=cause)
            return self.compact_session(
                session=session, llm_runtime=llm_runtime, messages=messages, turn_index=turn_index,
                tools=tools, max_output_tokens=max_output_tokens, cause=cause, aggressive=hard,
            )

        api_messages = context_message_adapter.to_api_messages(runtime_messages)
        self._validate_or_raise(api_messages, state, token_estimate)
        if notice is not None:
            session.messages.append(notice)
        self.state_store.save(session, state)
        return PreparedContext(
            messages=api_messages,
            state=state,
            events=self._build_status_events(state, token_estimate),
            ephemeral_warning=warning,
        )

    async def prepare_for_llm_async(self, **kwargs) -> PreparedContext:
        try:
            return self.prepare_for_llm(**kwargs, allow_compaction=False)
        except ContextOverflowError as exc:
            return await self.recover_request_budget(exc, **kwargs)

    async def recover_request_budget(self, overflow: ContextOverflowError, **kwargs) -> PreparedContext:
        cause = overflow.reason if overflow.reason in {"soft_limit", "provider_overflow"} else "hard_limit"
        try:
            return await self.compact_session_async(**kwargs, cause=cause, aggressive=cause != "soft_limit")
        except ContextOverflowError as exc:
            if cause != "soft_limit" or exc.reason in {"pinned_over_budget", "retry_limit"}:
                raise
            session = kwargs["session"]
            state = self.state_store.get(session)
            state.last_compaction_attempt_at = utc_now_iso()
            self.state_store.save(session, state)
            return self.prepare_for_llm(**kwargs, allow_compaction=False, allow_soft_compaction=False)

    async def compact_session_async(self, *, session, llm_runtime, messages, turn_index,
                                    tools=None, max_output_tokens=None, cause="agent", aggressive=False,
                                    native_fallback=False, progress=None) -> PreparedContext:
        from app.services.context.summarizer import compaction_summarizer
        from app.services.context.message_groups import split_checkpoint
        started = perf_counter()
        degraded = "COMPACTION_NATIVE_UNAVAILABLE" if native_fallback else None
        context_message_adapter.normalize_session(session)
        original = copy.deepcopy(session.messages)
        visible_ids = {message.get("message_id") for message in messages if message.get("message_id")}
        has_unidentified_history = any(message.get("role") != "system" and not message.get("message_id") for message in messages)
        working = [message for message in original if has_unidentified_history or message.get("message_id") in visible_ids]
        self._persist_session(session)
        state = self._refresh_state_thresholds(self.state_store.get(session), str(llm_runtime.model),
                                               max_output_tokens, runtime=llm_runtime)
        prefix = self._system_prefix(messages)
        before = self._estimate_request(prefix + working, tools, state, runtime=llm_runtime)
        self._validate_or_raise(context_message_adapter.to_api_messages(prefix + working), state, before)
        if cause == "user_command" and before <= state.compact_target_tokens:
            latest_audit = session.compaction_audit[-1] if session.compaction_audit else {}
            anchor = self._compaction_anchor(working)
            if anchor and latest_audit.get("after_message_id") == anchor.get("message_id"):
                raise ContextOverflowError("上下文已压缩，暂时没有新历史需要处理。",
                                           state=state, token_estimate=before, reason="no_progress")
            latest_user = latest_user_message(working)
            if latest_user and not any(m.get("role") in {"user", "assistant", "tool"}
                    and not m.get("is_boundary_marker") and m.get("turn_index", 0) < latest_user.get("turn_index", 0)
                    for m in working):
                raise ContextOverflowError("只有当前轮次，暂时没有可压缩的历史。",
                                           state=state, token_estimate=before, reason="no_progress")
        if cause == "provider_overflow" and not native_fallback:
            if state.reactive_retry_count >= self.config.reactive_max_retries:
                raise ContextOverflowError("上下文溢出恢复次数已达上限", state=state, token_estimate=before, reason="retry_limit")
            state.reactive_retry_count += 1
            self.state_store.save(session, state)
        used_native = settings.context_compaction_model_enabled and bool(
            (getattr(llm_runtime, "sampling", None) or {}).get("native_compaction"))
        # 客户端检查点不把旧预算快照当作任务事实；原生 compact 的完整窗口交给服务处理。
        source_window = working if used_native else [m for m in working if m.get("kind") != "context_budget"]
        keep_recent = self.config.keep_recent_turns_reactive if aggressive else self.config.keep_recent_turns
        candidate, affected = self._prune_history(source_window, keep_recent=keep_recent, runtime=llm_runtime, tools=tools)
        affected += len(working) - len(source_window)
        used_summary = False
        audit_summary = None
        strategy = "tool_result_truncate"
        latest = latest_user_message(working)
        pinned = self._pinned_messages(working)
        if not used_native:
            pinned = [*self._inherited_rules(working, pinned), *pinned]
        pinned_estimate = self._estimate_request(prefix + pinned, tools, state, runtime=llm_runtime)
        if pinned_estimate > state.effective_window or not state.effective_window:
            logger.info("Context compaction blocked: cause=%s reason=pinned_over_budget input=%d budget=%d",
                        cause, pinned_estimate, state.effective_window)
            raise self._pinned_overflow(state, pinned_estimate)
        ceiling = state.blocking_limit
        if cause == "provider_overflow":
            ceiling = min(ceiling, max(pinned_estimate, int(before * 0.85)))
        target = min(ceiling, max(pinned_estimate, state.compact_target_tokens))

        def estimate(items):
            return self._estimate_request(prefix + items, tools, state, runtime=llm_runtime)

        def archive_fallback(code=None):
            return self._compact_to_fit(session=session, llm_runtime=llm_runtime, messages=messages,
                turn_index=turn_index, tools=tools, max_output_tokens=max_output_tokens, cause=cause, state=state,
                degraded=code, started=started)

        if used_native:
            from app.services.llm_client import llm_client
            traced_runtime = replace(llm_runtime, trace_context={"session_id": session.session_id,
                "run_id": session.current_run_id(), "purpose": "native_compaction", "context_epoch": session.context_epoch})
            try:
                groups = api_groups(working)
                cut = next((i for i, group in enumerate(groups) if any(item is latest for item in group)), len(groups))
                recent = [item for group in groups[cut:] for item in group]
                native_source = [item for group in groups[:cut] for item in group if item not in pinned]
                if not native_source:
                    raise ContextOverflowError("当前任务没有可供原生压缩的历史", state=state, token_estimate=before)
                if progress:
                    progress("summarizing")
                async with asyncio.timeout(20):
                    payload = await llm_client.compact_native(traced_runtime, context_message_adapter.to_api_messages(prefix + native_source))
            except (ValueError, RuntimeError, TimeoutError, httpx.HTTPError):
                # 原生 compact 不可用或暂时失败时，用已归档来源走客户端检查点，不中断任务。
                fallback = replace(llm_runtime, sampling={**llm_runtime.sampling, "native_compaction": False})
                return await self.compact_session_async(session=session, llm_runtime=fallback,
                    messages=messages, turn_index=turn_index, tools=tools, max_output_tokens=max_output_tokens,
                    cause=cause, aggressive=True, native_fallback=True, progress=progress)
            payload["system_rules"] = history_system_rules(native_source)
            checkpoint = context_message_adapter.make_assistant_message(content="原生上下文检查点", provider_payload=payload, turn_index=turn_index)
            checkpoint["is_compact_summary"] = True
            checkpoint["visible_in_transcript"] = False
            checkpoint["compression_meta"] = {"source_message_ids": [m.get("message_id") for m in native_source]}
            audit_summary = "原生压缩检查点：服务未提供可读摘要；原始对话仍保留在归档中。"
            readable = []
            for item in payload.get("items", []):
                if item.get("type") != "compaction":
                    continue
                summary_parts = item.get("summary") or []
                if isinstance(summary_parts, str):
                    readable.append(summary_parts)
                elif isinstance(summary_parts, list):
                    readable.extend(part["text"] for part in summary_parts if isinstance(part, dict) and isinstance(part.get("text"), str))
            if readable:
                audit_summary = "\n".join(readable)
            candidate, affected = [checkpoint, *[m for m in pinned if m not in recent], *recent], len(native_source)
            strategy = "native_checkpoint"
        elif not affected or estimate(candidate) > target or getattr(llm_runtime, "api_format", "openai-compatible") != "openai-compatible":
            tail_ratio, tail_cap = (0.20, 6000) if aggressive else (0.35, 12000)
            older, tail = split_checkpoint(candidate, self.estimator,
                max(1, min(tail_cap, int(before * tail_ratio), int(state.effective_window * tail_ratio))))
            pinned = [item for item in pinned if item is not latest]
            older = [message for message in older if message not in pinned]
            tail = [message for message in tail if message not in pinned]
            cut_turn = min((int(m.get("turn_index", 0)) for m in tail), default=turn_index)
            if not older:
                return archive_fallback()
            # 摘要不能切开同一工具调用组。
            older_api = context_message_adapter.to_api_messages(older)
            if not context_integrity_validator.validate(older_api).ok:
                raise ContextIntegrityError("压缩边界切开了未完成的工具循环")
            strategy = "structured_checkpoint"
            audit_summary = self._handoff(older)
            checkpoint = context_message_adapter.make_summary_message(content=audit_summary, turn_index=cut_turn - 1,
                compression_meta={"strategy": strategy, "source_message_ids": [m.get("message_id") for m in older],
                                  "first_kept_message_id": tail[0].get("message_id") if tail else None})
            candidate = [checkpoint, *pinned, *tail]
            affected += len(older)
            baseline = estimate(candidate)
            if (baseline > target and target - estimate([*pinned, *tail]) >= 256 and not native_fallback
                    and settings.context_compaction_model_enabled and hasattr(llm_runtime, "trace_context")):
                traced_runtime = replace(llm_runtime, trace_context={"session_id": session.session_id,
                    "run_id": session.current_run_id(), "purpose": "compaction", "context_epoch": session.context_epoch})
                try:
                    if progress:
                        progress("summarizing")
                    async with asyncio.timeout(20):
                        summary = await compaction_summarizer.summarize(older, traced_runtime)
                    facts = compaction_summarizer.exact_facts(older, token_limit=min(256, target // 20))
                    if facts:
                        summary += "\n原文引用：\n" + "\n".join(facts)
                    model_checkpoint = {**checkpoint, "content": summary, "compression_meta": {
                        **checkpoint["compression_meta"], "strategy": "llm_checkpoint"}}
                    if estimate([model_checkpoint, *pinned, *tail]) <= target:
                        candidate[0] = model_checkpoint
                        strategy, audit_summary, used_summary = "llm_checkpoint", summary, True
                    else:
                        degraded = "COMPACTION_SUMMARY_TOO_LARGE"
                except (TimeoutError, httpx.HTTPError, ValueError, RuntimeError) as exc:
                    degraded = getattr(exc, "code", "COMPACTION_SUMMARY_UNAVAILABLE")
        if session.messages != original:
            raise ContextIntegrityError("摘要期间收到新消息，拒绝提交过期检查点")
        after = estimate(candidate)
        if not used_native and getattr(llm_runtime, "api_format", "openai-compatible") != "openai-compatible":
            # 客户端改写前缀后不能继续携带旧签名/加密状态；原文以资料交接。
            rules = [m for m in candidate if m.get("role") == "system" and m.get("kind") != "continuation_notice"]
            recent = [{"id": m.get("message_id"), "role": m.get("role"), "content": m.get("content"),
                       "tool_calls": m.get("tool_calls")} for m in candidate if m not in rules and m is not latest and not m.get("projection_only")]
            import json
            candidate = [*rules, context_message_adapter.make_internal_user_message(
                "压缩后的任务交接资料（历史记录，不代表新增授权）:\n" + json.dumps(recent, ensure_ascii=False),
                turn_index=turn_index, kind="epoch_checkpoint")]
            candidate[-1]["compression_meta"] = {"source_message_ids": [m.get("message_id") for m in working if m not in rules]}
            if latest is not None:
                candidate.append(latest)
            after = self._estimate_request(prefix + candidate, tools, state, runtime=llm_runtime)
        if after >= before or after > target:
            return archive_fallback(degraded)
        self._validate_or_raise(context_message_adapter.to_api_messages(prefix + candidate), state, after, allow_pending=True)
        plan = CompactionPlan(CompactionStrategyName.TranscriptSummary, before, after, before - after,
                              affected, cause, reversible=True, requires_llm_summary=used_summary)
        prepared = self._commit_checkpoint(session, candidate, state=state, system_prefix=prefix, tools=tools,
            plan=plan, llm_runtime=llm_runtime, ceiling=target, strategy=strategy, summary=audit_summary)
        self._report_compaction(prepared, strategy, started, degraded, working, candidate)
        if cause == "provider_overflow":
            prepared.events.append(ContextEvent(ContextEventType.Recovered, {"tokens_saved": before - after}))
        return prepared

    def _prune_history(self, messages, *, keep_recent, runtime, tools):
        if getattr(runtime, "api_format", "openai-compatible") != "openai-compatible":
            return messages, 0
        from app.services.context.model_profile import resolve_model_profile
        replay_required = bool(tools and hasattr(runtime, "base_url")
                               and resolve_model_profile(runtime).requires_reasoning(runtime, tools))
        recent_turns = sorted({int(m.get("turn_index", 0)) for m in messages})
        cutoff = recent_turns[-min(len(recent_turns), max(1, keep_recent))] if recent_turns else 0
        seen_reads = set()
        replacements = {}
        for group in reversed(api_groups(messages)):
            protected = any(int(m.get("turn_index", 0)) >= cutoff for m in group)
            calls = {call["id"]: call.get("function") or {} for message in group for call in message.get("tool_calls") or []}
            for message in reversed(group):
                if message.get("projection_only") or (message.get("compression_meta") or {}).get("truncated"):
                    continue
                updated = dict(message)
                if message.get("role") == "assistant" and not protected and not replay_required:
                    # 已闭合的旧轮可释放推理正文；当前工具循环保持原样。
                    updated.pop("reasoning_content", None)
                    updated.pop("reasoning", None)
                    if isinstance(updated.get("blocks"), list):
                        updated["blocks"] = [b for b in updated["blocks"] if b.get("kind") != "reasoning"]
                elif message.get("role") == "tool":
                    call = calls.get(message.get("tool_call_id"), {})
                    name = call.get("name") or message.get("tool_name") or "tool"
                    args = str(call.get("arguments") or "")
                    key = (name, args)
                    content = str(message.get("content") or "")
                    repeatable = name in {"read_file", "file_read", "sandbox_read", "sandbox_shell", "terminal", "exec_command"}
                    duplicate = repeatable and key in seen_reads
                    if repeatable:
                        seen_reads.add(key)
                    if not protected and (duplicate or len(content) > 900):
                        lines = content.count("\n") + 1
                        error = next((line.strip()[:140] for line in content[:4096].splitlines()
                                      if any(word in line.casefold() for word in ("error", "failed", "exception"))), "")
                        receipt = (f"[历史工具回执] {name} {args[:120]}；"
                                   f"结果{lines}行；message_id={message.get('message_id')}；"
                                   "使用 conversation_read 读取归档原文。")
                        if message.get("tool_status"):
                            receipt += f" 状态：{message['tool_status']}。"
                        if isinstance(message.get("tool_elapsed_ms"), int):
                            receipt += f" 耗时：{message['tool_elapsed_ms']} ms。"
                        if error:
                            receipt += f" 错误片段：{error}"
                        if not duplicate and lines > 1:
                            receipt += f"\n{content[:120]}\n…\n{content[-120:]}"
                        if len(receipt) < len(content):
                            updated["content"] = receipt
                if updated != message:
                    updated["compression_meta"] = {**(message.get("compression_meta") or {}),
                                                   "strategy": "history_prune", "truncated": True}
                    replacements[id(message)] = updated
        return [replacements.get(id(m), m) for m in messages], len(replacements)

    def _handoff(self, messages):
        from app.services.context.summarizer import compaction_summarizer
        sections = {"目标与约束": [], "进度与待办原文": [], "工具证据": []}
        for message in reversed(messages):
            if (message.get("projection_only") or message.get("is_boundary_marker")
                    or message.get("kind") in {"context_budget", "skill_context", "continuation_notice"}):
                continue
            role = message.get("role")
            section = {"user": "目标与约束", "assistant": "进度与待办原文", "tool": "工具证据"}.get(role)
            if not section or len(sections[section]) >= 3:
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            excerpt = content.strip()
            if len(excerpt) > 220:
                excerpt = excerpt[:140] + " … " + excerpt[-70:]
            sections[section].append(f"- {message.get('message_id')} {excerpt}")
        facts = compaction_summarizer.exact_facts(messages)
        return "\n".join(["历史任务交接。下列摘录仅供参考，完整记录可按 message_id 查阅。",
            *(f"【{name}】\n" + "\n".join(reversed(entries)) for name, entries in sections.items() if entries),
            *([f"【原文引用】\n" + "\n".join(facts)] if facts else [])])

    @staticmethod
    def _report_compaction(prepared, strategy, started, degraded, original, candidate):
        payload = prepared.events[0].payload
        retained = {m.get("message_id"): m for m in candidate}
        affected = {}
        for item in original:
            if retained.get(item.get("message_id")) != item:
                kind = item.get("kind") or item.get("role") or "unknown"
                affected[kind] = affected.get(kind, 0) + 1
        payload.update(compaction_strategy=strategy, degraded=bool(degraded),
                       elapsed_ms=round((perf_counter() - started) * 1000), message_types_affected=affected)
        if degraded:
            payload["error_code"] = degraded
        logger.info("Context compaction: %s", payload)

    def _pinned_messages(self, messages):
        latest = latest_user_message(messages)
        return [message for message in messages if message is latest or (
            message.get("role") == "system" and not message.get("is_boundary_marker")
            and message.get("kind") not in {"context_budget", "continuation_notice", "compact_summary"}
        )]

    @staticmethod
    def _inherited_rules(messages, pinned):
        seen = [item.get("content") for item in pinned if item.get("role") == "system"]
        inherited = []
        for rule in history_system_rules([m for m in messages if (m.get("provider_payload") or {}).get("complete_window")]):
            if rule["content"] not in seen:
                inherited.append(context_message_adapter.ensure_runtime_metadata(rule))
                seen.append(rule["content"])
        return inherited

    @staticmethod
    def _pinned_overflow(state, estimate):
        return ContextOverflowError("当前消息与系统指令、工具定义合计超过可用输入预算，请拆分或精简当前消息。",
                                    state=state, token_estimate=estimate, reason="pinned_over_budget")

    def _compact_to_fit(self, *, session, llm_runtime, messages, turn_index, tools=None,
                        max_output_tokens=None, cause, state=None, degraded=None, started=None):
        state = self._refresh_state_thresholds(state or self.state_store.get(session), llm_runtime.model,
                                               max_output_tokens, runtime=llm_runtime)
        original = session.messages
        visible_ids = {item.get("message_id") for item in messages if item.get("message_id")}
        unidentified = any(item.get("role") != "system" and not item.get("message_id") for item in messages)
        working = original if unidentified else [item for item in original if item.get("message_id") in visible_ids]
        prefix = self._system_prefix(messages)
        pinned = self._pinned_messages(working)
        inherited_rules = self._inherited_rules(working, pinned)

        def estimate(items):
            return self._estimate_request(prefix + items, tools, state, runtime=llm_runtime)

        before, minimum = estimate(working), estimate(inherited_rules + pinned)
        if not state.effective_window or minimum > state.effective_window:
            raise self._pinned_overflow(state, minimum)

        ceiling = state.effective_window
        if cause == "provider_overflow":
            ceiling = min(ceiling, max(minimum, int(before * 0.85)))
        target = min(ceiling, max(minimum, state.compact_target_tokens))
        groups = api_groups(working)
        recent_turns = sorted({int(item.get("turn_index", 0)) for item in working if not item.get("is_boundary_marker")})
        pinned_ids = {id(item) for item in pinned}
        best = None
        native = getattr(llm_runtime, "api_format", "openai-compatible") != "openai-compatible"
        shortened, _ = self._prune_history(working, keep_recent=1, runtime=llm_runtime, tools=tools)
        replacements = {id(item): updated for item, updated in zip(working, shortened)}
        for keep in ((0,) if native else (self.config.keep_recent_turns, 1, 0)):
            threshold = recent_turns[-min(keep, len(recent_turns))] if keep and recent_turns else float("inf")
            retained = pinned_ids | {
                id(item) for group in groups
                if any(int(item.get("turn_index", 0)) >= threshold for item in group)
                for item in group
            }
            # 原生签名依赖旧前缀。归档回退保留原文用户输入和系统规则，历史仍可查询。
            candidate = inherited_rules + [replacements[id(item)] for item in working if id(item) in retained]
            after = estimate(candidate)
            omitted = [item for item in working if id(item) not in retained and not item.get("is_boundary_marker")]
            checkpoint = None
            if omitted and after < target:
                summary = self._handoff(omitted)
                option = context_message_adapter.make_summary_message(content=summary, turn_index=turn_index,
                    compression_meta={"strategy": "archive_checkpoint"})
                with_handoff = estimate([option, *candidate])
                if with_handoff <= target and with_handoff < before:
                    checkpoint, after = option, with_handoff
            if after < before and after <= ceiling and context_integrity_validator.validate(
                context_message_adapter.to_api_messages(prefix + candidate)
            ).ok:
                if best is None or after < best[1]:
                    best = (retained, after, checkpoint)
                if after <= target:
                    break
        if best is None:
            logger.info("Context compaction unchanged: cause=%s reason=%s input=%d budget=%d",
                        cause, degraded or "no_progress", before, ceiling)
            raise ContextOverflowError("没有可进一步缩减的历史，请拆分当前输入或调整模型限额。",
                                       state=state, token_estimate=before, reason=degraded or "no_progress")
        retained, after, checkpoint = best
        # 每段被移出的历史单独保留原位置，审计回放顺序与压缩前一致。
        candidate, projected, archived = list(inherited_rules), list(inherited_rules), []
        if checkpoint:
            candidate.append(checkpoint)
            projected.append(checkpoint)
        working_ids = {id(item) for item in working}
        for item in [*original, None]:
            if item is not None and id(item) in working_ids and id(item) not in retained:
                archived.append(item["message_id"])
                continue
            if archived:
                candidate.append(context_message_adapter.make_boundary_message(
                    turn_index=turn_index, compression_meta={"source_message_ids": archived}))
                archived = []
            if item is not None:
                kept = replacements.get(id(item), item)
                candidate.append(kept)
                if id(item) in retained:
                    projected.append(kept)
        affected = sum(id(item) not in retained or replacements[id(item)] is not item for item in working)
        plan = CompactionPlan(CompactionStrategyName.TranscriptSummary, before, after, before - after,
                              affected, cause, reversible=True)
        prepared = self._commit_checkpoint(session, candidate, state=state, system_prefix=prefix, tools=tools,
            plan=plan, llm_runtime=llm_runtime, working_messages=projected, ceiling=max(after, target),
            strategy="archive_checkpoint", summary=(checkpoint or {}).get("content")
            or "为适应输入预算收缩了近期窗口，移出的原文仍保留在会话归档。")
        self._report_compaction(prepared, "archive_checkpoint", started or perf_counter(), degraded, working, projected)
        if cause == "provider_overflow":
            prepared.events.append(ContextEvent(ContextEventType.Recovered, {"tokens_saved": plan.estimated_tokens_saved}))
        return prepared

    def _commit_checkpoint(self, session, candidate, *, state, system_prefix, tools, plan,
                           llm_runtime, ceiling, strategy, summary, working_messages=None):
        original, epoch, revision = session.messages, session.context_epoch, session.ledger_revision
        previous_state, audit_length = dict(session.context_state), len(session.compaction_audit)
        session.messages = candidate
        if getattr(llm_runtime, "api_format", "openai-compatible") != "openai-compatible":
            session.context_epoch += 1
        state.compaction_count += 1
        state.last_compaction_at = utc_now_iso()
        state.last_compaction_strategy = strategy
        state.consecutive_compaction_failures = 0
        prepared = None
        try:
            prepared = self._build_prepared_context(session=session, state=state, system_prefix=system_prefix,
                tools=tools, plan=plan, cause=plan.reason, llm_runtime=llm_runtime,
                working_messages=working_messages, ceiling=ceiling, persist_state=False)
            self._record_compaction(session, original, before=plan.estimated_tokens_before,
                after=plan.estimated_tokens_after, cause=plan.reason, strategy=strategy, summary=summary)
            self.state_store.save(session, state)
        except Exception as exc:
            if prepared is None or session.ledger_revision == revision:
                session.messages, session.context_epoch = original, epoch
                session.context_state = previous_state
                del session.compaction_audit[audit_length:]
                if prepared is not None and isinstance(exc, (RuntimeError, OSError, sqlite3.Error)):
                    raise ContextIntegrityError("会话账本提交失败，压缩检查点未生效") from exc
                raise
            # 账本已提交，兼容 JSON 投影写失败不回滚已经生效的检查点。
            logger.warning("Context checkpoint committed but its metadata projection failed", exc_info=True)
        return prepared

    def compact_session(
        self,
        *,
        session: AgentSession,
        llm_runtime: Any,
        messages: list[dict[str, Any]],
        turn_index: int,
        tools: list[dict[str, Any]] | None = None,
        max_output_tokens: int | None = None,
        cause: str,
        aggressive: bool = False,
    ) -> PreparedContext:
        """执行渐进式上下文压缩，直到达到目标预算或确认不可继续缩减。"""
        state = self._refresh_state_thresholds(self.state_store.get(session), str(llm_runtime.model), max_output_tokens, runtime=llm_runtime)
        # 先归档原始数据，再生成任何有损的请求视图。
        self._persist_session(session)
        original = copy.deepcopy(session.messages)
        system_prefix = self._system_prefix(messages)
        token_before = self._estimate_request(system_prefix + session.messages, tools, state, runtime=llm_runtime)
        messages_affected = 0
        last_strategy = CompactionStrategyName.ToolResultTruncate
        has_progress = False

        # 逐步增强策略：先无损截断大工具结果，再摘要旧历史，最后收紧保护窗口。
        steps: list[tuple[CompactionStrategyName, int, bool]] = [
            (CompactionStrategyName.ToolResultTruncate, self.config.keep_recent_turns, False),
            (CompactionStrategyName.TranscriptSummary, self.config.keep_recent_turns, False),
            (CompactionStrategyName.ToolResultTruncate, self.config.keep_recent_turns_reactive, True),
            (CompactionStrategyName.TranscriptSummary, self.config.keep_recent_turns_reactive, True),
        ]
        if aggressive:
            steps = list(reversed(steps))

        for strategy, keep_recent_turns, is_aggressive in steps:
            current_estimate = self._estimate_request(system_prefix + session.messages, tools, state, runtime=llm_runtime)
            # Provider 溢出时本地估算可能仍低于目标，因此必须先完成一次有效压缩；
            # 已产生进展后，达到目标即停止，避免过度丢弃近期上下文。
            if has_progress and current_estimate <= state.compact_target_tokens:
                break

            if strategy == CompactionStrategyName.ToolResultTruncate:
                candidate, affected = self._truncate_tool_results(
                    session.messages,
                    keep_recent_turns=keep_recent_turns,
                    aggressive=is_aggressive,
                )
            else:
                candidate = self._summarize_older_turns(
                    session.messages,
                    turn_index=turn_index,
                    keep_recent_turns=keep_recent_turns,
                    strategy=strategy,
                )
                affected = max(0, len(session.messages) - len(candidate)) if candidate is not None else 0

            if candidate is None or affected == 0:
                continue
            candidate_estimate = self._estimate_request(system_prefix + candidate, tools, state, runtime=llm_runtime)
            if candidate_estimate >= current_estimate:
                continue

            session.messages = candidate
            messages_affected += affected
            last_strategy = strategy
            has_progress = True
            self._persist_session(session)

        token_after = self._estimate_request(system_prefix + session.messages, tools, state, runtime=llm_runtime)
        if messages_affected == 0 or token_after >= token_before:
            state.consecutive_compaction_failures += 1
            self.state_store.save(session, state)
            raise ContextOverflowError(
                f"context compaction could not reduce usage ({cause})",
                state=state,
                token_estimate=token_after,
            )

        state.compaction_count += 1
        state.consecutive_compaction_failures = 0
        state.last_compaction_at = utc_now_iso()
        state.last_compaction_strategy = last_strategy.value
        plan = CompactionPlan(
            strategy=last_strategy,
            estimated_tokens_before=token_before,
            estimated_tokens_after=token_after,
            estimated_tokens_saved=max(0, token_before - token_after),
            messages_affected=messages_affected,
            reason=f"forced context compaction ({cause})",
            reversible=True,
        )
        self._record_compaction(session, original, before=token_before, after=token_after, cause=cause,
            strategy=last_strategy.value,
            summary=next((str(m.get("content") or "") for m in session.messages if m.get("is_compact_summary")), None))
        return self._build_prepared_context(
            session=session,
            state=state,
            system_prefix=system_prefix,
            tools=tools,
            plan=plan,
            cause=cause,
            llm_runtime=llm_runtime,
        )

    def recover_from_overflow(
        self,
        *,
        session: AgentSession,
        llm_runtime: Any,
        messages: list[dict[str, Any]],
        turn_index: int,
        error_message: str,
        tools: list[dict[str, Any]] | None = None,
        max_output_tokens: int | None = None,
    ) -> PreparedContext:
        state = self._refresh_state_thresholds(self.state_store.get(session), str(llm_runtime.model), max_output_tokens, runtime=llm_runtime)
        if state.reactive_retry_count >= self.config.reactive_max_retries:
            raise ContextOverflowError(
                f"reactive compaction retries exhausted: {error_message}",
                state=state,
                token_estimate=state.last_known_token_estimate,
            )
        state.reactive_retry_count += 1
        recovered = self.compact_session(
            session=session,
            llm_runtime=llm_runtime,
            messages=messages,
            turn_index=turn_index,
            tools=tools,
            max_output_tokens=max_output_tokens,
            cause="provider_overflow",
            aggressive=True,
        )
        recovered.events.append(
            ContextEvent(
                type=ContextEventType.Recovered,
                payload={"error": error_message, "tokens_saved": recovered.plan.estimated_tokens_saved if recovered.plan else 0},
            )
        )
        return recovered

    def update_api_usage(self, session: AgentSession, usage: dict[str, int], *, runtime: Any = None) -> ContextSessionState:
        state = self.state_store.get(session)
        state.last_api_usage = dict(usage)
        from app.services.context.model_profile import deployment_profile
        semantics = deployment_profile(runtime).get("usage_semantics", "auto") if runtime is not None else "auto"
        if semantics == "auto" and runtime is not None:
            semantics = "anthropic" if runtime.api_format == "anthropic-messages" else "openai"
        state.last_usage_semantics = semantics
        actual_prompt_tokens = int(
            usage.get("prompt_tokens")
            or (
                usage.get("input_tokens", 0)
                + (usage.get("cache_creation_input_tokens", 0) if semantics != "openai" else 0)
                + (usage.get("cache_read_input_tokens", 0) if semantics != "openai" else 0)
            )
        )
        # 使用真实 API 用量校准本地启发式估算，避免连续低估直至请求失败。
        if actual_prompt_tokens > 0 and state.last_heuristic_token_estimate > 0:
            state.token_calibration_factor = min(3.0, max(0.5, actual_prompt_tokens / state.last_heuristic_token_estimate))
        state.last_known_token_estimate = actual_prompt_tokens or self.estimator.count_from_usage(usage)
        state.percent_used = self._safe_percent(state.last_known_token_estimate, state.effective_window)
        self.state_store.save(session, state)
        return state

    def reset_reactive_retry(self, session: AgentSession) -> None:
        state = self.state_store.get(session)
        state.reactive_retry_count = 0
        self.state_store.save(session, state)

    def _truncate_tool_results(
        self,
        messages: list[dict[str, Any]],
        *,
        keep_recent_turns: int,
        aggressive: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        if not messages:
            return messages, 0

        max_turn = max(int(message.get("turn_index", 0)) for message in messages)
        protected_turn = max(0, max_turn - keep_recent_turns + 1)
        threshold = self.config.tool_result_token_threshold if not aggressive else int(self.config.tool_result_token_threshold * 0.6)
        max_chars = self.config.tool_result_max_chars if not aggressive else int(self.config.tool_result_max_chars * 0.5)

        changed = 0
        result: list[dict[str, Any]] = []
        for message in messages:
            if message.get("role") != "tool":
                result.append(message)
                continue
            if int(message.get("turn_index", 0)) >= protected_turn:
                result.append(message)
                continue

            token_count = self.estimator.estimate_message_tokens(message)
            content = message.get("content", "")
            if token_count < threshold:
                result.append(message)
                continue

            truncated = self._truncate_tool_content(content, max_chars=max_chars)
            if truncated != content:
                truncated = (f"[历史工具结果已归档 message_id={message.get('message_id', '')}; "
                             "使用 conversation_read 读取完整结果]\n" + str(content)[:max_chars // 2]
                             + "\n...\n" + str(content)[-max_chars // 2:])
            if truncated == content:
                result.append(message)
                continue

            changed += 1
            result.append(
                context_message_adapter.ensure_runtime_metadata(
                    {
                        **message,
                        "content": truncated,
                        "compression_meta": {
                            **(message.get("compression_meta") or {}),
                            "strategy": CompactionStrategyName.ToolResultTruncate.value,
                            "truncated": True,
                            "original_token_estimate": token_count,
                        },
                    },
                    turn_index=int(message.get("turn_index", 0)),
                    kind=str(message.get("kind", "tool_result")),
                )
            )
        return result, changed

    def _truncate_tool_content(self, content: Any, *, max_chars: int) -> str:
        if isinstance(content, str):
            if len(content) <= max_chars:
                return content
            return truncate_tool_result(content, max_chars)
        return truncate_json_content(content, max_chars)

    def _summarize_older_turns(
        self,
        messages: list[dict[str, Any]],
        *,
        turn_index: int,
        keep_recent_turns: int,
        strategy: CompactionStrategyName,
    ) -> list[dict[str, Any]] | None:
        if len(messages) < self.config.minimum_messages_for_summary:
            return None

        max_turn = max(int(message.get("turn_index", 0)) for message in messages)
        protected_turn = max(1, max_turn - keep_recent_turns + 1)
        latest = latest_user_message(messages)

        def critical(message: dict[str, Any]) -> bool:
            # 系统规则、既有压缩边界和摘要是后续执行的安全基线，任何时候都不能被摘要掉。
            return (
                message is latest or message.get("role") == "system"
                or bool(message.get("is_boundary_marker"))
            )

        summarize_messages = [
            message
            for message in messages
            if int(message.get("turn_index", 0)) < protected_turn and not critical(message)
        ]
        keep_messages = [
            message
            for message in messages
            if int(message.get("turn_index", 0)) >= protected_turn or critical(message)
        ]
        if len(summarize_messages) < self.config.minimum_messages_for_summary:
            return None

        lines: list[str] = []
        for message in summarize_messages:
            if message.get("role") == "tool":
                lines.append(f"工具结果已归档 message_id={message.get('message_id', '')}；用 conversation_read 读取原文")
            else:
                # 同步兼容入口只合并原文，不以截头文本冒充语义摘要。
                lines.append(f"{message.get('role')}: {message.get('content') or ''}")
                if message.get("tool_calls"):
                    lines.append("工具调用: " + str(message["tool_calls"]))

        tokens_before = self.estimator.estimate_messages_tokens(messages)
        boundary_meta = {
            "strategy": strategy.value,
            "messages_summarized": len(summarize_messages),
            "tokens_before": tokens_before,
            "tokens_after": 0,
        }
        boundary = context_message_adapter.make_boundary_message(
            turn_index=turn_index,
            compression_meta=boundary_meta,
        )
        summary = context_message_adapter.make_summary_message(
            turn_index=turn_index,
            content="<conversation_summary>\n"
            + "\n".join(lines)
            + "\n</conversation_summary>\n\nContinue from this summarized context.",
            compression_meta={
                "strategy": strategy.value,
                "source_message_ids": [message.get("message_id") for message in summarize_messages],
            },
        )
        compacted_messages = [boundary, summary, *keep_messages]
        tokens_after = self.estimator.estimate_messages_tokens(compacted_messages)
        boundary["compression_meta"]["tokens_after"] = tokens_after
        return compacted_messages

    def _refresh_state_thresholds(
        self,
        state: ContextSessionState,
        model: str,
        max_output_tokens: int | None = None,
        *,
        runtime: Any = None,
    ) -> ContextSessionState:
        state.model_id = model
        from app.services.llm_client import llm_client
        if runtime is not None and hasattr(runtime, "api_format"):
            state.context_window, state.budget_source = llm_client.context_limits(runtime, self.budget.models_registry)
            output = max_output_tokens or llm_client.output_limit(runtime)
        else:
            state.context_window = self.budget.get_context_window_for_model(model)
            state.budget_source = "catalog_or_fallback"
            output = max_output_tokens or self.budget.config.max_output_tokens_for_summary
        state.output_reserve = output
        state.effective_window = input_budget(state.context_window, output, self.budget.config.provider_safety_tokens)
        state.warning_threshold = int(state.effective_window * self.budget.config.warning_ratio)
        state.blocking_limit = state.effective_window
        state.error_threshold = state.blocking_limit
        state.compact_target_tokens = int(state.effective_window * self.budget.config.compact_target_ratio)
        state.target_input_tokens = state.compact_target_tokens
        return state

    def inspect_status(self, session, resolve_runtime) -> dict:
        """读取最近预算快照。首次请求前只展示配置预算，使用量保留为空。"""
        state = self.state_store.get(session)
        has_estimate = bool(state.model_id)
        if not has_estimate:
            runtime = resolve_runtime()
            self._refresh_state_thresholds(state, str(runtime.model), runtime=runtime)
        reserve = state.output_reserve
        if reserve is None and state.effective_window > 0:
            reserve = max(0, state.context_window - state.effective_window - self.budget.config.provider_safety_tokens)
        audit = session.compaction_audit[-1] if session.compaction_audit else None
        return {
            "model": state.model_id,
            "estimated_tokens": state.last_known_token_estimate if has_estimate else None,
            "input_budget": state.effective_window,
            "context_window": state.context_window,
            "output_reserve": reserve,
            "budget_source": state.budget_source,
            "last_compaction": {key: audit.get(key) for key in ("timestamp", "tokens_before", "tokens_after")} if audit else None,
        }

    def _build_status_events(self, state: ContextSessionState, token_estimate: int) -> list[ContextEvent]:
        events: list[ContextEvent] = []
        if self.config.emit_status_each_turn:
            events.append(
                ContextEvent(
                    type=ContextEventType.Status,
                    payload={
                        "model": state.model_id,
                        "estimated_tokens": token_estimate,
                        "effective_window": state.effective_window,
                        "context_window": state.context_window,
                        "budget_source": state.budget_source,
                        "target_input_tokens": state.target_input_tokens,
                        "warning_threshold": state.warning_threshold,
                        "blocking_limit": state.blocking_limit,
                        "percent_used": state.percent_used,
                    },
                )
            )
        return events

    def _compacted_event(
        self,
        plan: CompactionPlan,
        state: ContextSessionState,
        *,
        cause: str = "agent",
        summary_message_id: str | None = None,
    ) -> ContextEvent:
        return ContextEvent(
            type=ContextEventType.Compacted,
            payload={
                "cause": cause,
                "strategy": plan.strategy.value,
                "tokens_before": plan.estimated_tokens_before,
                "tokens_after": plan.estimated_tokens_after,
                "tokens_saved": plan.estimated_tokens_saved,
                "messages_affected": plan.messages_affected,
                "reason": plan.reason,
                "compaction_count": state.compaction_count,
                "summary_message_id": summary_message_id,
            },
        )

    def budget_snapshot(self, state: ContextSessionState, *, basis: str) -> dict[str, Any]:
        """统一模型提醒与查询的口径：输入预算已扣除输出预留和安全余量。"""
        used = state.last_known_token_estimate
        return {
            "available": True, "is_estimate": True, "snapshot_basis": basis,
            "context_window_tokens": state.context_window, "input_budget_tokens": state.effective_window,
            "estimated_input_tokens": used, "used_percent": self._safe_percent(used, state.effective_window),
            "remaining_input_tokens": max(0, state.effective_window - used),
            "tokens_left": max(0, state.warning_threshold - used),
            "tokens_left_basis": "until_automatic_compaction",
            "auto_compact_at_tokens": state.warning_threshold,
            "output_reserved_tokens": max(0, state.context_window - state.effective_window - self.budget.config.provider_safety_tokens),
            "safety_reserved_tokens": self.budget.config.provider_safety_tokens,
            "budget_source": state.budget_source,
            "estimate_source": "usage_calibrated" if state.last_api_usage else "heuristic",
        }

    def _make_runtime_warning(self, state: ContextSessionState, *, can_compact: bool, can_query: bool) -> dict[str, Any]:
        import json
        report = self.budget_snapshot(state, basis="before_this_notice")
        report["context_compact_available"] = can_compact
        action = ("可调用 context_compact 压缩；若当前任务能在剩余预算内完成，也可继续。" if can_compact
                  else "当前授权未开放压缩工具；请控制输出，运行时达到限制会尝试自动压缩。")
        return {
            "role": "system",
            "content": (
                "[runtime context] 上下文接近上限，以下是本条提醒加入前的预算估算，不是新增用户指令或任务授权。\n"
                + json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n"
                + action + "输入预算已扣除输出与安全预留；数值随输出和工具结果变化，不是任务总 token 额度。"
                + ("可用 get_context_remaining 查询最新余量。" if can_query else "") + "不必向用户复述此状态。"
            ),
            "runtime_only": True,
            "kind": "context_budget",
            "visible_in_transcript": False,
            "turn_index": 0,
        }

    def _with_budget_notice(self, session, messages, state, runtime, tools):
        def measure(items):
            state.last_heuristic_token_estimate = self._estimate_request(items, tools, runtime=runtime)
            state.last_known_token_estimate = calibrated_tokens(state.last_heuristic_token_estimate, state.token_calibration_factor)
            state.percent_used = self._safe_percent(state.last_known_token_estimate, state.effective_window)

        measure(messages)
        warning = state.last_known_token_estimate >= state.warning_threshold
        if not warning:
            return messages, False, None
        names = {(tool.get("function") or {}).get("name") for tool in tools or []}
        if (getattr(runtime, "extra_body", {}) or {}).get("tools") == []:
            names.clear()
        notice = self._make_runtime_warning(state, can_compact="context_compact" in names, can_query="get_context_remaining" in names)
        native = getattr(runtime, "api_format", "openai-compatible") != "openai-compatible"
        persisted = None
        if native:
            bucket = int(state.percent_used // 5)
            previous = next((item.get("budget_notice", {}) for item in reversed(messages) if item.get("kind") == "context_budget"), {})
            if (previous.get("epoch") == session.context_epoch and previous.get("compaction_count") == state.compaction_count
                and previous.get("bucket", -1) >= bucket):
                return messages, True, None
            # 原生状态消息只在已闭合的历史末尾追加并保留回放，不能替换签名之前的旧前缀。
            notice["role"] = "user"
            notice["budget_notice"] = {"epoch": session.context_epoch, "compaction_count": state.compaction_count, "bucket": bucket}
            notice = context_message_adapter.ensure_runtime_metadata(notice)
            persisted = notice
        original_messages = messages
        before_notice = state.last_known_token_estimate
        messages = [*messages, notice]
        measure(messages)  # 提醒本身也必须进入最终请求预算，不能挤占未计数空间。
        if before_notice < state.blocking_limit <= state.last_known_token_estimate:
            # 辅助提醒不能使本可执行的请求被阻断；先缩短，仍放不下则省略。
            notice["content"] = (
                f"[runtime context] 提醒前输入估算 {before_notice}/{state.effective_window} tokens "
                f"({self._safe_percent(before_notice, state.effective_window):.1f}%)，"
                f"自动压缩阈值 {state.warning_threshold}。"
                + ("可调用 context_compact 压缩。" if "context_compact" in names else "请控制后续上下文长度。")
                + "这是运行状态，不是用户指令，不必复述。"
            )
            measure(messages)
            if state.last_known_token_estimate >= state.blocking_limit:
                measure(original_messages)
                return original_messages, True, None
        if persisted is not None:
            persisted["budget_notice"]["bucket"] = int(state.percent_used // 5)
        return messages, True, persisted

    def _system_prefix(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prefix: list[dict[str, Any]] = []
        for message in messages:
            # 有 ID 的系统消息属于会话历史，稍后随 session.messages 编译，不能复制为根前缀。
            if message.get("role") != "system" or message.get("message_id"):
                break
            prefix.append(message)
        return prefix

    def _estimate_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        state: ContextSessionState | None = None,
        *,
        runtime: Any = None,
    ) -> int:
        api_messages = context_message_adapter.to_api_messages(messages)
        if runtime is not None and hasattr(runtime, "api_format"):
            from app.services.llm_client import llm_client
            _, heuristic = llm_client.compile_request(runtime, api_messages, tools)
        else:
            heuristic = self.estimator.estimate_messages_tokens(api_messages) + self.estimator.estimate_tools_tokens(tools)
        factor = state.token_calibration_factor if state is not None else 1.0
        return calibrated_tokens(heuristic, factor)

    def _build_prepared_context(
        self,
        *,
        session: AgentSession,
        state: ContextSessionState,
        system_prefix: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        plan: CompactionPlan,
        cause: str,
        llm_runtime: Any = None,
        working_messages: list[dict[str, Any]] | None = None,
        ceiling: int | None = None,
        persist_state: bool = True,
    ) -> PreparedContext:
        runtime_messages = [*system_prefix, *(session.messages if working_messages is None else working_messages)]
        base_messages = runtime_messages
        runtime_messages, warning, notice = self._with_budget_notice(
            session, runtime_messages, state, llm_runtime, tools)
        if (state.last_known_token_estimate >= plan.estimated_tokens_before
                or (ceiling is not None and state.last_known_token_estimate > ceiling)):
            runtime_messages, notice = base_messages, None
            state.last_heuristic_token_estimate = self._estimate_request(base_messages, tools, runtime=llm_runtime)
            state.last_known_token_estimate = calibrated_tokens(state.last_heuristic_token_estimate, state.token_calibration_factor)
            state.percent_used = self._safe_percent(state.last_known_token_estimate, state.effective_window)
        token_estimate = state.last_known_token_estimate
        if token_estimate > state.blocking_limit or not state.blocking_limit:
            if persist_state:
                self.state_store.save(session, state)
            raise ContextOverflowError(
                "context window exceeded after forced compaction",
                state=state,
                token_estimate=token_estimate,
            )

        plan.estimated_tokens_after = token_estimate
        plan.estimated_tokens_saved = max(0, plan.estimated_tokens_before - token_estimate)
        boundary = next((message for message in session.messages if message.get("is_boundary_marker")), None)
        summary = next((message for message in session.messages if message.get("is_compact_summary")), None)
        state.last_boundary_message_id = str(boundary.get("message_id")) if boundary else None
        api_messages = context_message_adapter.to_api_messages(runtime_messages)
        self._validate_or_raise(api_messages, state, token_estimate, allow_pending=True)
        if notice is not None:
            session.messages.append(notice)
        if persist_state:
            self.state_store.save(session, state)
        return PreparedContext(
            messages=api_messages,
            state=state,
            events=[self._compacted_event(plan, state, cause=cause, summary_message_id=str(summary.get("message_id")) if summary else None)],
            compacted=True,
            ephemeral_warning=warning,
            plan=plan,
        )

    def _validate_or_raise(
        self,
        api_messages: list[dict[str, Any]],
        state: ContextSessionState,
        token_estimate: int,
        *,
        allow_pending: bool = False,
    ) -> None:
        report = context_integrity_validator.validate(api_messages, allow_pending=allow_pending)
        if report.ok:
            return
        raise ContextIntegrityError(f"context integrity validation failed: {'; '.join(report.errors)}")

    def _safe_percent(self, token_estimate: int, effective_window: int) -> float:
        if effective_window <= 0:
            return 0.0
        return round(token_estimate / effective_window * 100, 2)

    def _persist_session(self, session: AgentSession) -> None:
        if self._persister is None:
            raise RuntimeError("ContextPipeline requires a bound SessionPersister")
        self._persister.persist(session)

    def _record_compaction(self, session, original, *, before, after, cause, strategy, summary=None) -> None:
        """审计事件与模型窗口分开保存，摘要不作为用户发言，也不重复送给模型。"""
        from app.services.context.runtime_types import new_message_id
        anchor = self._compaction_anchor(original)
        session.compaction_audit.append({
            "id": new_message_id("compaction"), "after_message_id": anchor.get("message_id") if anchor else None,
            "timestamp": utc_now_iso(), "strategy": strategy, "cause": cause,
            "tokens_before": before, "tokens_after": after, "summary": summary or "本次仅缩减历史工具结果，未生成文本摘要。",
        })

    @staticmethod
    def _compaction_anchor(messages):
        return next((m for m in reversed(messages) if m.get("message_id")
                     and m.get("kind") not in {"context_budget", "epoch_checkpoint"}
                     and not (m.get("projection_only") or m.get("is_boundary_marker") or m.get("is_compact_summary"))), None)


context_pipeline = ContextPipeline(state_store=context_state_store)
