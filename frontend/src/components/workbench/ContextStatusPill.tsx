import { formatTokenCount } from "../../pages/workbench/markdown";
import type { ContextStatus } from "../../pages/workbench/types";

type ContextStatusPillProps = {
  contextStatus: ContextStatus | null;
};

export function ContextStatusPill({ contextStatus }: ContextStatusPillProps) {
  if (!contextStatus) return null;

  const contextUsagePercent = Math.max(0, Math.min(100, Math.round(contextStatus.percentUsed ?? 0)));
  const contextStateTone = contextStatus.state ?? "idle";

  return (
    <div className={`context-pill context-pill--${contextStateTone}`}>
      <div className="context-pill__compact">
        <span className="context-pill__model" title={contextStatus.model || "等待首次对话"}>{contextStatus.model || "Model"}</span>
        <span className="context-pill__usage">
          {formatTokenCount(contextStatus.estimatedTokens)} / {formatTokenCount(contextStatus.effectiveWindow || contextStatus.contextWindow)}
        </span>
      </div>
      <div className="context-pill__popup">
        <div className="context-pill__detail">
          <div className="context-pill__meter">
            <div className="context-pill__meter-bar" style={{ width: `${contextUsagePercent}%` }} />
          </div>
          <div className="context-pill__row">
            <span className="context-pill__row-label">Usage</span>
            <span className="context-pill__row-value">{contextUsagePercent}%</span>
          </div>
          <div className="context-pill__row">
            <span className="context-pill__row-label">Target</span>
            <span className="context-pill__row-value">{formatTokenCount(contextStatus.targetInputTokens)}</span>
          </div>
          <div className="context-pill__row">
            <span className="context-pill__row-label">Block</span>
            <span className="context-pill__row-value">{formatTokenCount(contextStatus.blockingLimit)}</span>
          </div>
          {contextStatus.detail ? <div className="context-pill__detail-text">{contextStatus.detail}</div> : null}
        </div>
      </div>
    </div>
  );
}
