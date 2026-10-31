# backend/app/services/context/context_pipeline.py
"""生产级上下文管线。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import copy
from typing import Any

from app.services.context.contracts import SessionPersister
from app.services.context.context_budget import ContextBudget, ContextBudgetConfig
from app.services.context.context_state_store import ContextStateStore, context_state_store
from app.services.context.integrity import context_integrity_validator
from app.services.context.message_adapter import context_message_adapter
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


@dataclass
class ContextPipelineConfig:
    target_input_ratio: float = 0.65
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
        heuristic_estimate = self._estimate_request(runtime_messages, tools, runtime=llm_runtime)
        token_estimate = int(heuristic_estimate * state.token_calibration_factor)
        state.last_heuristic_token_estimate = heuristic_estimate
        state.last_known_token_estimate = token_estimate
        state.percent_used = self._safe_percent(token_estimate, state.effective_window)

        events = self._build_status_events(state, state.last_known_token_estimate)

        # 警戒区不自动压缩：只给模型注入一次运行时提示，由 Agent 判断是否调用 context_compact。
        if token_estimate >= state.warning_threshold and getattr(llm_runtime, "api_format", "openai-compatible") == "openai-compatible":
            runtime_messages.append(self._make_runtime_warning())
            heuristic_estimate = self._estimate_request(runtime_messages, tools, runtime=llm_runtime)
            state.last_heuristic_token_estimate = heuristic_estimate
            token_estimate = int(heuristic_estimate * state.token_calibration_factor)
            state.last_known_token_estimate = token_estimate
            state.percent_used = self._safe_percent(token_estimate, state.effective_window)

        if token_estimate >= state.blocking_limit:
            if not allow_compaction:
                raise ContextOverflowError("context requires compaction", state=state, token_estimate=token_estimate)
            return self.compact_session(
                session=session,
                llm_runtime=llm_runtime,
                messages=messages,
                turn_index=turn_index,
                tools=tools,
                max_output_tokens=max_output_tokens,
                cause="hard_limit",
                aggressive=True,
            )

        api_messages = context_message_adapter.to_api_messages(runtime_messages)
        ephemeral_warning = any(message.get("runtime_only") for message in runtime_messages)
        self._validate_or_raise(api_messages, state, token_estimate)
        self.state_store.save(session, state)
        return PreparedContext(
            messages=api_messages,
            state=state,
            events=events,
            ephemeral_warning=ephemeral_warning,
        )

    async def prepare_for_llm_async(self, **kwargs) -> PreparedContext:
        try:
            return self.prepare_for_llm(**kwargs, allow_compaction=False)
        except ContextOverflowError:
            return await self.compact_session_async(**kwargs, cause="hard_limit")

    async def compact_session_async(self, *, session, llm_runtime, messages, turn_index,
                                    tools=None, max_output_tokens=None, cause="agent", aggressive=False) -> PreparedContext:
        from app.services.context.summarizer import compaction_summarizer
        from app.services.context.message_groups import split_checkpoint
        context_message_adapter.normalize_session(session)
        original = copy.deepcopy(session.messages)
        visible_ids = {message.get("message_id") for message in messages if message.get("message_id")}
        has_unidentified_history = any(message.get("role") != "system" and not message.get("message_id") for message in messages)
        use_visible_ids = not has_unidentified_history and bool(visible_ids.intersection(message.get("message_id") for message in original))
        working = [message for message in original if not use_visible_ids or message.get("message_id") in visible_ids]
        self._persist_session(session)
        state = self._refresh_state_thresholds(self.state_store.get(session), str(llm_runtime.model),
                                               max_output_tokens, runtime=llm_runtime)
        prefix = self._system_prefix(messages)
        before = self._estimate_request(prefix + working, tools, state, runtime=llm_runtime)
        self._validate_or_raise(context_message_adapter.to_api_messages(prefix + working), state, before)
        if cause == "provider_overflow":
            if state.reactive_retry_count >= self.config.reactive_max_retries:
                raise ContextOverflowError("上下文溢出恢复次数已达上限", state=state, token_estimate=before)
            state.reactive_retry_count += 1
        candidate, affected = self._truncate_tool_results(working, keep_recent_turns=2, aggressive=True)
        used_native = bool((getattr(llm_runtime, "sampling", None) or {}).get("native_compaction"))
        used_summary = False
        if used_native:
            from app.services.llm_client import llm_client
            traced_runtime = replace(llm_runtime, trace_context={"session_id": session.session_id, "purpose": "native_compaction", "context_epoch": session.context_epoch})
            payload = await llm_client.compact_native(traced_runtime, context_message_adapter.to_api_messages(prefix + working))
            from app.services.context.provider_adapters import history_system_rules
            payload["system_rules"] = history_system_rules(working)
            checkpoint = context_message_adapter.make_assistant_message(content="原生上下文检查点", provider_payload=payload, turn_index=turn_index)
            checkpoint["is_compact_summary"] = True
            checkpoint["visible_in_transcript"] = False
            checkpoint["compression_meta"] = {"source_message_ids": [m.get("message_id") for m in working]}
            candidate, affected = [checkpoint], len(working)
        elif not affected or self._estimate_request(prefix + candidate, tools, state, runtime=llm_runtime) > state.compact_target_tokens:
            older, tail = split_checkpoint(candidate, self.estimator, max(1, min(12000, int(before * 0.35), int(state.effective_window * 0.35))))
            pinned = [message for message in working if message.get("role") == "system" and message.get("kind") != "continuation_notice" and not message.get("is_boundary_marker")]
            older = [message for message in older if message not in pinned]
            tail = [message for message in tail if message not in pinned]
            cut_turn = min((int(m.get("turn_index", 0)) for m in tail), default=turn_index)
            if not older:
                raise ContextOverflowError("当前任务没有可安全压缩的已完成历史", state=state, token_estimate=before)
            # 摘要不能切开同一工具调用组。
            older_api = context_message_adapter.to_api_messages(older)
            if not context_integrity_validator.validate(older_api).ok:
                raise ContextIntegrityError("压缩边界切开了未完成的工具循环")
            try:
                summary_source, _ = self._truncate_tool_results(older, keep_recent_turns=0, aggressive=True)
                traced_runtime = replace(llm_runtime, trace_context={"session_id": session.session_id, "purpose": "compaction", "context_epoch": session.context_epoch}) if hasattr(llm_runtime, "trace_context") else llm_runtime
                summary = await compaction_summarizer.summarize(summary_source, traced_runtime)
                used_summary = True
            except Exception:
                state.consecutive_compaction_failures += 1
                self.state_store.save(session, state)
                raise
            facts = compaction_summarizer.exact_facts(summary_source)
            summary += "\n\n原文事实（逐字保留）:\n" + "\n".join(facts)
            checkpoint = context_message_adapter.make_summary_message(content=summary, turn_index=cut_turn - 1,
                compression_meta={"strategy": "llm_checkpoint", "source_message_ids": [m.get("message_id") for m in older],
                                  "first_kept_message_id": tail[0].get("message_id") if tail else None})
            candidate = [checkpoint, *pinned, *tail]
            affected = len(older)
        after = self._estimate_request(prefix + candidate, tools, state, runtime=llm_runtime)
        if not used_native and getattr(llm_runtime, "api_format", "openai-compatible") != "openai-compatible":
            # 客户端改写前缀后不能继续携带旧签名/加密状态；原文以资料交接。
            rules = [m for m in candidate if m.get("role") == "system" and m.get("kind") != "continuation_notice"]
            recent = [{"id": m.get("message_id"), "role": m.get("role"), "content": m.get("content"),
                       "tool_calls": m.get("tool_calls")} for m in candidate if m not in rules and not m.get("projection_only")]
            import json
            candidate = [*rules, context_message_adapter.make_internal_user_message(
                "压缩后的任务交接资料（历史记录，不代表新增授权）:\n" + json.dumps(recent, ensure_ascii=False),
                turn_index=turn_index, kind="epoch_checkpoint")]
            candidate[-1]["compression_meta"] = {"source_message_ids": [m.get("message_id") for m in working if m not in rules]}
            after = self._estimate_request(prefix + candidate, tools, state, runtime=llm_runtime)
        if after >= before or after >= state.blocking_limit:
            raise ContextOverflowError("压缩候选未释放足够空间，原始上下文保持不变", state=state, token_estimate=after)
        if session.messages != original:
            raise ContextIntegrityError("摘要期间收到新消息，拒绝提交过期检查点")
        self._validate_or_raise(context_message_adapter.to_api_messages(prefix + candidate), state, after, allow_pending=True)
        session.messages = candidate
        if getattr(llm_runtime, "api_format", "openai-compatible") != "openai-compatible":
            session.context_epoch += 1
        state.compaction_count += 1
        state.consecutive_compaction_failures = 0
        state.last_compaction_at = utc_now_iso()
        state.last_compaction_strategy = "checkpoint"
        plan = CompactionPlan(CompactionStrategyName.TranscriptSummary, before, after, before - after,
                              affected, cause, reversible=True, requires_llm_summary=used_summary)
        self._persist_session(session)
        prepared = self._build_prepared_context(session=session, state=state, system_prefix=prefix,
                                                tools=tools, plan=plan, cause=cause, llm_runtime=llm_runtime)
        if cause == "provider_overflow":
            prepared.events.append(ContextEvent(ContextEventType.Recovered, {"tokens_saved": before - after}))
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

        def critical(message: dict[str, Any]) -> bool:
            # 系统规则、既有压缩边界和摘要是后续执行的安全基线，任何时候都不能被摘要掉。
            return (
                message.get("role") == "system"
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
        declared_window = llm_client.context_window(runtime) if runtime is not None and hasattr(runtime, "api_format") else None
        # 有明确部署声明时不查公网目录，也不先以较小目录窗口否决合法部署。
        state.context_window = int(declared_window or self.budget.get_context_window_for_model(model))
        state.budget_source = "deployment" if declared_window else "catalog_or_fallback"
        if not declared_window and runtime is not None and hasattr(runtime, "api_format"):
            registry = self.budget._get_registry()
            lookup = getattr(registry, "get_model", None)
            if callable(lookup):
                if lookup(model) is not None:
                    state.budget_source = "catalog_unverified"
                else:
                    # 未知内网别名不能继承目录的 200K 默认事实；仅采用明确标注的保守上限。
                    state.context_window = min(state.context_window, 65_536)
                    state.budget_source = "unverified_fallback"
        output = max_output_tokens or (llm_client.output_limit(runtime) if runtime is not None and hasattr(runtime, "api_format") else self.budget.config.max_output_tokens_for_summary)
        state.effective_window = state.context_window - output - self.budget.config.provider_safety_tokens
        if state.effective_window <= 0:
            raise ValueError("部署上下文窗口不足以容纳输出预留与安全余量")
        state.warning_threshold = int(state.effective_window * self.budget.config.warning_ratio)
        state.blocking_limit = int(state.effective_window * self.budget.config.hard_limit_ratio)
        state.error_threshold = state.blocking_limit
        state.compact_target_tokens = int(state.effective_window * self.budget.config.compact_target_ratio)
        state.target_input_tokens = state.compact_target_tokens
        return state

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

    def _make_runtime_warning(self) -> dict[str, Any]:
        """构造仅进入本次 LLM 请求的上下文警戒提示。"""
        return {
            "role": "system",
            "content": (
                "[runtime context] Context usage is high. You may call context_compact now. "
                "If the current task can finish within the remaining budget, you may continue without compaction. "
                "Do not mention this notice to the user."
            ),
            "runtime_only": True,
            "turn_index": 0,
        }

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
        return int(heuristic * factor)

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
    ) -> PreparedContext:
        runtime_messages = [*system_prefix, *session.messages]
        warning = self._estimate_request(runtime_messages, tools, state, runtime=llm_runtime) >= state.warning_threshold
        if warning and getattr(llm_runtime, "api_format", "openai-compatible") == "openai-compatible":
            runtime_messages.append(self._make_runtime_warning())

        heuristic = self._estimate_request(runtime_messages, tools, runtime=llm_runtime)
        token_estimate = int(heuristic * state.token_calibration_factor)
        state.last_heuristic_token_estimate = heuristic
        state.last_known_token_estimate = token_estimate
        state.percent_used = self._safe_percent(token_estimate, state.effective_window)
        if token_estimate >= state.blocking_limit:
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


context_pipeline = ContextPipeline(state_store=context_state_store)
