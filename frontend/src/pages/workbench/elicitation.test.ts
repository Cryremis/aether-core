import { expect, it } from "vitest";
import type { ElicitationRequest, ElicitationState } from "../../api/client";
import { applyLegacyQuestion, mergeElicitationSnapshot } from "./elicitation";

const request: ElicitationRequest = {
  id: "old", title: "确认", kind: "confirmation", blocking: true, related_work_items: [],
  status: "pending", questions: [], answers: [], created_at: "2026-11-10", updated_at: "2026-11-10",
};
const earlier: ElicitationState = {
  session_id: "session", revision: 1, pending: request, history: [], updated_at: request.updated_at,
};
const resolved: ElicitationState = {
  ...earlier, revision: 2, pending: null, history: [{ ...request, status: "resolved" }],
};

it("keeps an answered question closed when the old snapshot or legacy event is replayed", () => {
  expect(mergeElicitationSnapshot(resolved, earlier)).toBe(resolved);
  expect(applyLegacyQuestion(resolved, "session", request)).toBe(resolved);
});

it("accepts a new question and ignores replayed earlier revisions", () => {
  const newer = { ...resolved, revision: 3, pending: { ...request, id: "next" } };
  expect(mergeElicitationSnapshot(resolved, newer)).toBe(newer);
  expect(mergeElicitationSnapshot(newer, resolved)).toBe(newer);
});
