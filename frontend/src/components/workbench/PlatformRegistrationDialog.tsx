import { FormEvent, useMemo, useState } from "react";

import type { PlatformRegistrationRequestSummary } from "../../api/client";

type PlatformRegistrationDialogProps = {
  open: boolean;
  busy: boolean;
  error: string;
  recentRequests: PlatformRegistrationRequestSummary[];
  onClose: () => void;
  onSubmit: (payload: {
    platform_key: string;
    display_name: string;
    description: string;
    justification: string;
  }) => Promise<void>;
};

function formatStatus(status: PlatformRegistrationRequestSummary["status"]) {
  if (status === "pending") return "待审批";
  if (status === "approved") return "已通过";
  if (status === "rejected") return "已驳回";
  if (status === "returned") return "待补充";
  return "已取消";
}

export function PlatformRegistrationDialog({
  open,
  busy,
  error,
  recentRequests,
  onClose,
  onSubmit,
}: PlatformRegistrationDialogProps) {
  const [platformKey, setPlatformKey] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [justification, setJustification] = useState("");

  const canSubmit = useMemo(() => {
    return Boolean(platformKey.trim() && displayName.trim() && justification.trim()) && !busy;
  }, [busy, displayName, justification, platformKey]);

  if (!open) {
    return null;
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }
    await onSubmit({
      platform_key: platformKey.trim().toLowerCase(),
      display_name: displayName.trim(),
      description: description.trim(),
      justification: justification.trim(),
    });
    setPlatformKey("");
    setDisplayName("");
    setDescription("");
    setJustification("");
  };

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <section className="dialog-card platform-request-dialog" onClick={(event) => event.stopPropagation()}>
        <div className="dialog-card__header">
          <div>
            <span className="dialog-card__eyebrow">Platform Registration</span>
            <h2>申请注册新平台</h2>
            <p>申请提交后，系统管理员审批通过即自动生效，你会成为该平台负责人。</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </div>

        <form className="dialog-form" onSubmit={handleSubmit}>
          <label>
            <span>平台标识</span>
            <input
              value={platformKey}
              onChange={(event) => setPlatformKey(event.target.value)}
              placeholder="例如 marketing-copilot"
            />
          </label>

          <label>
            <span>平台名称</span>
            <input
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder="例如 Marketing Copilot"
            />
          </label>

          <label>
            <span>平台说明</span>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="这个平台面向谁，主要解决什么问题。"
            />
          </label>

          <label>
            <span>申请理由</span>
            <textarea
              value={justification}
              onChange={(event) => setJustification(event.target.value)}
              placeholder="为什么需要这个平台，预计会做哪些配置与接入。"
            />
          </label>

          {error ? <div className="dialog-error">{error}</div> : null}

          <div className="dialog-actions">
            <button type="button" className="action-button sidebar-footer__button sidebar-footer__button--ghost" onClick={onClose}>
              关闭
            </button>
            <button type="submit" className="action-button sidebar-footer__button" disabled={!canSubmit}>
              {busy ? "提交中..." : "提交申请"}
            </button>
          </div>
        </form>

        <div className="dialog-card__history">
          <div className="dialog-card__history-head">
            <h3>我的申请记录</h3>
            <span>{recentRequests.length}</span>
          </div>
          <div className="dialog-card__history-list">
            {recentRequests.length === 0 ? (
              <div className="empty-state">你还没有提交过平台注册申请。</div>
            ) : (
              recentRequests.slice(0, 6).map((item) => (
                <article key={item.request_id} className="request-history-card">
                  <div className="request-history-card__head">
                    <strong>{item.display_name}</strong>
                    <span className={`request-status request-status--${item.status}`}>{formatStatus(item.status)}</span>
                  </div>
                  <p>{item.platform_key}</p>
                  {item.review_comment ? <p className="request-history-card__comment">{item.review_comment}</p> : null}
                </article>
              ))
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
