import { useEffect, useMemo, useState } from "react";

import type { WorkItem, WorkItemStatus, WorkboardOperation, WorkboardState } from "../api/client";
import { WorkbenchIcons as Icons } from "./workbench/WorkbenchIcons";

type WorkboardDockProps = {
  workboard: WorkboardState | null;
  visible: boolean;
  busy?: boolean;
  onToggle: () => void;
  onApplyOps: (ops: WorkboardOperation[]) => Promise<void>;
};

type DraftState = {
  title: string;
  notes: string;
  priority: "low" | "medium" | "high";
  status: WorkItemStatus;
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

function createDraft(item?: WorkItem): DraftState {
  return {
    title: item?.active_form || item?.title || "",
    notes: item?.notes || "",
    priority: item?.priority || "medium",
    status: item?.status || "pending",
  };
}

export function WorkboardDock({ workboard, visible, busy = false, onToggle, onApplyOps }: WorkboardDockProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [shouldRender, setShouldRender] = useState(visible);
  const [error, setError] = useState("");
  const [isAdding, setIsAdding] = useState(false);
  const [addDraft, setAddDraft] = useState<DraftState>(() => createDraft());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<DraftState>(() => createDraft());

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

  useEffect(() => {
    if (!workboard?.items.some((item) => item.id === editingId)) {
      setEditingId(null);
    }
  }, [editingId, workboard]);

  if (!shouldRender) return null;

  const handleToggleCollapse = () => {
    setCollapsed((v) => !v);
  };

  const applyOps = async (ops: WorkboardOperation[]) => {
    try {
      setError("");
      await onApplyOps(ops);
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新任务清单失败");
      throw err;
    }
  };

  const handleAdd = async () => {
    const title = addDraft.title.trim();
    if (!title) {
      setError("请先输入任务标题");
      return;
    }
    await applyOps([
      {
        op: "add_item",
        title,
        notes: addDraft.notes.trim() || null,
        priority: addDraft.priority,
        status: addDraft.status,
        source: "user",
        owner: "user",
      },
    ]);
    setAddDraft(createDraft());
    setIsAdding(false);
  };

  const handleDelete = async (itemId: string) => {
    await applyOps([{ op: "remove_item", id: itemId }]);
  };

  const handleMove = async (itemId: string, direction: -1 | 1) => {
    const items = workboard?.items ?? [];
    const currentIndex = items.findIndex((item) => item.id === itemId);
    const nextIndex = currentIndex + direction;
    if (currentIndex < 0 || nextIndex < 0 || nextIndex >= items.length) return;
    const orderedIds = [...items.map((item) => item.id)];
    const [moved] = orderedIds.splice(currentIndex, 1);
    orderedIds.splice(nextIndex, 0, moved);
    await applyOps([{ op: "reorder_items", ordered_ids: orderedIds }]);
  };

  const startEdit = (item: WorkItem) => {
    setEditingId(item.id);
    setEditDraft(createDraft(item));
    setError("");
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditDraft(createDraft());
  };

  const saveEdit = async (itemId: string) => {
    const title = editDraft.title.trim();
    if (!title) {
      setError("任务标题不能为空");
      return;
    }
    await applyOps([
      {
        op: "update_item",
        id: itemId,
        title,
        notes: editDraft.notes.trim() || null,
        priority: editDraft.priority,
        status: editDraft.status,
        source: "user",
        owner: "user",
      },
    ]);
    cancelEdit();
  };

  const quickSetStatus = async (item: WorkItem, status: WorkItemStatus) => {
    await applyOps([
      {
        op: "update_item",
        id: item.id,
        status,
        source: "user",
        owner: "user",
      },
    ]);
  };

  if (!workboard || metrics.total === 0) {
    return (
      <section className={`workboard-dock ${visible ? "is-visible" : "is-hidden"} is-empty is-collapsed`}>
        <div className="workboard-dock__header">
          <button type="button" className="workboard-dock__header-left" onClick={handleToggleCollapse}>
            <span className="workboard-dock__eyebrow">任务清单</span>
            <span className="workboard-dock__empty-text">暂无追踪任务</span>
          </button>
          <div className="workboard-dock__header-right">
            <button
              type="button"
              className="workboard-dock__action-btn"
              onClick={() => {
                setCollapsed(false);
                setIsAdding(true);
              }}
              title="新增任务"
            >
              <Icons.Plus />
            </button>
            <button type="button" className="workboard-dock__toggle-btn" onClick={onToggle} title="隐藏">
              <Icons.Close />
            </button>
          </div>
        </div>
        {isAdding ? (
          <div className="workboard-dock__body workboard-dock__body--always-open">
            <div className="workboard-dock__editor">
              <input
                className="workboard-dock__input"
                placeholder="输入任务标题"
                value={addDraft.title}
                onChange={(event) => setAddDraft((current) => ({ ...current, title: event.target.value }))}
              />
              <textarea
                className="workboard-dock__textarea"
                placeholder="备注（可选）"
                value={addDraft.notes}
                onChange={(event) => setAddDraft((current) => ({ ...current, notes: event.target.value }))}
              />
              <div className="workboard-dock__editor-row">
                <select
                  className="workboard-dock__select"
                  value={addDraft.priority}
                  onChange={(event) => setAddDraft((current) => ({ ...current, priority: event.target.value as DraftState["priority"] }))}
                >
                  <option value="low">低优先级</option>
                  <option value="medium">中优先级</option>
                  <option value="high">高优先级</option>
                </select>
                <select
                  className="workboard-dock__select"
                  value={addDraft.status}
                  onChange={(event) => setAddDraft((current) => ({ ...current, status: event.target.value as WorkItemStatus }))}
                >
                  <option value="pending">待处理</option>
                  <option value="in_progress">进行中</option>
                  <option value="completed">已完成</option>
                  <option value="blocked">受阻</option>
                  <option value="cancelled">已取消</option>
                </select>
              </div>
              {error ? <div className="workboard-dock__error">{error}</div> : null}
              <div className="workboard-dock__editor-actions">
                <button type="button" className="workboard-dock__secondary-btn" onClick={() => setIsAdding(false)} disabled={busy}>
                  取消
                </button>
                <button type="button" className="workboard-dock__primary-btn" onClick={() => void handleAdd()} disabled={busy}>
                  保存
                </button>
              </div>
            </div>
          </div>
        ) : null}
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
          <span className="workboard-dock__summary">
            {metrics.active > 0 ? `进行中 ${metrics.active}` : metrics.blocked > 0 ? `受阻 ${metrics.blocked}` : "稳定"}
          </span>
          <button
            type="button"
            className="workboard-dock__action-btn"
            onClick={() => {
              setCollapsed(false);
              setIsAdding((current) => !current);
              setAddDraft(createDraft());
            }}
            title="新增任务"
          >
            <Icons.Plus />
          </button>
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
        {isAdding ? (
          <div className="workboard-dock__editor">
            <input
              className="workboard-dock__input"
              placeholder="输入任务标题"
              value={addDraft.title}
              onChange={(event) => setAddDraft((current) => ({ ...current, title: event.target.value }))}
            />
            <textarea
              className="workboard-dock__textarea"
              placeholder="备注（可选）"
              value={addDraft.notes}
              onChange={(event) => setAddDraft((current) => ({ ...current, notes: event.target.value }))}
            />
            <div className="workboard-dock__editor-row">
              <select
                className="workboard-dock__select"
                value={addDraft.priority}
                onChange={(event) => setAddDraft((current) => ({ ...current, priority: event.target.value as DraftState["priority"] }))}
              >
                <option value="low">低优先级</option>
                <option value="medium">中优先级</option>
                <option value="high">高优先级</option>
              </select>
              <select
                className="workboard-dock__select"
                value={addDraft.status}
                onChange={(event) => setAddDraft((current) => ({ ...current, status: event.target.value as WorkItemStatus }))}
              >
                <option value="pending">待处理</option>
                <option value="in_progress">进行中</option>
                <option value="completed">已完成</option>
                <option value="blocked">受阻</option>
                <option value="cancelled">已取消</option>
              </select>
            </div>
            <div className="workboard-dock__editor-actions">
              <button type="button" className="workboard-dock__secondary-btn" onClick={() => setIsAdding(false)} disabled={busy}>
                取消
              </button>
              <button type="button" className="workboard-dock__primary-btn" onClick={() => void handleAdd()} disabled={busy}>
                保存
              </button>
            </div>
          </div>
        ) : null}
        {error ? <div className="workboard-dock__error">{error}</div> : null}
        <div className="workboard-dock__items">
          {workboard.items.map((item, index) => {
            const isEditing = editingId === item.id;
            return (
              <article key={item.id} className={`workboard-item workboard-item--${item.status}`}>
                {isEditing ? (
                  <div className="workboard-dock__editor workboard-dock__editor--inline">
                    <input
                      className="workboard-dock__input"
                      value={editDraft.title}
                      onChange={(event) => setEditDraft((current) => ({ ...current, title: event.target.value }))}
                    />
                    <textarea
                      className="workboard-dock__textarea"
                      value={editDraft.notes}
                      onChange={(event) => setEditDraft((current) => ({ ...current, notes: event.target.value }))}
                    />
                    <div className="workboard-dock__editor-row">
                      <select
                        className="workboard-dock__select"
                        value={editDraft.priority}
                        onChange={(event) => setEditDraft((current) => ({ ...current, priority: event.target.value as DraftState["priority"] }))}
                      >
                        <option value="low">低优先级</option>
                        <option value="medium">中优先级</option>
                        <option value="high">高优先级</option>
                      </select>
                      <select
                        className="workboard-dock__select"
                        value={editDraft.status}
                        onChange={(event) => setEditDraft((current) => ({ ...current, status: event.target.value as WorkItemStatus }))}
                      >
                        <option value="pending">待处理</option>
                        <option value="in_progress">进行中</option>
                        <option value="completed">已完成</option>
                        <option value="blocked">受阻</option>
                        <option value="cancelled">已取消</option>
                      </select>
                    </div>
                    <div className="workboard-dock__editor-actions">
                      <button type="button" className="workboard-dock__secondary-btn" onClick={cancelEdit} disabled={busy}>
                        取消
                      </button>
                      <button type="button" className="workboard-dock__primary-btn" onClick={() => void saveEdit(item.id)} disabled={busy}>
                        保存
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="workboard-item__main">
                      <div className="workboard-item__title-row">
                        <button
                          type="button"
                          className={`workboard-item__status workboard-item__status--${item.status}`}
                          onClick={() => void quickSetStatus(item, item.status === "completed" ? "pending" : "completed")}
                          disabled={busy}
                        >
                          {statusLabel(item.status)}
                        </button>
                        <strong>{item.active_form || item.title}</strong>
                      </div>
                      {item.notes ? <p className="workboard-item__notes">{item.notes}</p> : null}
                      <div className="workboard-item__quick-actions">
                        <button type="button" className="workboard-item__link-btn" onClick={() => startEdit(item)} disabled={busy}>
                          编辑
                        </button>
                        <button type="button" className="workboard-item__link-btn" onClick={() => void handleMove(item.id, -1)} disabled={busy || index === 0}>
                          上移
                        </button>
                        <button
                          type="button"
                          className="workboard-item__link-btn"
                          onClick={() => void handleMove(item.id, 1)}
                          disabled={busy || index === workboard.items.length - 1}
                        >
                          下移
                        </button>
                        <button type="button" className="workboard-item__link-btn is-danger" onClick={() => void handleDelete(item.id)} disabled={busy}>
                          删除
                        </button>
                      </div>
                    </div>
                    <div className="workboard-item__meta">
                      <span>{priorityLabel(item.priority)}</span>
                      {item.owner ? <span>{item.owner === "assistant" ? "AI" : item.owner}</span> : null}
                      <span>{item.source === "user" ? "手动" : "自动"}</span>
                    </div>
                  </>
                )}
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}
