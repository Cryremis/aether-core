# backend/tests/test_context_management.py
"""
Comprehensive tests for the context management module.

Tests cover:
- Token estimation and counting
- Context budget and thresholds
- Priority management
- Message compaction
- Context manager integration
- Truncation utilities
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import pytest

from app.services.context.token_estimation import (
    TokenEstimator,
    TokenEstimationConfig,
    rough_token_count,
    estimate_message_tokens,
    estimate_messages_tokens,
)
from app.services.context.context_budget import (
    ContextBudget,
    ContextBudgetConfig,
    TokenWarningState,
    WarningLevel,
    calculate_token_warning_state,
    get_auto_compact_threshold,
)
from app.services.context.priority_manager import (
    PriorityManager,
    PriorityConfig,
    MessagePriority,
)
from app.services.context.message_compaction import (
    MessageCompactor,
    MicroCompactConfig,
    CompactBoundaryMarker,
    MicroCompactResult,
    CompactResult,
    CompactionStrategy,
)
from app.services.context.context_manager import (
    ContextManager,
    ContextManagerConfig,
    ContextWindowState,
    AutoCompactTrackingState,
)
from app.services.context.truncate import (
    truncate_text,
    truncate_path_middle,
    truncate_to_width,
    truncate_start_to_width,
    truncate_tool_result,
    truncate_json_content,
    strip_images_from_messages,
)


class TestTokenEstimation:
    """Tests for token estimation module."""

    def test_rough_token_count_basic(self):
        estimator = TokenEstimator()
        text = "Hello, world! This is a test."
        tokens = estimator.rough_token_count(text)
        assert tokens > 0
        assert tokens == len(text) // 4

    def test_rough_token_count_empty(self):
        estimator = TokenEstimator()
        assert estimator.rough_token_count("") == 0
        assert estimator.rough_token_count(None) == 0

    def test_rough_token_count_custom_ratio(self):
        estimator = TokenEstimator()
        text = "JSON content test"
        json_tokens = estimator.rough_token_count(text, bytes_per_token=2)
        default_tokens = estimator.rough_token_count(text, bytes_per_token=4)
        assert json_tokens == default_tokens * 2

    def test_bytes_per_token_for_file_type(self):
        estimator = TokenEstimator()
        assert estimator.bytes_per_token_for_file_type("json") == 2
        assert estimator.bytes_per_token_for_file_type("yaml") == 2
        assert estimator.bytes_per_token_for_file_type("py") == 4
        assert estimator.bytes_per_token_for_file_type("txt") == 4

    def test_estimate_message_tokens_user(self):
        estimator = TokenEstimator()
        message = {"role": "user", "content": "Hello, this is a user message."}
        tokens = estimator.estimate_message_tokens(message)
        assert tokens > 0
        assert tokens >= len(message["content"]) // 4

    def test_estimate_message_tokens_assistant(self):
        estimator = TokenEstimator()
        message = {
            "role": "assistant",
            "content": "Here is the assistant response.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "function": {"name": "test_tool", "arguments": '{"arg": "value"}'},
                }
            ],
        }
        tokens = estimator.estimate_message_tokens(message)
        assert tokens > 0

    def test_estimate_message_tokens_system(self):
        estimator = TokenEstimator()
        message = {"role": "system", "content": "System instructions here."}
        tokens = estimator.estimate_message_tokens(message)
        assert tokens > 0

    def test_estimate_message_tokens_tool(self):
        estimator = TokenEstimator()
        message = {
            "role": "tool",
            "tool_call_id": "call_123",
            "content": "Tool result output.",
        }
        tokens = estimator.estimate_message_tokens(message)
        assert tokens > 0

    def test_estimate_messages_tokens(self):
        estimator = TokenEstimator()
        messages = [
            {"role": "system", "content": "System prompt."},
            {"role": "user", "content": "User query."},
            {"role": "assistant", "content": "Assistant response."},
        ]
        total = estimator.estimate_messages_tokens(messages)
        assert total > 0
        individual_sum = sum(estimator.estimate_message_tokens(m) for m in messages)
        assert total >= individual_sum

    def test_estimate_content_block_text(self):
        estimator = TokenEstimator()
        blocks = [{"type": "text", "text": "Content block text."}]
        tokens = estimator.estimate_content_tokens(blocks)
        assert tokens > 0

    def test_estimate_content_block_image(self):
        estimator = TokenEstimator()
        blocks = [{"type": "image", "source": {"data": "base64data"}}]
        tokens = estimator.estimate_content_tokens(blocks)
        assert tokens == estimator.config.image_token_estimate

    def test_estimate_content_block_tool_use(self):
        estimator = TokenEstimator()
        blocks = [{"type": "tool_use", "name": "read_file", "input": {"path": "/test"}}]
        tokens = estimator.estimate_content_tokens(blocks)
        assert tokens > 0

    def test_count_from_usage(self):
        estimator = TokenEstimator()
        usage = {
            "input_tokens": 1000,
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": 200,
            "output_tokens": 300,
        }
        total = estimator.count_from_usage(usage)
        assert total == 2000

    def test_convenience_functions(self):
        text = "Test content for convenience functions."
        assert rough_token_count(text) > 0
        
        msg = {"role": "user", "content": text}
        assert estimate_message_tokens(msg) > 0
        
        msgs = [msg]
        assert estimate_messages_tokens(msgs) > 0


class TestContextBudget:
    """Tests for context budget management."""

    def test_get_context_window_for_model_default(self):
        budget = ContextBudget()
        window = budget.get_context_window_for_model("unknown-model-xyz")
        assert window == 200_000

    def test_get_context_window_for_model_with_cache(self):
        budget = ContextBudget()
        # 使用一个不会匹配到真实模型的名字，验证默认值
        window = budget.get_context_window_for_model("test-unknown-model-xyz")
        assert window == 200_000

    def test_get_context_window_for_model_1m_suffix(self):
        budget = ContextBudget()
        window = budget.get_context_window_for_model("test-model[1m]")
        assert window == 1_000_000

    def test_get_context_window_for_model_1m_beta(self):
        budget = ContextBudget()
        window = budget.get_context_window_for_model("test-model", betas=["context-1m"])
        assert window == 1_000_000

    def test_get_effective_context_window(self):
        budget = ContextBudget()
        effective = budget.get_effective_context_window("test-model-200k")
        # 验证有效窗口小于完整窗口（因为预留了输出tokens）
        full_window = budget.get_context_window_for_model("test-model-200k")
        assert effective < full_window
        assert effective == full_window - budget.config.max_output_tokens_for_summary

    def test_get_auto_compact_threshold(self):
        budget = ContextBudget()
        threshold = budget.get_auto_compact_threshold("test-model-200k")
        effective = budget.get_effective_context_window("test-model-200k")
        assert threshold == effective - budget.config.auto_compact_buffer_tokens

    def test_get_warning_threshold(self):
        budget = ContextBudget()
        warning = budget.get_warning_threshold("test-model-200k")
        threshold = budget.get_auto_compact_threshold("test-model-200k")
        assert warning == threshold - budget.config.warning_threshold_buffer_tokens

    def test_get_blocking_limit(self):
        budget = ContextBudget()
        blocking = budget.get_blocking_limit("test-model-200k")
        effective = budget.get_effective_context_window("test-model-200k")
        assert blocking == effective - budget.config.manual_compact_buffer_tokens

    def test_calculate_token_warning_state_normal(self):
        budget = ContextBudget()
        state = budget.calculate_token_warning_state(50_000, "test-model-200k")
        assert state.percent_left > 50
        assert not state.is_above_warning_threshold
        assert not state.is_above_auto_compact_threshold
        assert state.warning_level == WarningLevel.Normal

    def test_calculate_token_warning_state_warning(self):
        budget = ContextBudget()
        auto_threshold = budget.get_auto_compact_threshold("test-model-200k")
        state = budget.calculate_token_warning_state(auto_threshold - 1, "test-model-200k")
        assert state.is_above_auto_compact_threshold is False
        state2 = budget.calculate_token_warning_state(auto_threshold + 100, "test-model-200k")
        assert state2.is_above_auto_compact_threshold

    def test_calculate_token_warning_state_error(self):
        budget = ContextBudget()
        threshold = budget.get_auto_compact_threshold("test-model-200k")
        state = budget.calculate_token_warning_state(threshold + 1, "test-model-200k")
        assert state.is_above_auto_compact_threshold
        assert state.warning_level in (WarningLevel.Error, WarningLevel.Blocking)

    def test_calculate_token_warning_state_blocking(self):
        budget = ContextBudget()
        blocking = budget.get_blocking_limit("test-model-200k")
        state = budget.calculate_token_warning_state(blocking + 10_000, "test-model-200k")
        assert state.is_at_blocking_limit
        assert state.warning_level == WarningLevel.Blocking

    def test_should_auto_compact_enabled(self):
        budget = ContextBudget()
        threshold = budget.get_auto_compact_threshold("test-model-200k")
        assert budget.should_auto_compact(threshold + 1, "test-model-200k")
        assert not budget.should_auto_compact(threshold - 1, "test-model-200k")

    def test_should_auto_compact_disabled(self):
        config = ContextBudgetConfig(auto_compact_enabled=False)
        budget = ContextBudget(config=config)
        threshold = budget.get_auto_compact_threshold("test-model-200k")
        assert not budget.should_auto_compact(threshold + 1, "test-model-200k")

    def test_should_auto_compact_recursion_guard(self):
        budget = ContextBudget()
        threshold = budget.get_auto_compact_threshold("test-model-200k")
        assert not budget.should_auto_compact(threshold + 1, "test-model-200k", query_source="compact")
        assert not budget.should_auto_compact(threshold + 1, "test-model-200k", query_source="session_memory")

    def test_calculate_context_percentages(self):
        budget = ContextBudget()
        result = budget.calculate_context_percentages({"input_tokens": 100_000}, 200_000)
        assert result["used"] == 50
        assert result["remaining"] == 50

    def test_calculate_context_percentages_none(self):
        budget = ContextBudget()
        result = budget.calculate_context_percentages(None, 200_000)
        assert result["used"] is None
        assert result["remaining"] is None

    def test_convenience_functions(self):
        state = calculate_token_warning_state(50_000, "test-model-200k")
        assert state.percent_left > 50
        threshold = get_auto_compact_threshold("test-model-200k")
        assert threshold > 0


class TestPriorityManager:
    """Tests for message priority management."""

    def test_calculate_priority_system(self):
        manager = PriorityManager()
        message = {"role": "system", "content": "System instructions."}
        priority = manager.calculate_priority(message)
        assert priority == MessagePriority.Critical

    def test_calculate_priority_user_recent(self):
        manager = PriorityManager()
        message = {"role": "user", "content": "User query.", "turn_index": 0}
        priority = manager.calculate_priority(message, current_turn=1)
        assert priority == MessagePriority.High

    def test_calculate_priority_user_old(self):
        manager = PriorityManager()
        message = {"role": "user", "content": "Old user query.", "turn_index": 0}
        priority = manager.calculate_priority(message, current_turn=10)
        assert priority == MessagePriority.Medium

    def test_calculate_priority_assistant_with_tools(self):
        manager = PriorityManager()
        message = {
            "role": "assistant",
            "content": "Response.",
            "tool_calls": [{"function": {"name": "test_tool"}}],
        }
        priority = manager.calculate_priority(message)
        assert priority == MessagePriority.High

    def test_calculate_priority_tool_result_compactable(self):
        config = PriorityConfig(max_tool_result_age_turns=5)
        manager = PriorityManager(config=config)
        message = {
            "role": "tool",
            "tool_call_id": "read_file_123",
            "content": "File content here.",
            "turn_index": 0,
        }
        priority = manager.calculate_priority(message, current_turn=10)
        assert priority in (MessagePriority.Compactable, MessagePriority.Low, MessagePriority.Medium)

    def test_calculate_priority_compact_boundary(self):
        manager = PriorityManager()
        message = {"role": "system", "content": "compact_boundary marker", "is_boundary_marker": True}
        priority = manager.calculate_priority(message)
        assert priority == MessagePriority.Critical

    def test_calculate_priority_compact_summary(self):
        manager = PriorityManager()
        message = {"role": "user", "content": "conversation_summary text", "is_compact_summary": True}
        priority = manager.calculate_priority(message)
        assert priority == MessagePriority.High

    def test_build_priority_map(self):
        manager = PriorityManager()
        messages = [
            {"role": "system", "content": "System."},
            {"role": "user", "content": "Query.", "turn_index": 0},
            {"role": "assistant", "content": "Response.", "turn_index": 0},
        ]
        priorities = manager.build_priority_map(messages, current_turn=1)
        assert len(priorities) == 3
        assert priorities[0] == MessagePriority.Critical
        assert priorities[1] == MessagePriority.High

    def test_advance_turn(self):
        manager = PriorityManager()
        initial = manager._turn_counter
        new_turn = manager.advance_turn()
        assert new_turn == initial + 1

    def test_update_file_state_cache(self):
        manager = PriorityManager()
        manager.update_file_state_cache("/test/path.py", 500)
        assert "/test/path.py" in manager._file_state_cache

    def test_clear_file_state_cache(self):
        manager = PriorityManager()
        manager.update_file_state_cache("/test/path.py", 500)
        manager.clear_cache()
        assert len(manager._file_state_cache) == 0

    def test_fingerprint_messages(self):
        manager = PriorityManager()
        messages = [{"role": "user", "content": "Test"}]
        fingerprint = manager.fingerprint_messages(messages)
        assert len(fingerprint) == 16
        fingerprint2 = manager.fingerprint_messages(messages)
        assert fingerprint == fingerprint2


class TestMessageCompaction:
    """Tests for message compaction module."""

    def test_micro_compact_disabled(self):
        config = MicroCompactConfig(enabled=False)
        compactor = MessageCompactor(micro_config=config)
        messages = [{"role": "user", "content": "Test"}]
        result = compactor.micro_compact_messages(messages)
        assert result.messages == messages
        assert result.tokens_saved == 0

    def test_micro_compact_time_based_no_gap(self):
        config = MicroCompactConfig(time_based_enabled=True, time_based_gap_threshold_minutes=30)
        compactor = MessageCompactor(micro_config=config)
        messages = [
            {"role": "assistant", "content": "Response", "timestamp": datetime.now(timezone.utc).isoformat()},
            {"role": "tool", "tool_call_id": "read_file_123", "content": "Content"},
        ]
        result = compactor.micro_compact_messages(messages, query_source="agent")
        assert result.tokens_saved == 0

    def test_micro_compact_time_based_with_gap(self):
        config = MicroCompactConfig(
            time_based_enabled=True,
            time_based_gap_threshold_minutes=30,
            keep_recent_count=1,
        )
        compactor = MessageCompactor(micro_config=config)
        old_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        messages = [
            {"role": "assistant", "content": "Response", "timestamp": old_time, "tool_calls": [{"id": "read_file_123", "function": {"name": "read_file"}}]},
            {"role": "tool", "tool_call_id": "read_file_123", "content": "File content here that is quite long."},
        ]
        result = compactor.micro_compact_messages(messages, query_source="agent")
        if result.tokens_saved > 0:
            assert result.tools_cleared > 0
            assert any(m.get("content") == config.cleared_message for m in result.messages if m.get("role") == "tool")

    def test_collect_compactable_tool_ids(self):
        config = MicroCompactConfig(compactable_tool_names={"read_file", "glob"})
        compactor = MessageCompactor(micro_config=config)
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "call_1", "function": {"name": "read_file"}}]},
            {"role": "assistant", "tool_calls": [{"id": "call_2", "function": {"name": "write_file"}}]},
        ]
        ids = compactor._collect_compactable_tool_ids(messages)
        assert "call_1" in ids
        assert "call_2" not in ids

    def test_create_boundary_marker(self):
        compactor = MessageCompactor()
        marker = compactor.create_boundary_marker(
            compaction_type="auto",
            pre_compact_token_count=100_000,
            messages_summarized=10,
        )
        assert marker.compaction_type == "auto"
        assert marker.pre_compact_token_count == 100_000
        assert marker.messages_summarized == 10

    def test_boundary_marker_to_message(self):
        marker = CompactBoundaryMarker(
            compaction_type="manual",
            pre_compact_token_count=50_000,
        )
        message = marker.to_message()
        assert message["role"] == "system"
        assert message["is_boundary_marker"] is True
        assert "compact_boundary" in message["content"]

    def test_create_summary_message(self):
        compactor = MessageCompactor()
        summary = compactor.create_summary_message("Summary text here", suppress_follow_up=False)
        assert summary["role"] == "user"
        assert summary["is_compact_summary"] is True
        assert "conversation_summary" in summary["content"]

    def test_estimate_compaction_savings(self):
        compactor = MessageCompactor()
        messages = [
            {"role": "system", "content": "System prompt."},
            {"role": "user", "content": "Query 1."},
            {"role": "assistant", "content": "Response 1."},
            {"role": "user", "content": "Query 2."},
        ]
        savings = compactor.estimate_compaction_savings(messages, target_tokens=100)
        assert savings["current_tokens"] > 0
        assert "estimated_savings" in savings

    def test_strip_images_from_messages(self):
        compactor = MessageCompactor()
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}, {"type": "image", "source": {"data": "base64"}}]},
        ]
        stripped = compactor.strip_images_from_messages(messages)
        image_blocks = [b for b in stripped[0]["content"] if b.get("type") == "image"]
        assert len(image_blocks) == 0
        text_blocks = [b for b in stripped[0]["content"] if b.get("type") == "text" and b.get("text") == "[image]"]
        assert len(text_blocks) == 1

    def test_suppress_and_clear_warning(self):
        compactor = MessageCompactor()
        assert not compactor.is_warning_suppressed()
        compactor.suppress_compact_warning()
        assert compactor.is_warning_suppressed()
        compactor.clear_compact_warning_suppression()
        assert not compactor.is_warning_suppressed()


class TestContextManager:
    """Tests for context manager integration."""

    def test_set_model_updates_thresholds(self):
        manager = ContextManager()
        manager.set_model("test-model-200k")
        state = manager.get_context_state()
        # 验证阈值已正确计算（不依赖具体的context_window值）
        assert state.context_window >= 200_000
        assert state.effective_window > 0
        assert state.effective_window < state.context_window
        assert state.auto_compact_threshold > 0
        assert state.auto_compact_threshold < state.effective_window

    def test_update_token_usage(self):
        manager = ContextManager()
        manager.set_model("test-model-200k")
        messages = [
            {"role": "user", "content": "This is a test message for token counting."},
            {"role": "assistant", "content": "This is the assistant response."},
        ]
        tokens = manager.update_token_usage(messages)
        assert tokens > 0
        state = manager.get_context_state()
        assert state.token_usage == tokens

    def test_update_from_api_usage(self):
        manager = ContextManager()
        manager.set_model("test-model-200k")
        usage = {
            "input_tokens": 5000,
            "cache_creation_input_tokens": 1000,
            "cache_read_input_tokens": 2000,
            "output_tokens": 500,
        }
        manager.update_from_api_usage(usage)
        state = manager.get_context_state()
        assert state.token_usage == 8500
        assert state.last_usage_from_api == usage

    def test_should_auto_compact_below_threshold(self):
        manager = ContextManager()
        manager.set_model("test-model-200k")
        messages = [{"role": "user", "content": "Short message."}]
        manager.update_token_usage(messages)
        should = manager.should_auto_compact(messages)
        assert not should

    def test_should_auto_compact_above_threshold(self):
        config = ContextManagerConfig(auto_compact_enabled=True)
        budget_config = ContextBudgetConfig(auto_compact_buffer_tokens=100)
        manager = ContextManager(config=config, budget_config=budget_config)
        
        manager.set_model("test-model-200k")
        threshold = manager._state.auto_compact_threshold
        long_text = "x" * (threshold * 100)
        messages = [{"role": "user", "content": long_text}]
        manager.update_token_usage(messages)
        should = manager.should_auto_compact(messages, query_source="agent")
        assert should

    def test_should_auto_compact_recursion_guard(self):
        manager = ContextManager()
        manager.set_model("test-model-200k")
        threshold = manager._state.auto_compact_threshold
        long_text = "x" * (threshold + 1000)
        messages = [{"role": "user", "content": long_text}]
        manager.update_token_usage(messages)
        assert not manager.should_auto_compact(messages, query_source="compact")
        assert not manager.should_auto_compact(messages, query_source="session_memory")

    def test_should_auto_compact_circuit_breaker(self):
        config = ContextManagerConfig(max_consecutive_failures=3)
        manager = ContextManager(config=config)
        manager.set_model("test-model-200k")
        manager._tracking.consecutive_failures = 3
        threshold = manager._state.auto_compact_threshold
        long_text = "x" * (threshold + 1000)
        messages = [{"role": "user", "content": long_text}]
        manager.update_token_usage(messages)
        assert not manager.should_auto_compact(messages)

    def test_advance_turn(self):
        manager = ContextManager()
        initial_turn = manager._tracking.turn_counter
        manager.advance_turn()
        assert manager._tracking.turn_counter == initial_turn + 1
        assert manager._tracking.turn_id.startswith("turn_")

    def test_auto_compact_if_needed(self):
        manager = ContextManager()
        manager.set_model("test-model-200k")
        messages = [{"role": "user", "content": "Test message."}]
        was_compacted, result = manager.auto_compact_if_needed(messages)
        assert not was_compacted
        assert result is None

    def test_get_messages_after_compact_boundary(self):
        manager = ContextManager()
        messages = [
            {"role": "system", "content": "Start"},
            {"role": "system", "content": "Boundary", "is_boundary_marker": True},
            {"role": "user", "content": "After boundary"},
        ]
        after = manager.get_messages_after_compact_boundary(messages)
        assert len(after) == 1
        assert after[0]["content"] == "After boundary"

    def test_get_messages_after_compact_boundary_no_marker(self):
        manager = ContextManager()
        messages = [
            {"role": "system", "content": "Start"},
            {"role": "user", "content": "Query"},
        ]
        after = manager.get_messages_after_compact_boundary(messages)
        assert len(after) == 2

    def test_get_last_compact_boundary_info(self):
        manager = ContextManager()
        messages = [
            {"role": "system", "content": "Start"},
            {"role": "system", "content": "Boundary", "is_boundary_marker": True, "marker_id": "b123"},
        ]
        info = manager.get_last_compact_boundary_info(messages)
        assert info is not None
        assert info["marker_id"] == "b123"

    def test_build_context_summary(self):
        manager = ContextManager()
        manager.set_model("test-model-200k")
        manager.update_token_usage([{"role": "user", "content": "Test"}])
        summary = manager.build_context_summary()
        assert "上下文窗口" in summary
        assert "当前使用" in summary

    def test_get_compaction_metrics(self):
        manager = ContextManager()
        manager.set_model("test-model-200k")
        metrics = manager.get_compaction_metrics()
        assert "token_usage" in metrics
        assert "context_window" in metrics
        assert "warning_level" in metrics

    def test_reset_state(self):
        manager = ContextManager()
        manager.set_model("test-model-200k")
        manager.update_token_usage([{"role": "user", "content": "Test message." * 100}])
        manager._state.compact_count = 5
        manager.reset_state()
        state = manager.get_context_state()
        assert state.token_usage == 0
        assert state.compact_count == 0

    def test_record_compaction_failure(self):
        manager = ContextManager()
        manager.record_compaction_failure()
        assert manager._tracking.consecutive_failures == 1
        manager.record_compaction_failure()
        assert manager._tracking.consecutive_failures == 2

    def test_record_compaction_success(self):
        manager = ContextManager()
        manager._tracking.consecutive_failures = 2
        manager.record_compaction_success()
        assert manager._tracking.consecutive_failures == 0
        assert manager._tracking.compacted is True


class TestTruncate:
    """Tests for truncation utilities."""

    def test_truncate_text_short(self):
        text = "Short text"
        result = truncate_text(text, 100)
        assert result == text

    def test_truncate_text_long(self):
        text = "This is a very long text that needs truncation"
        result = truncate_text(text, 20)
        assert len(result) <= 20
        assert result.endswith("...")

    def test_truncate_path_middle_preserves_filename(self):
        path = "src/components/deeply/nested/folder/MyComponent.tsx"
        result = truncate_path_middle(path, 30)
        assert "MyComponent.tsx" in result
        assert "..." in result

    def test_truncate_path_middle_short(self):
        path = "src/file.py"
        result = truncate_path_middle(path, 50)
        assert result == path

    def test_truncate_to_width(self):
        text = "Hello world"
        result = truncate_to_width(text, 5)
        assert len(result) <= 5

    def test_truncate_start_to_width(self):
        text = "Hello world"
        result = truncate_start_to_width(text, 8)
        assert result.startswith("...")
        assert "world" in result

    def test_truncate_tool_result(self):
        content = "Very long tool result content..."
        result = truncate_tool_result(content, 10)
        assert result == "[旧工具结果内容已清除]"

    def test_truncate_json_content_array(self):
        content = ["item1", "item2", "item3", "item4", "item5", "item6", "item7"]
        result = truncate_json_content(content, 100)
        assert "[" in result
        assert "more items" in result

    def test_truncate_json_content_dict(self):
        content = {"key1": "value1", "key2": "value2", "key3": "value3", "key4": "value4"}
        result = truncate_json_content(content, 50)
        assert "{" in result

    def test_truncate_json_content_string(self):
        content = "Simple string content"
        result = truncate_json_content(content, 50)
        assert result == content

    def test_strip_images_from_messages_basic(self):
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}, {"type": "image", "source": {"data": "b64"}}]},
        ]
        result = strip_images_from_messages(messages)
        assert result[0]["content"][1]["type"] == "text"
        assert result[0]["content"][1]["text"] == "[image]"

    def test_strip_images_from_messages_nested_in_tool_result(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "content": [{"type": "image", "source": {"data": "b64"}}],
                    }
                ],
            },
        ]
        result = strip_images_from_messages(messages)
        tool_result = result[0]["content"][0]
        assert tool_result["content"][0]["type"] == "text"

    def test_strip_images_from_messages_preserves_other(self):
        messages = [
            {"role": "assistant", "content": "Response text"},
            {"role": "user", "content": [{"type": "text", "text": "Query"}]},
        ]
        result = strip_images_from_messages(messages)
        assert result[0]["content"] == "Response text"
        assert result[1]["content"][0]["text"] == "Query"


class TestIntegration:
    """Integration tests for context management workflow."""

    def test_full_context_workflow(self):
        estimator = TokenEstimator()
        budget_config = ContextBudgetConfig()
        manager = ContextManager(estimator=estimator, budget_config=budget_config)
        
        manager.set_model("test-model-200k")
        
        messages = [
            {"role": "system", "content": "System instructions for the agent."},
            {"role": "user", "content": "Please analyze this data file."},
            {"role": "assistant", "content": "I will read the file first.", "tool_calls": [{"id": "call_1", "function": {"name": "read_file"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "File contents..."},
        ]
        
        tokens = manager.update_token_usage(messages)
        assert tokens > 0
        
        state = manager.get_context_state()
        assert state.token_usage == tokens
        
        should_compact = manager.should_auto_compact(messages)
        assert not should_compact
        
        metrics = manager.get_compaction_metrics()
        assert metrics["token_usage"] == tokens

    def test_context_overflow_scenario(self):
        config = ContextManagerConfig(auto_compact_enabled=True)
        budget_config = ContextBudgetConfig(auto_compact_buffer_tokens=100)
        manager = ContextManager(config=config, budget_config=budget_config)
        
        manager.set_model("test-model-200k")
        
        threshold = manager._state.auto_compact_threshold
        overflow_text = "x" * (threshold * 100)
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": overflow_text},
        ]
        
        manager.update_token_usage(messages)
        
        should_compact = manager.should_auto_compact(messages, query_source="agent")
        assert should_compact
        
        state = manager.get_warning_state()
        assert state is not None
        assert state.warning_level in (WarningLevel.Error, WarningLevel.Blocking)

    def test_compaction_with_priority(self):
        estimator = TokenEstimator()
        priority_manager = PriorityManager()
        compactor = MessageCompactor(estimator=estimator, priority_manager=priority_manager)
        
        messages = [
            {"role": "system", "content": "Critical system instructions."},
            {"role": "user", "content": "User query 1.", "turn_index": 0},
            {"role": "assistant", "content": "Response 1.", "turn_index": 0},
            {"role": "user", "content": "User query 2.", "turn_index": 5},
            {"role": "assistant", "content": "Response 2.", "turn_index": 5},
            {"role": "tool", "tool_call_id": "read_file_old", "content": "Old file content.", "turn_index": 2},
        ]
        
        priorities = priority_manager.build_priority_map(messages, current_turn=10)
        
        critical_count = sum(1 for p in priorities.values() if p == MessagePriority.Critical)
        assert critical_count >= 1
        
        savings = compactor.estimate_compaction_savings(messages, target_tokens=100)
        assert savings["current_tokens"] > 0