import { useEffect, useState } from "react";

import type { ElicitationRequest, ElicitationResponseItem } from "../api/client";
import { WorkbenchIcons as Icons } from "./workbench/WorkbenchIcons";

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
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);

  useEffect(() => {
    if (!request) {
      setDrafts({});
      setCurrentQuestionIndex(0);
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
    setCurrentQuestionIndex(0);
  }, [request?.id]);

  if (!request || request.questions.length === 0) return null;

  const questions = request.questions;
  const questionIndex = Math.min(currentQuestionIndex, questions.length - 1);
  const isLastQuestion = questionIndex === request.questions.length - 1;

  function buildResponses(skippedQuestionId?: string): ElicitationResponseItem[] {
    return questions.map((question) => {
      const draft = question.id === skippedQuestionId ? undefined : drafts[question.id];
      return {
        question_id: question.id,
        selected_options: draft?.selected_options ?? [],
        other_text: draft?.other_text.trim() || null,
        notes: draft?.notes.trim() || null,
      };
    });
  }

  function skipQuestion() {
    if (busy) return;
    const questionId = questions[questionIndex].id;
    setDrafts((current) => ({
      ...current,
      [questionId]: { selected_options: [], other_text: "", notes: "" },
    }));
    if (isLastQuestion) {
      onSubmit(buildResponses(questionId));
    } else {
      setCurrentQuestionIndex(questionIndex + 1);
    }
  }

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
        {request.questions.slice(questionIndex, questionIndex + 1).map((question) => {
          const draft = drafts[question.id] ?? { selected_options: [], other_text: "", notes: "" };
          return (
            <article key={question.id} className="elicitation-question-card">
              <header className="elicitation-question-card__header">
                <div className="elicitation-question-card__header-left">
                  <span>{question.header}</span>
                  <strong>{question.question}</strong>
                </div>
                <span className="elicitation-question-card__counter" aria-label={`进度 ${questionIndex + 1}/${request.questions.length}`}>
                  <span>{questionIndex + 1}</span><span aria-hidden="true">/</span><span>{request.questions.length}</span>
                </span>
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
                        disabled={busy}
                        aria-pressed={active}
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
                  disabled={busy}
                  aria-label="补充你的回答"
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
                  disabled={busy}
                  aria-label="补充说明"
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
        <div className="elicitation-panel__nav" role="group" aria-label="题目翻页">
          <button
            type="button"
            className="elicitation-panel__nav-btn"
            disabled={busy || questionIndex === 0}
            onClick={() => setCurrentQuestionIndex(questionIndex - 1)}
            aria-label="上一题"
            title="上一题"
          >
            <Icons.ChevronLeft />
          </button>
          <button
            type="button"
            className="elicitation-panel__nav-btn"
            disabled={busy || isLastQuestion}
            onClick={() => setCurrentQuestionIndex(questionIndex + 1)}
            aria-label="下一题"
            title="下一题"
          >
            <Icons.ChevronRight />
          </button>
        </div>
        <div className="elicitation-panel__actions">
          <button
            type="button"
            className="elicitation-panel__skip"
            disabled={busy}
            onClick={skipQuestion}
            title={isLastQuestion ? "不回答本题并提交已有回答" : "不回答本题，前往下一题"}
          >
            <Icons.Skip />
            {isLastQuestion ? "跳过并提交" : "跳过"}
          </button>
          <button
            type="button"
            className="elicitation-panel__submit"
            disabled={busy}
            onClick={() => {
              if (busy) return;
              if (!isLastQuestion) { setCurrentQuestionIndex(questionIndex + 1); return; }
              onSubmit(buildResponses());
            }}
          >
            {busy ? "提交中…" : isLastQuestion ? "提交回答" : "下一题"}
          </button>
        </div>
      </div>
    </section>
  );
}
