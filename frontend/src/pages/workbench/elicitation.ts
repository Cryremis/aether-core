import type { ElicitationRequest, ElicitationState } from "../../api/client";

export function mergeElicitationSnapshot(current: ElicitationState | null, snapshot: ElicitationState): ElicitationState {
  if (!current || current.session_id !== snapshot.session_id || snapshot.revision >= current.revision) return snapshot;
  return current;
}

export function applyLegacyQuestion(current: ElicitationState | null, sessionId: string, request: ElicitationRequest): ElicitationState {
  if (current?.session_id !== sessionId) current = null;
  if (current && (current.pending || current.history.some((item) => item.id === request.id))) return current;
  return {
    session_id: sessionId, revision: current?.revision ?? 0, pending: request,
    history: current?.history ?? [], updated_at: request.updated_at,
  };
}
