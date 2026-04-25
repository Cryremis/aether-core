import { useEffect, useMemo, useState } from "react";

import type { WorkboardState } from "../api/client";
import { WorkbenchIcons as Icons } from "./workbench/WorkbenchIcons";

type WorkboardDockProps = {
  workboard: WorkboardState | null;
  visible: boolean;
  onToggle: () => void;
};

function statusLabel(status: string) {
  switch (status) {
    case "in_progress":
      return "进行中";
    case "completed":
      return "已完成";
    case "blocked":
      return "受阻";
    case "cancelled":
      return "已取消";
    default:
      return "待处理";
  }
}

function priorityLabel(priority: string) {
  switch (priority) {
    case "high":
      return "高优先级";
    case "low":
      return "低优先级";
    default:
      return "中优先级";
  }
}

export function WorkboardDock({ workboard, visible, onToggle }: WorkboardDockProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [shouldRender, setShouldRender] = useState(visible);

  const metrics = useMemo(() => {
    const items = workboard?.items ?? [];
    const total = items.length;
    const completed = items.filter((item) => item.status === "completed").length;
    const active = items.filter((item) => item.status === "in_progress").length;
    const blocked = items.filter((item) => item.status === "blocked").length;
    const percent = total > 0 ? Math.round((completed / total) * 100) : 0;
    const allDone = total > 0 && completed === total;
    return { total, completed, active, blocked, percent, allDone };
  }, [workboard]);

  useEffect(() => {
    if (visible) {
      setShouldRender(true);
    } else {
      const timer = setTimeout(() => setShouldRender(false), 300);
      return () => clearTimeout(timer);
    }
  }, [visible]);

  useEffect(() => {
    if (!workboard || metrics.total === 0) return;
    if (metrics.allDone) {
      setCollapsed(true);
    }
  }, [workboard?.revision, metrics.allDone, metrics.total]);

  if (!shouldRender) return null;

  const handleToggleCollapse = () => {
    setCollapsed((v) => !v);
  };

  if (!workboard || metrics.total === 0) {
    return (
      <section className={`workboard-dock ${visible ? "is-visible" : "is-hidden"} is-empty is-collapsed`}>
        <div className="workboard-dock__header">
          <button type="button" className="workboard-dock__header-left" onClick={handleToggleCollapse}>
            <span className="workboard-dock__eyebrow">任务清单</span>
            <span className="workboard-dock__empty-text">暂无追踪任务</span>
          </button>
          <button type="button" className="workboard-dock__toggle-btn" onClick={onToggle} title="隐藏">
            <Icons.Close />
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className={`workboard-dock ${visible ? "is-visible" : "is-hidden"} ${collapsed ? "is-collapsed" : "is-expanded"}`}>
      <div className="workboard-dock__header">
        <button type="button" className="workboard-dock__header-left" onClick={handleToggleCollapse}>
          <span className="workboard-dock__eyebrow">任务清单</span>
          <span className="workboard-dock__count">{metrics.completed} / {metrics.total}</span>
          <div className="workboard-dock__mini-meter">
            <div className="workboard-dock__mini-meter-bar" style={{ width: `${metrics.percent}%` }} />
          </div>
        </button>
        <div className="workboard-dock__header-right">
          {metrics.allDone ? (
            <span className="workboard-dock__complete-badge">
              <Icons.Check />
            </span>
          ) : null}
          <button type="button" className="workboard-dock__toggle-btn" onClick={onToggle} title="隐藏面板">
            <Icons.Close />
          </button>
        </div>
      </div>
      <div className="workboard-dock__body">
        <div className="workboard-dock__items">
          {workboard.items.map((item) => (
            <article key={item.id} className={`workboard-item workboard-item--${item.status}`}>
              <div className="workboard-item__main">
                <div className="workboard-item__title-row">
                  <span className={`workboard-item__status workboard-item__status--${item.status}`}>
                    {statusLabel(item.status)}
                  </span>
                  <strong>{item.active_form || item.title}</strong>
                </div>
                {item.notes ? <p className="workboard-item__notes">{item.notes}</p> : null}
              </div>
              <div className="workboard-item__meta">
                <span>{priorityLabel(item.priority)}</span>
                {item.owner ? <span>{item.owner === "assistant" ? "AI" : item.owner}</span> : null}
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}