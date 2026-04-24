from __future__ import annotations

import uuid
from typing import Any

from app.services.runtime_state.models import (
    ElicitationAnswer,
    ElicitationOption,
    ElicitationQuestion,
    ElicitationRequest,
    ElicitationState,
    WorkItem,
    WorkboardState,
    utc_now_iso,
)
from app.services.runtime_state.store import runtime_state_store
from app.services.session_types import AgentSession


class RuntimeStateService:
    def build_runtime_context_sections(self, session: AgentSession) -> list[str]:
        sections: list[str] = []

        workboard = self.get_workboard(session)
        if workboard.items:
            lines = [
                "<workboard_state>",
                f"status: {workboard.status}",
                f"revision: {workboard.revision}",
            ]
            for index, item in enumerate(workboard.items, start=1):
                lines.append(
                    f"{index}. [{item.status}] {item.active_form or item.title} "
                    f"(id={item.id}, priority={item.priority}, owner={item.owner})"
                )
                if item.blocked_by:
                    lines.append(f"   blocked_by: {', '.join(item.blocked_by)}")
                if item.depends_on:
                    lines.append(f"   depends_on: {', '.join(item.depends_on)}")
                if item.notes:
                    lines.append(f"   notes: {item.notes}")
            lines.append("</workboard_state>")
            sections.append("\n".join(lines))

        elicitation = self.get_elicitation(session)
        if elicitation.pending is not None:
            pending = elicitation.pending
            lines = [
                "<elicitation_state>",
                f"pending_request_id: {pending.id}",
                f"title: {pending.title}",
                f"kind: {pending.kind}",
                f"blocking: {str(pending.blocking).lower()}",
            ]
            for question in pending.questions:
                option_labels = ", ".join(option.label for option in question.options) or "freeform"
                lines.append(f"- {question.header}: {question.question} | options: {option_labels}")
            lines.append("</elicitation_state>")
            sections.append("\n".join(lines))

        return sections

    def get_workboard(self, session: AgentSession) -> WorkboardState:
        return runtime_state_store.load(
            session,
            "workboard",
            WorkboardState,
            lambda: WorkboardState(session_id=session.session_id),
        )

    def save_workboard(self, session: AgentSession, state: WorkboardState) -> WorkboardState:
        runtime_state_store.save(session, "workboard", state)
        return state

    def update_workboard(self, session: AgentSession, payload: dict[str, Any]) -> WorkboardState:
        now = utc_now_iso()
        state = self.get_workboard(session)
        items = list(state.items)
        operations = payload.get("ops")
        if isinstance(operations, list) and operations:
            items = self._apply_workboard_ops(items, operations, now)
        elif isinstance(payload.get("items"), list):
            items = self._replace_workboard_items(items, payload.get("items") or [], now)

        if payload.get("archive_completed"):
            items = [item for item in items if item.status != "completed"]

        state.items = items
        state.status = self._derive_workboard_status(items, explicit_status=payload.get("status"))
        state.revision += 1
        state.updated_at = now
        return self.save_workboard(session, state)

    def _replace_workboard_items(self, current_items: list[WorkItem], raw_items: list[dict[str, Any]], now: str) -> list[WorkItem]:
        existing_by_id = {item.id: item for item in current_items}
        next_items: list[WorkItem] = []
        for raw_item in raw_items:
            title = str(raw_item.get("title") or "").strip()
            if not title:
                continue
            item_id = str(raw_item.get("id") or f"work_{uuid.uuid4().hex[:10]}")
            next_items.append(self._materialize_item(raw_item, existing_by_id.get(item_id), item_id, now))
        return next_items

    def _apply_workboard_ops(self, current_items: list[WorkItem], operations: list[dict[str, Any]], now: str) -> list[WorkItem]:
        items = list(current_items)
        for operation in operations:
            op_type = str(operation.get("op") or "").strip()
            if op_type == "add_item":
                title = str(operation.get("title") or "").strip()
                if not title:
                    continue
                item_id = str(operation.get("id") or f"work_{uuid.uuid4().hex[:10]}")
                items.append(self._materialize_item(operation, None, item_id, now))
            elif op_type == "update_item":
                item_id = str(operation.get("id") or "").strip()
                if not item_id:
                    continue
                existing_index = next((index for index, item in enumerate(items) if item.id == item_id), None)
                if existing_index is None:
                    continue
                items[existing_index] = self._materialize_item(operation, items[existing_index], item_id, now)
            elif op_type == "remove_item":
                item_id = str(operation.get("id") or "").strip()
                items = [item for item in items if item.id != item_id]
            elif op_type == "reorder_items":
                ordered_ids = [str(item) for item in operation.get("ordered_ids", operation.get("orderedIds", []))]
                if not ordered_ids:
                    continue
                order_map = {item_id: index for index, item_id in enumerate(ordered_ids)}
                items.sort(key=lambda item: order_map.get(item.id, len(order_map)))
            elif op_type == "replace_all":
                items = self._replace_workboard_items(items, operation.get("items") or [], now)
        return items

    def _materialize_item(
        self,
        raw_item: dict[str, Any],
        previous: WorkItem | None,
        item_id: str,
        now: str,
    ) -> WorkItem:
        title = str(raw_item.get("title") or (previous.title if previous else "")).strip()
        if not title:
            title = previous.title if previous else "Untitled"
        status = str(raw_item.get("status") or (previous.status if previous else "pending"))
        if status not in {"pending", "in_progress", "completed", "blocked", "cancelled"}:
            status = previous.status if previous else "pending"
        priority = str(raw_item.get("priority") or (previous.priority if previous else "medium"))
        if priority not in {"low", "medium", "high"}:
            priority = previous.priority if previous else "medium"
        completed_at = previous.completed_at if previous else None
        if status == "completed" and not completed_at:
            completed_at = now
        if status != "completed":
            completed_at = None
        return WorkItem(
            id=item_id,
            title=title,
            active_form=str(raw_item.get("active_form") or raw_item.get("activeForm") or previous.active_form if previous else title),
            status=status,  # type: ignore[arg-type]
            priority=priority,  # type: ignore[arg-type]
            owner=str(raw_item.get("owner") or (previous.owner if previous else "assistant")),
            depends_on=[str(item) for item in raw_item.get("depends_on", raw_item.get("dependsOn", previous.depends_on if previous else []))],
            blocked_by=[str(item) for item in raw_item.get("blocked_by", raw_item.get("blockedBy", previous.blocked_by if previous else []))],
            notes=str(raw_item.get("notes")) if raw_item.get("notes") not in (None, "") else (previous.notes if previous else None),
            source=str(raw_item.get("source") or (previous.source if previous else "assistant")),
            evidence_refs=[str(item) for item in raw_item.get("evidence_refs", raw_item.get("evidenceRefs", previous.evidence_refs if previous else []))],
            created_at=previous.created_at if previous else now,
            updated_at=now,
            completed_at=completed_at,
        )

    def _derive_workboard_status(self, items: list[WorkItem], explicit_status: str | None = None) -> str:
        if explicit_status in {"idle", "active", "completed", "blocked"}:
            return explicit_status
        if not items:
            return "idle"
        if all(item.status == "completed" for item in items):
            return "completed"
        if any(item.status == "blocked" for item in items):
            return "blocked"
        return "active"

    def get_elicitation(self, session: AgentSession) -> ElicitationState:
        return runtime_state_store.load(
            session,
            "elicitation",
            ElicitationState,
            lambda: ElicitationState(session_id=session.session_id),
        )

    def save_elicitation(self, session: AgentSession, state: ElicitationState) -> ElicitationState:
        runtime_state_store.save(session, "elicitation", state)
        return state

    def request_user_input(self, session: AgentSession, payload: dict[str, Any]) -> ElicitationRequest:
        state = self.get_elicitation(session)
        now = utc_now_iso()
        if state.pending is not None:
            state.pending.status = "cancelled"
            state.pending.cancelled_at = now
            state.pending.updated_at = now
            state.history.append(state.pending)

        questions: list[ElicitationQuestion] = []
        for raw_question in payload.get("questions") or []:
            options = [
                ElicitationOption(
                    label=str(option.get("label") or "").strip(),
                    description=str(option.get("description") or "").strip() or None,
                )
                for option in raw_question.get("options") or []
                if str(option.get("label") or "").strip()
            ]
            questions.append(
                ElicitationQuestion(
                    id=str(raw_question.get("id") or f"q_{uuid.uuid4().hex[:8]}"),
                    header=str(raw_question.get("header") or "Question").strip(),
                    question=str(raw_question.get("question") or "").strip(),
                    options=options,
                    multi_select=bool(raw_question.get("multi_select", raw_question.get("multiSelect", False))),
                    allow_other=bool(raw_question.get("allow_other", raw_question.get("allowOther", True))),
                    allow_notes=bool(raw_question.get("allow_notes", raw_question.get("allowNotes", False))),
                )
            )

        kind = str(payload.get("kind") or "clarification")
        if kind not in {"clarification", "confirmation", "decision", "missing_info", "approval"}:
            kind = "clarification"

        request = ElicitationRequest(
            id=str(payload.get("id") or f"ask_{uuid.uuid4().hex[:12]}"),
            kind=kind,  # type: ignore[arg-type]
            title=str(payload.get("title") or "Need your input").strip(),
            blocking=bool(payload.get("blocking", True)),
            source_agent=str(payload.get("source_agent") or payload.get("sourceAgent") or "root"),
            related_work_items=[str(item) for item in payload.get("related_work_items", payload.get("relatedWorkItems", []))],
            questions=questions,
            preview_text=str(payload.get("preview_text") or payload.get("previewText") or "").strip() or None,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        state.pending = request
        state.revision += 1
        state.updated_at = now
        self.save_elicitation(session, state)
        return request

    def cancel_pending_elicitation(self, session: AgentSession, request_id: str) -> ElicitationState:
        state = self.get_elicitation(session)
        now = utc_now_iso()
        if state.pending and state.pending.id == request_id:
            state.pending.status = "cancelled"
            state.pending.cancelled_at = now
            state.pending.updated_at = now
            state.history.append(state.pending)
            state.pending = None
            state.revision += 1
            state.updated_at = now
            self.save_elicitation(session, state)
        return state

    def resolve_elicitation(
        self,
        session: AgentSession,
        request_id: str,
        responses: list[dict[str, Any]],
    ) -> tuple[ElicitationState, str]:
        state = self.get_elicitation(session)
        if state.pending is None or state.pending.id != request_id:
            raise RuntimeError("pending elicitation not found")

        pending = state.pending
        answers_by_question = {str(item.get("question_id")): item for item in responses}
        resolved_answers: list[ElicitationAnswer] = []

        for question in pending.questions:
            answer_payload = answers_by_question.get(question.id, {})
            selected_options = [str(item) for item in answer_payload.get("selected_options", answer_payload.get("selectedOptions", []))]
            other_text = str(answer_payload.get("other_text") or answer_payload.get("otherText") or "").strip() or None
            notes = str(answer_payload.get("notes") or "").strip() or None

            if not question.multi_select and len(selected_options) > 1:
                raise RuntimeError(f"question {question.id} does not allow multiple selections")

            allowed_labels = {option.label for option in question.options}
            if any(label not in allowed_labels for label in selected_options):
                raise RuntimeError(f"question {question.id} contains unsupported option")
            if other_text and not question.allow_other:
                raise RuntimeError(f"question {question.id} does not allow custom answers")
            if notes and not question.allow_notes:
                raise RuntimeError(f"question {question.id} does not allow notes")
            if not selected_options and not other_text and not notes:
                raise RuntimeError(f"question {question.id} is missing an answer")

            rendered_parts = [*selected_options]
            if other_text:
                rendered_parts.append(other_text)
            if notes:
                rendered_parts.append(f"notes: {notes}")
            resolved_answers.append(
                ElicitationAnswer(
                    question_id=question.id,
                    selected_options=selected_options,
                    other_text=other_text,
                    notes=notes,
                    rendered_answer="; ".join(rendered_parts),
                )
            )

        now = utc_now_iso()
        pending.answers = resolved_answers
        pending.status = "resolved"
        pending.updated_at = now
        pending.resolved_at = now

        resume_message_lines = [f"User resolved request: {pending.title}"]
        for question in pending.questions:
            answer = next(item for item in resolved_answers if item.question_id == question.id)
            resume_message_lines.append(f"- {question.header}: {answer.rendered_answer}")
        resume_message = "\n".join(resume_message_lines)

        state.history.append(pending)
        state.pending = None
        state.revision += 1
        state.updated_at = now
        self.save_elicitation(session, state)
        return state, resume_message


runtime_state_service = RuntimeStateService()
