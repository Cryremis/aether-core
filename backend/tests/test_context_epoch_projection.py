from __future__ import annotations

import copy
import json

from app.services.context.epoch import HANDOFF_MAX_BYTES, rebase_context
from app.services.context.history_store import history_store
from app.services.context.message_adapter import context_message_adapter
from app.services.session_types import AgentSession
from test_agent_engine import initialize_store


def test_repeated_epochs_flatten_handoffs_without_nesting_or_reference_growth():
    original = [
        {"message_id": "goal", "role": "user", "content": '检查 "/var/log/app" 并保留 "引号"'},
        {"message_id": "result", "role": "tool", "tool_call_id": "call1", "content": "last evidence"},
    ]
    session = AgentSession("repeated_epochs", messages=copy.deepcopy(original))
    for iteration in range(20):
        rebase_context(session, reason="model changed")
        handoff = session.messages[0]
        if iteration == 0:
            expected_content = handoff["content"]
        assert handoff["content"] == expected_content
        assert handoff["compression_meta"]["source_message_ids"] == ["goal", "result"]
        assert len(handoff["compression_meta"]["epoch_records"]) == 2
        assert handoff["compression_meta"]["omitted_records"] == 0


def test_projection_is_bounded_and_originals_remain_readable_after_persistence(tmp_path):
    initialize_store(tmp_path)
    original = [context_message_adapter.make_user_message("original goal", turn_index=1)]
    original += [
        {"message_id": f"evidence-{i}", "role": "tool", "tool_call_id": f"call-{i}",
         "content": f"start-{i}" + "日志内容" * 12000 + f"end-{i}"}
        for i in range(24)
    ]
    history_store.capture("bounded_epoch", {"messages": original})
    session = AgentSession("bounded_epoch", messages=copy.deepcopy(original))
    rebase_context(session, reason="switch")
    handoff = session.messages[0]
    meta = handoff["compression_meta"]
    assert len(handoff["content"].encode("utf-8")) <= HANDOFF_MAX_BYTES
    assert "original goal" in handoff["content"]
    assert "start-23" in handoff["content"] and "end-23" in handoff["content"]
    assert meta["omitted_records"] > 0
    assert len(meta["source_message_ids"]) == len(original)
    history_store.capture(session.session_id, {"messages": session.messages})
    expanded = history_store.expand_history(session.session_id, session.messages)
    assert [item["message_id"] for item in expanded] == [item["message_id"] for item in original]
    assert expanded[12]["content"] == original[12]["content"]
    previous = copy.deepcopy(meta)
    rebase_context(session, reason="switch")
    assert session.messages[0]["compression_meta"] == previous


def test_new_evidence_extends_flat_projection_and_preserves_current_user():
    session = AgentSession("continued_epoch", messages=[
        {"role": "user", "content": "original task", "message_id": "goal"},
    ])
    rebase_context(session, reason="switch")
    current = {"role": "user", "content": "continue", "message_id": "current"}
    session.messages += [
        {"role": "assistant", "content": "progress", "message_id": "progress"},
        current,
    ]
    rebase_context(session, current_user_id="current", reason="switch")
    handoff = session.messages[0]
    assert handoff["compression_meta"]["source_message_ids"] == ["goal", "progress"]
    assert [record["source_message_id"] for record in handoff["compression_meta"]["epoch_records"]] == ["goal", "progress"]
    assert session.messages[-1] == current
