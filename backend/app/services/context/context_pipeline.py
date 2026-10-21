# backend/app/services/context/context_pipeline.py
"""生产级上下文管线。"""

from __future__ import annotations

from dataclasses import dataclass
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
    ) -> PreparedContext:
        changed = context_message_adapter.normalize_session(session)
        if changed:
            self._persist_session(session)

        state = self.state_store.get(session)
        state = self._refresh_state_thresholds(state, str(llm_runtime.model), max_output_tokens)

        runtime_messages = [
            context_message_adapter.ensure_runtime_metadata(message, turn_index=message.get("turn_index", 0))
            for message in messages
        ]
        token_estimate = self.estimator.estimate_messages_tokens(runtime_messages)
        heuristic_estimate = token_estimate + self.estimator.estimate_tools_tokens(tools)
        token_estimate = int(heuristic_estimate * state.token_calibration_factor)
        state.last_heuristic_token_estimate = heuristic_estimate
        state.last_known_token_estimate = token_estimate
        state.percent_used = self._safe_percent(token_estimate, state.effective_window)

        events = self._build_status_events(state, state.last_known_token_estimate)

        # 警戒区不自动压缩：只给模型注入一次运行时提示，由 Agent 判断是否调用 context_compact。
        if token_estimate >= state.warning_threshold:
            runtime_messages.append(self._make_runtime_warning())
            heuristic_estimate = self.estimator.estimate_messages_tokens(runtime_messages) + self.estimator.estimate_tools_tokens(tools)
            state.last_heuristic_token_estimate = heuristic_estimate
            token_estimate = int(heuristic_estimate * state.token_calibration_factor)
            state.last_known_token_estimate = token_estimate
            state.percent_used = self._safe_percent(token_estimate, state.effective_window)

        if token_estimate >= state.blocking_limit:
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
        state = self._refresh_state_thresholds(self.state_store.get(session), str(llm_runtime.model), max_output_tokens)
        system_prefix = self._system_prefix(messages)
        token_before = self._estimate_request(system_prefix + session.messages, tools, state)
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
            current_estimate = self._estimate_request(system_prefix + session.messages, tools, state)
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
            candidate_estimate = self._estimate_request(system_prefix + candidate, tools, state)
            if candidate_estimate >= current_estimate:
                continue

            session.messages = candidate
            messages_affected += affected
            last_strategy = strategy
            has_progress = True
            self._persist_session(session)

        token_after = self._estimate_request(system_prefix + session.messages, tools, state)
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
            reversible=False,
        )
        return self._build_prepared_context(
            session=session,
            state=state,
            system_prefix=system_prefix,
            tools=tools,
            plan=plan,
            cause=cause,
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
        state = self._refresh_state_thresholds(self.state_store.get(session), str(llm_runtime.model))
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

    def update_api_usage(self, session: AgentSession, usage: dict[str, int]) -> ContextSessionState:
        state = self.state_store.get(session)
        state.last_api_usage = dict(usage)
        actual_prompt_tokens = int(
            usage.get("prompt_tokens")
            or (
                usage.get("input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
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
                or bool(message.get("is_compact_summary"))
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
            if len(lines) >= self.config.summary_max_lines:
                break
            lines.append(context_message_adapter.estimate_text_for_summary(message, max_chars=self.config.summary_line_max_chars))
        if len(summarize_messages) > len(lines):
            lines.append(f"... {len(summarize_messages) - len(lines)} more historical messages summarized")

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
    ) -> ContextSessionState:
        state.model_id = model
        state.context_window = self.budget.get_context_window_for_model(model)
        state.effective_window = self.budget.get_effective_context_window(model, max_output_tokens)
        state.warning_threshold = self.budget.get_warning_threshold(model, max_output_tokens)
        state.error_threshold = self.budget.get_error_threshold(model, max_output_tokens)
        state.blocking_limit = self.budget.get_blocking_limit(model, max_output_tokens)
        state.compact_target_tokens = self.budget.get_compact_target(model, max_output_tokens)
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
            if message.get("role") != "system":
                break
            prefix.append(message)
        return prefix

    def _estimate_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        state: ContextSessionState | None = None,
    ) -> int:
        heuristic = self.estimator.estimate_messages_tokens(messages) + self.estimator.estimate_tools_tokens(tools)
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
    ) -> PreparedContext:
        runtime_messages = [*system_prefix, *session.messages]
        warning = self._estimate_request(runtime_messages, tools, state) >= state.warning_threshold
        if warning:
            runtime_messages.append(self._make_runtime_warning())

        heuristic = self._estimate_request(runtime_messages, tools)
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
        self._validate_or_raise(api_messages, state, token_estimate)
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
    ) -> None:
        report = context_integrity_validator.validate(api_messages)
        if report.ok:
            return
        raise ContextOverflowError(
            f"context integrity validation failed: {'; '.join(report.errors)}",
            state=state,
            token_estimate=token_estimate,
        )

    def _safe_percent(self, token_estimate: int, effective_window: int) -> float:
        if effective_window <= 0:
            return 0.0
        return round(token_estimate / effective_window * 100, 2)

    def _persist_session(self, session: AgentSession) -> None:
        if self._persister is None:
            raise RuntimeError("ContextPipeline requires a bound SessionPersister")
        self._persister.persist(session)


context_pipeline = ContextPipeline(state_store=context_state_store)
