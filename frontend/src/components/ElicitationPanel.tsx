import { useEffect, useMemo, useState } from "react";

import type { ElicitationRequest, ElicitationResponseItem } from "../api/client";

type DraftAnswer = {
  selected_options: string[];
  other_text: string;
  notes: string;
};

type ElicitationPanelProps = {
  request: ElicitationRequest | null;
  busy: boolean;
  onSubmit: (responses: ElicitationResponseItem[]) => void;
};

function kindLabel(kind: ElicitationRequest["kind"]) {
  switch (kind) {
    case "confirmation":
      return "确认";
    case "decision":
      return "决策";
    case "missing_info":
      return "补充信息";
    case "approval":
      return "授权";
    default:
      return "澄清";
  }
}

export function ElicitationPanel({ request, busy, onSubmit }: ElicitationPanelProps) {
  const [drafts, setDrafts] = useState<Record<string, DraftAnswer>>({});

  useEffect(() => {
    if (!request) {
      setDrafts({});
      return;
    }
    const nextDrafts: Record<string, DraftAnswer> = {};
    request.questions.forEach((question) => {
      nextDrafts[question.id] = {
        selected_options: [],
        other_text: "",
        notes: "",
      };
    });
    setDrafts(nextDrafts);
  }, [request?.id]);

  const canSubmit = useMemo(() => {
    if (!request) return false;
    return request.questions.every((question) => {
      const draft = drafts[question.id];
      if (!draft) return false;
      return draft.selected_options.length > 0 || draft.other_text.trim().length > 0 || draft.notes.trim().length > 0;
    });
  }, [drafts, request]);

  if (!request) return null;

  return (
    <section className="elicitation-panel">
      <div className="elicitation-panel__header">
        <div>
          <span className="elicitation-panel__eyebrow">需要你的回答</span>
          <h3>{request.title}</h3>
          {request.preview_text ? <p>{request.preview_text}</p> : null}
        </div>
        <span className={`elicitation-panel__kind elicitation-panel__kind--${request.kind}`}>{kindLabel(request.kind)}</span>
      </div>
      <div className="elicitation-panel__questions">
        {request.questions.map((question) => {
          const draft = drafts[question.id] ?? { selected_options: [], other_text: "", notes: "" };
          return (
            <article key={question.id} className="elicitation-question-card">
              <header className="elicitation-question-card__header">
                <span>{question.header}</span>
                <strong>{question.question}</strong>
              </header>
              {question.options.length > 0 ? (
                <div className="elicitation-question-card__options">
                  {question.options.map((option) => {
                    const active = draft.selected_options.includes(option.label);
                    return (
                      <button
                        key={option.label}
                        type="button"
                        className={`elicitation-option ${active ? "is-active" : ""}`}
                        onClick={() => {
                          setDrafts((current) => {
                            const currentDraft = current[question.id] ?? { selected_options: [], other_text: "", notes: "" };
                            let nextSelected = currentDraft.selected_options;
                            if (question.multi_select) {
                              nextSelected = active
                                ? currentDraft.selected_options.filter((label) => label !== option.label)
                                : [...currentDraft.selected_options, option.label];
                            } else {
                              nextSelected = active ? [] : [option.label];
                            }
                            return {
                              ...current,
                              [question.id]: { ...currentDraft, selected_options: nextSelected },
                            };
                          });
                        }}
                      >
                        <strong>{option.label}</strong>
                        {option.description ? <span>{option.description}</span> : null}
                      </button>
                    );
                  })}
                </div>
              ) : null}
              {question.allow_other ? (
                <textarea
                  className="elicitation-textarea"
                  value={draft.other_text}
                  onChange={(event) =>
                    setDrafts((current) => ({
                      ...current,
                      [question.id]: {
                        ...(current[question.id] ?? { selected_options: [], other_text: "", notes: "" }),
                        other_text: event.target.value,
                      },
                    }))
                  }
                  placeholder="补充你的回答"
                  rows={2}
                />
              ) : null}
              {question.allow_notes ? (
                <textarea
                  className="elicitation-textarea elicitation-textarea--notes"
                  value={draft.notes}
                  onChange={(event) =>
                    setDrafts((current) => ({
                      ...current,
                      [question.id]: {
                        ...(current[question.id] ?? { selected_options: [], other_text: "", notes: "" }),
                        notes: event.target.value,
                      },
                    }))
                  }
                  placeholder="补充说明"
                  rows={2}
                />
              ) : null}
            </article>
          );
        })}
      </div>
      <div className="elicitation-panel__footer">
        <span>{request.blocking ? "AI 会在你回答后继续执行" : "这条回答会立即传给 AI 处理"}</span>
        <button
          type="button"
          className="elicitation-panel__submit"
          disabled={busy || !canSubmit}
          onClick={() =>
            onSubmit(
              request.questions.map((question) => {
                const draft = drafts[question.id] ?? { selected_options: [], other_text: "", notes: "" };
                return {
                  question_id: question.id,
                  selected_options: draft.selected_options,
                  other_text: draft.other_text.trim() || null,
                  notes: draft.notes.trim() || null,
                };
              }),
            )
          }
        >
          {busy ? "提交中..." : "提交回答"}
        </button>
      </div>
    </section>
  );
}
