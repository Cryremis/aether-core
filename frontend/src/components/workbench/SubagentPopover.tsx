import { useEffect, useRef } from "react";

import type { SubagentRunSummary } from "../../api/client";
import { WorkbenchIcons as Icons } from "./WorkbenchIcons";

type SubagentPopoverProps = {
  subagents: SubagentRunSummary[];
  open: boolean;
  activeChildSessionId?: string | null;
  onOpenChange: (open: boolean) => void;
  onSelect: (subagent: SubagentRunSummary) => void;
  onStop: (subagent: SubagentRunSummary) => void;
  onDestroy: (subagent: SubagentRunSummary) => void;
};

export function SubagentPopover({
  subagents,
  open,
  activeChildSessionId,
  onOpenChange,
  onSelect,
  onStop,
  onDestroy,
}: SubagentPopoverProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const runningCount = subagents.filter((item) => item.status === "running").length;

  useEffect(() => {
    if (!open) return;
    const handler = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        onOpenChange(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onOpenChange, open]);

  if (subagents.length === 0) return null;

  return (
    <div className="subagent-popover" ref={containerRef}>
      <button
        type="button"
        className={`subagent-toggle ${open ? "active" : ""} ${runningCount > 0 ? "running" : ""}`}
        onClick={() => onOpenChange(!open)}
        aria-pressed={open}
        aria-label={runningCount > 0 ? `${runningCount} 个子代理运行中` : "子代理"}
        title={runningCount > 0 ? `${runningCount} 个子代理运行中` : "子代理"}
      >
        <Icons.User />
        <span className="subagent-toggle__count">{subagents.length}</span>
      </button>
      {open ? (
        <div className="subagent-popover__panel">
          <header className="subagent-popover__header">
            <span>子代理</span>
            <strong>{runningCount > 0 ? `${runningCount}/${subagents.length} 运行中` : `${subagents.length} 个`}</strong>
          </header>
          <div className="subagent-popover__list">
            {subagents.map((subagent) => (
              <div
                key={subagent.subagent_run_id}
                className={`subagent-row ${activeChildSessionId === subagent.child_session_id ? "active" : ""} subagent-row--${subagent.status}`}
              >
                <button type="button" className="subagent-row__main" onClick={() => onSelect(subagent)}>
                  <span className={`subagent-row__dot subagent-row__dot--${subagent.status}`} />
                  <strong>{subagent.name}</strong>
                  <span className="subagent-row__action">{subagent.current_action?.label || subagent.task}</span>
                </button>
                <div className="subagent-row__actions">
                  {subagent.status === "running" ? (
                    <button type="button" onClick={() => onStop(subagent)} title="停止">
                      <Icons.Stop />
                    </button>
                  ) : null}
                  <button type="button" className="danger" onClick={() => onDestroy(subagent)} title="销毁">
                    <Icons.Close />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
