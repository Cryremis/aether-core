# backend/app/services/context/context_pipeline.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.services.context.context_budget import ContextBudget, ContextBudgetConfig
from app.services.context.context_state_store import context_state_store
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
if TYPE_CHECKING:
    from app.services.session_service import AgentSession


@dataclass
class ContextPipelineConfig:
    target_input_ratio: float = 0.80
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
    """Production context-management pipeline for AetherCore's runtime history."""

    def __init__(
        self,
        config: ContextPipelineConfig | None = None,
        budget_config: ContextBudgetConfig | None = None,
        estimator: TokenEstimator | None = None,
    ):
        self.config = config or ContextPipelineConfig()
        self.budget = ContextBudget(config=budget_config or ContextBudgetConfig())
        self.estimator = estimator or TokenEstimator()

    def prepare_for_llm(
        self,
        *,
        session: AgentSession,
        llm_runtime: Any,
        messages: list[dict[str, Any]],
        turn_index: int,
    ) -> PreparedContext:
        changed = context_message_adapter.normalize_session(session)
        if changed:
            from app.services.session_service import session_service

            session_service.persist(session)

        state = context_state_store.get(session)
        state = self._refresh_state_thresholds(state, str(llm_runtime.model))

        runtime_messages = [
            context_message_adapter.ensure_runtime_metadata(message, turn_index=message.get("turn_index", 0))
            for message in messages
        ]
        token_estimate = self.estimator.estimate_messages_tokens(runtime_messages)
        state.last_known_token_estimate = token_estimate
        state.percent_used = self._safe_percent(token_estimate, state.effective_window)

        events = self._build_status_events(state, token_estimate)
        if token_estimate <= state.target_input_tokens:
            api_messages = context_message_adapter.to_api_messages(runtime_messages)
            self._validate_or_raise(api_messages, state, token_estimate)
            context_state_store.save(session, state)
            return PreparedContext(messages=api_messages, state=state, events=events)

        compacted = self._apply_proactive_compaction(session=session, state=state, turn_index=turn_index)
        events.extend(compacted.events)
        runtime_messages = [
            context_message_adapter.ensure_runtime_metadata(message, turn_index=message.get("turn_index", 0))
            for message in messages[:1]
        ]
        runtime_messages.extend(session.messages)
        token_estimate = self.estimator.estimate_messages_tokens(runtime_messages)
        state.last_known_token_estimate = token_estimate
        state.percent_used = self._safe_percent(token_estimate, state.effective_window)

        api_messages = context_message_adapter.to_api_messages(runtime_messages)
        if token_estimate > state.blocking_limit:
            context_state_store.save(session, state)
            raise ContextOverflowError(
                "context window exceeded after proactive compaction",
                state=state,
                token_estimate=token_estimate,
            )

        self._validate_or_raise(api_messages, state, token_estimate)
        context_state_store.save(session, state)
        return PreparedContext(
            messages=api_messages,
            state=state,
            events=events,
            compacted=compacted.compacted,
            plan=compacted.plan,
        )

    def recover_from_overflow(
        self,
        *,
        session: AgentSession,
        llm_runtime: Any,
        messages: list[dict[str, Any]],
        turn_index: int,
        error_message: str,
    ) -> PreparedContext:
        state = self._refresh_state_thresholds(context_state_store.get(session), str(llm_runtime.model))
        if state.reactive_retry_count >= self.config.reactive_max_retries:
            raise ContextOverflowError(
                f"reactive compaction retries exhausted: {error_message}",
                state=state,
                token_estimate=state.last_known_token_estimate,
            )

        state.reactive_retry_count += 1
        compacted = self._apply_reactive_compaction(session=session, state=state, turn_index=turn_index, error_message=error_message)
        events = list(compacted.events)

        runtime_messages = [
            context_message_adapter.ensure_runtime_metadata(message, turn_index=message.get("turn_index", 0))
            for message in messages[:1]
        ]
        runtime_messages.extend(session.messages)
        token_estimate = self.estimator.estimate_messages_tokens(runtime_messages)
        state.last_known_token_estimate = token_estimate
        state.percent_used = self._safe_percent(token_estimate, state.effective_window)

        api_messages = context_message_adapter.to_api_messages(runtime_messages)
        self._validate_or_raise(api_messages, state, token_estimate)
        context_state_store.save(session, state)
        return PreparedContext(
            messages=api_messages,
            state=state,
            events=events,
            compacted=compacted.compacted,
            plan=compacted.plan,
        )

    def update_api_usage(self, session: AgentSession, usage: dict[str, int]) -> ContextSessionState:
        state = context_state_store.get(session)
        state.last_api_usage = dict(usage)
        state.last_known_token_estimate = self.estimator.count_from_usage(usage)
        state.percent_used = self._safe_percent(state.last_known_token_estimate, state.effective_window)
        context_state_store.save(session, state)
        return state

    def reset_reactive_retry(self, session: AgentSession) -> None:
        state = context_state_store.get(session)
        state.reactive_retry_count = 0
        context_state_store.save(session, state)

    def _apply_proactive_compaction(
        self,
        *,
        session: AgentSession,
        state: ContextSessionState,
        turn_index: int,
    ) -> PreparedContext:
        original_messages = list(session.messages)
        token_before = self.estimator.estimate_messages_tokens(original_messages)

        truncated_messages, truncated_count = self._truncate_tool_results(
            original_messages,
            keep_recent_turns=self.config.keep_recent_turns,
        )
        if truncated_count > 0:
            from app.services.session_service import session_service

            session.messages = truncated_messages
            state.compaction_count += 1
            state.last_compaction_at = utc_now_iso()
            state.last_compaction_strategy = CompactionStrategyName.ToolResultTruncate.value
            token_after = self.estimator.estimate_messages_tokens(session.messages)
            plan = CompactionPlan(
                strategy=CompactionStrategyName.ToolResultTruncate,
                estimated_tokens_before=token_before,
                estimated_tokens_after=token_after,
                estimated_tokens_saved=max(0, token_before - token_after),
                messages_affected=truncated_count,
                reason="truncate older tool results before full summarization",
            )
            session_service.persist(session)
            return PreparedContext(
                messages=[],
                state=state,
                compacted=True,
                plan=plan,
                events=[self._compacted_event(plan, state)],
            )

        summary_messages = self._summarize_older_turns(
            original_messages,
            turn_index=turn_index,
            keep_recent_turns=self.config.keep_recent_turns,
            strategy=CompactionStrategyName.TranscriptSummary,
        )
        if summary_messages is None:
            state.consecutive_compaction_failures += 1
            context_state_store.save(session, state)
            return PreparedContext(messages=[], state=state)

        session.messages = summary_messages
        state.compaction_count += 1
        state.consecutive_compaction_failures = 0
        state.last_compaction_at = utc_now_iso()
        state.last_compaction_strategy = CompactionStrategyName.TranscriptSummary.value
        state.last_boundary_message_id = summary_messages[0].get("message_id")
        token_after = self.estimator.estimate_messages_tokens(session.messages)
        plan = CompactionPlan(
            strategy=CompactionStrategyName.TranscriptSummary,
            estimated_tokens_before=token_before,
            estimated_tokens_after=token_after,
            estimated_tokens_saved=max(0, token_before - token_after),
            messages_affected=max(0, len(original_messages) - len(summary_messages)),
            reason="summarize stable transcript history to recover input budget",
        )
        from app.services.session_service import session_service

        session_service.persist(session)
        return PreparedContext(
            messages=[],
            state=state,
            compacted=True,
            plan=plan,
            events=[self._compacted_event(plan, state)],
        )

    def _apply_reactive_compaction(
        self,
        *,
        session: AgentSession,
        state: ContextSessionState,
        turn_index: int,
        error_message: str,
    ) -> PreparedContext:
        original_messages = list(session.messages)
        token_before = self.estimator.estimate_messages_tokens(original_messages)
        truncated_messages, truncated_count = self._truncate_tool_results(
            original_messages,
            keep_recent_turns=self.config.keep_recent_turns_reactive,
            aggressive=True,
        )
        if truncated_count > 0:
            from app.services.session_service import session_service

            session.messages = truncated_messages
            state.compaction_count += 1
            state.last_compaction_at = utc_now_iso()
            state.last_compaction_strategy = CompactionStrategyName.ReactiveOverflow.value
            token_after = self.estimator.estimate_messages_tokens(session.messages)
            plan = CompactionPlan(
                strategy=CompactionStrategyName.ReactiveOverflow,
                estimated_tokens_before=token_before,
                estimated_tokens_after=token_after,
                estimated_tokens_saved=max(0, token_before - token_after),
                messages_affected=truncated_count,
                reason=f"reactive overflow recovery: {error_message}",
            )
            session_service.persist(session)
            return PreparedContext(
                messages=[],
                state=state,
                compacted=True,
                plan=plan,
                events=[ContextEvent(
                    type=ContextEventType.Recovered,
                    payload={
                        "strategy": plan.strategy.value,
                        "tokens_saved": plan.estimated_tokens_saved,
                        "messages_affected": plan.messages_affected,
                        "error": error_message,
                    },
                )],
            )

        summary_messages = self._summarize_older_turns(
            original_messages,
            turn_index=turn_index,
            keep_recent_turns=self.config.keep_recent_turns_reactive,
            strategy=CompactionStrategyName.ReactiveOverflow,
        )
        if summary_messages is None:
            state.consecutive_compaction_failures += 1
            context_state_store.save(session, state)
            raise ContextOverflowError(
                f"reactive compaction failed: {error_message}",
                state=state,
                token_estimate=token_before,
            )

        session.messages = summary_messages
        state.compaction_count += 1
        state.consecutive_compaction_failures = 0
        state.last_compaction_at = utc_now_iso()
        state.last_compaction_strategy = CompactionStrategyName.ReactiveOverflow.value
        state.last_boundary_message_id = summary_messages[0].get("message_id")
        token_after = self.estimator.estimate_messages_tokens(session.messages)
        plan = CompactionPlan(
            strategy=CompactionStrategyName.ReactiveOverflow,
            estimated_tokens_before=token_before,
            estimated_tokens_after=token_after,
            estimated_tokens_saved=max(0, token_before - token_after),
            messages_affected=max(0, len(original_messages) - len(summary_messages)),
            reason=f"reactive overflow recovery: {error_message}",
        )
        from app.services.session_service import session_service

        session_service.persist(session)
        return PreparedContext(
            messages=[],
            state=state,
            compacted=True,
            plan=plan,
            events=[ContextEvent(
                type=ContextEventType.Recovered,
                payload={
                    "strategy": plan.strategy.value,
                    "tokens_saved": plan.estimated_tokens_saved,
                    "messages_affected": plan.messages_affected,
                    "error": error_message,
                },
            )],
        )

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

        summarize_messages = [message for message in messages if int(message.get("turn_index", 0)) < protected_turn]
        keep_messages = [message for message in messages if int(message.get("turn_index", 0)) >= protected_turn]
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

    def _refresh_state_thresholds(self, state: ContextSessionState, model: str) -> ContextSessionState:
        state.model_id = model
        state.context_window = self.budget.get_context_window_for_model(model)
        state.effective_window = self.budget.get_effective_context_window(model)
        state.warning_threshold = self.budget.get_warning_threshold(model)
        state.error_threshold = self.budget.get_error_threshold(model)
        state.blocking_limit = self.budget.get_blocking_limit(model)
        state.target_input_tokens = min(
            self.budget.get_auto_compact_threshold(model),
            int(state.effective_window * self.config.target_input_ratio),
        )
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
        if token_estimate >= state.warning_threshold:
            events.append(
                ContextEvent(
                    type=ContextEventType.Warning,
                    payload={
                        "estimated_tokens": token_estimate,
                        "warning_threshold": state.warning_threshold,
                        "blocking_limit": state.blocking_limit,
                        "percent_used": state.percent_used,
                    },
                )
            )
        return events

    def _compacted_event(self, plan: CompactionPlan, state: ContextSessionState) -> ContextEvent:
        return ContextEvent(
            type=ContextEventType.Compacted,
            payload={
                "strategy": plan.strategy.value,
                "tokens_before": plan.estimated_tokens_before,
                "tokens_after": plan.estimated_tokens_after,
                "tokens_saved": plan.estimated_tokens_saved,
                "messages_affected": plan.messages_affected,
                "reason": plan.reason,
                "compaction_count": state.compaction_count,
            },
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


context_pipeline = ContextPipeline()
