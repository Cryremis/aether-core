import { useEffect, useMemo, useState } from "react";

import type { WorkboardState } from "../api/client";

type WorkboardDockProps = {
  workboard: WorkboardState | null;
  busy: boolean;
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

function boardStatusLabel(status: string) {
  switch (status) {
    case "active":
      return "执行中";
    case "completed":
      return "已完成";
    case "blocked":
      return "受阻中";
    default:
      return "空闲";
  }
}

export function WorkboardDock({ workboard, busy }: WorkboardDockProps) {
  const [collapsed, setCollapsed] = useState(false);

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
    if (!workboard || metrics.total === 0) return;
    if (metrics.allDone) {
      setCollapsed(true);
      return;
    }
    setCollapsed(false);
  }, [workboard?.revision, metrics.allDone, metrics.total]);

  if (!workboard || metrics.total === 0) return null;

  return (
    <section className={`workboard-dock ${collapsed ? "is-collapsed" : ""}`}>
      <button type="button" className="workboard-dock__header" onClick={() => setCollapsed((value) => !value)}>
        <div className="workboard-dock__summary">
          <span className="workboard-dock__eyebrow">任务清单</span>
          <strong>{metrics.allDone ? "当前追踪任务已全部完成" : `已完成 ${metrics.completed}/${metrics.total}`}</strong>
          <span className="workboard-dock__meta">
            {busy ? "正在实时同步" : "状态已持久保存"}
            {metrics.active > 0 ? ` · ${metrics.active} 项进行中` : ""}
            {metrics.blocked > 0 ? ` · ${metrics.blocked} 项受阻` : ""}
          </span>
        </div>
        <div className="workboard-dock__right">
          <span className={`workboard-dock__status workboard-dock__status--${workboard.status}`}>{boardStatusLabel(workboard.status)}</span>
          <span className="workboard-dock__toggle">{collapsed ? "展开" : "收起"}</span>
        </div>
      </button>
      <div className="workboard-dock__meter">
        <div className="workboard-dock__meter-bar" style={{ width: `${metrics.percent}%` }} />
      </div>
      {!collapsed ? (
        <div className="workboard-dock__items">
          {workboard.items.map((item) => (
            <article key={item.id} className={`workboard-item workboard-item--${item.status}`}>
              <div className="workboard-item__main">
                <div className="workboard-item__title-row">
                  <span className={`workboard-item__status workboard-item__status--${item.status}`}>{statusLabel(item.status)}</span>
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
      ) : null}
    </section>
  );
}
