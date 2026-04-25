import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

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
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingItemId, setEditingItemId] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftState>(() => createDraft());
  const [draggingItemId, setDraggingItemId] = useState<string | null>(null);
  const [dropTargetItemId, setDropTargetItemId] = useState<string | null>(null);

  const metrics = useMemo(() => {
    const items = workboard?.items ?? [];
    const total = items.length;
    const completed = items.filter((item) => item.status === "completed").length;
    const active = items.filter((item) => item.status === "in_progress").length;
    const blocked = items.filter((item) => item.status === "blocked").length;
    const percent = total > 0 ? Math.round((completed / total) * 100) : 0;
    return { total, completed, active, blocked, percent };
  }, [workboard]);

  const allDone = metrics.total > 0 && metrics.completed === metrics.total;

  useEffect(() => {
    if (visible) {
      setShouldRender(true);
    } else {
      const timer = setTimeout(() => setShouldRender(false), 300);
      return () => clearTimeout(timer);
    }
  }, [visible]);

  useEffect(() => {
    if (!workboard?.items.some((item) => item.id === editingItemId)) {
      setEditingItemId(null);
    }
  }, [editingItemId, workboard]);

  if (!shouldRender) return null;

  const openCreateModal = () => {
    setError("");
    setEditingItemId(null);
    setDraft(createDraft());
    setIsModalOpen(true);
    setCollapsed(false);
  };

  const openEditModal = (item: WorkItem) => {
    setError("");
    setEditingItemId(item.id);
    setDraft(createDraft(item));
    setIsModalOpen(true);
    setCollapsed(false);
  };

  const closeModal = () => {
    setIsModalOpen(false);
    setEditingItemId(null);
    setDraft(createDraft());
    setError("");
  };

  const applyOps = async (ops: WorkboardOperation[]) => {
    try {
      setError("");
      await onApplyOps(ops);
    } catch (err) {
      const message = err instanceof Error ? err.message : "更新任务清单失败";
      setError(message);
      throw err;
    }
  };

  const handleSave = async () => {
    const title = draft.title.trim();
    if (!title) {
      setError("任务标题不能为空");
      return;
    }

    const payload: WorkboardOperation = editingItemId
      ? {
          op: "update_item",
          id: editingItemId,
          title,
          active_form: title,
          notes: draft.notes.trim() || null,
          priority: draft.priority,
          status: draft.status,
          source: "user",
          owner: "user",
        }
      : {
          op: "add_item",
          title,
          active_form: title,
          notes: draft.notes.trim() || null,
          priority: draft.priority,
          status: draft.status,
          source: "user",
          owner: "user",
        };

    await applyOps([payload]);
    closeModal();
  };

  const handleDelete = async (itemId: string) => {
    await applyOps([{ op: "remove_item", id: itemId }]);
  };

  const handleDropOn = async (targetItemId: string) => {
    if (!draggingItemId || !workboard || draggingItemId === targetItemId) {
      setDraggingItemId(null);
      setDropTargetItemId(null);
      return;
    }
    const orderedIds = workboard.items.map((item) => item.id);
    const fromIndex = orderedIds.indexOf(draggingItemId);
    const toIndex = orderedIds.indexOf(targetItemId);
    if (fromIndex < 0 || toIndex < 0) {
      setDraggingItemId(null);
      setDropTargetItemId(null);
      return;
    }

    const [moved] = orderedIds.splice(fromIndex, 1);
    orderedIds.splice(toIndex, 0, moved);
    setDraggingItemId(null);
    setDropTargetItemId(null);
    await applyOps([{ op: "reorder_items", ordered_ids: orderedIds }]);
  };

  const hasItems = Boolean(workboard && workboard.items.length > 0);
  const modalTitle = editingItemId ? "编辑任务" : "新增任务";
  const canRenderPortal = typeof document !== "undefined";
  const modal =
    isModalOpen && canRenderPortal
      ? createPortal(
          <div className="workboard-modal">
            <button type="button" className="workboard-modal__backdrop" aria-label="关闭任务编辑" onClick={closeModal} />
            <div
              className="workboard-modal__dialog"
              role="dialog"
              aria-modal="true"
              aria-label={modalTitle}
              onClick={(event) => event.stopPropagation()}
            >
              <div className="workboard-modal__header">
                <div>
                  <span className="workboard-dock__eyebrow">任务编辑</span>
                  <h3>{modalTitle}</h3>
                </div>
                <button type="button" className="workboard-dock__toggle-btn" onClick={closeModal} title="关闭">
                  <Icons.Close />
                </button>
              </div>

              <div className="workboard-modal__body">
                <label className="workboard-modal__field">
                  <span>标题</span>
                  <input
                    className="workboard-dock__input"
                    placeholder="例如：梳理待办交互方案"
                    value={draft.title}
                    onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))}
                  />
                </label>

                <label className="workboard-modal__field">
                  <span>备注</span>
                  <textarea
                    className="workboard-dock__textarea"
                    placeholder="补充说明、背景或验收标准"
                    value={draft.notes}
                    onChange={(event) => setDraft((current) => ({ ...current, notes: event.target.value }))}
                  />
                </label>

                <div className="workboard-modal__grid">
                  <label className="workboard-modal__field">
                    <span>状态</span>
                    <select
                      className="workboard-dock__select"
                      value={draft.status}
                      onChange={(event) => setDraft((current) => ({ ...current, status: event.target.value as WorkItemStatus }))}
                    >
                      <option value="pending">待处理</option>
                      <option value="in_progress">进行中</option>
                      <option value="completed">已完成</option>
                      <option value="blocked">受阻</option>
                      <option value="cancelled">已取消</option>
                    </select>
                  </label>

                  <label className="workboard-modal__field">
                    <span>优先级</span>
                    <select
                      className="workboard-dock__select"
                      value={draft.priority}
                      onChange={(event) => setDraft((current) => ({ ...current, priority: event.target.value as DraftState["priority"] }))}
                    >
                      <option value="low">低优先级</option>
                      <option value="medium">中优先级</option>
                      <option value="high">高优先级</option>
                    </select>
                  </label>
                </div>

                {error ? <div className="workboard-dock__error">{error}</div> : null}
              </div>

              <div className="workboard-modal__footer">
                <button type="button" className="workboard-dock__secondary-btn" onClick={closeModal} disabled={busy}>
                  取消
                </button>
                <button type="button" className="workboard-dock__primary-btn" onClick={() => void handleSave()} disabled={busy}>
                  {editingItemId ? "保存修改" : "创建任务"}
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )
      : null;

  return (
    <>
      <section className={`workboard-dock ${visible ? "is-visible" : "is-hidden"} ${collapsed ? "is-collapsed" : "is-expanded"} ${hasItems ? "" : "is-empty"}`}>
        <div className="workboard-dock__header">
          <button type="button" className="workboard-dock__header-left" onClick={() => setCollapsed((value) => !value)}>
            <span className="workboard-dock__eyebrow">任务清单</span>
            {hasItems ? (
              <>
                <div className="workboard-dock__mini-meter">
                  <div className="workboard-dock__mini-meter-bar" style={{ width: `${metrics.percent}%` }} />
                </div>
                <span className="workboard-dock__count">{metrics.completed} / {metrics.total}</span>
              </>
            ) : (
              <span className="workboard-dock__empty-text">暂无追踪任务</span>
            )}
          </button>
          <div className="workboard-dock__header-right">
            {hasItems ? (
              <span className="workboard-dock__summary">
                {metrics.blocked > 0 ? `${metrics.blocked} 项受阻` : metrics.active > 0 ? `${metrics.active} 项进行中` : allDone ? "已完成" : ""}
              </span>
            ) : null}
            <button type="button" className="workboard-dock__action-btn" onClick={openCreateModal} title="新增任务">
              <Icons.Plus />
            </button>
            <button type="button" className="workboard-dock__toggle-btn" onClick={onToggle} title="隐藏面板">
              <Icons.Close />
            </button>
          </div>
        </div>

        <div className="workboard-dock__body">
          {error ? <div className="workboard-dock__error">{error}</div> : null}
          {hasItems ? (
            <div className="workboard-dock__items">
              {workboard!.items.map((item) => (
                <article
                  key={item.id}
                  className={`workboard-item workboard-item--${item.status} ${dropTargetItemId === item.id ? "is-drop-target" : ""}`}
                  draggable={!busy}
                  onDragStart={() => {
                    setDraggingItemId(item.id);
                    setDropTargetItemId(item.id);
                  }}
                  onDragEnter={(event) => {
                    event.preventDefault();
                    if (draggingItemId && draggingItemId !== item.id) {
                      setDropTargetItemId(item.id);
                    }
                  }}
                  onDragOver={(event) => {
                    event.preventDefault();
                    if (draggingItemId && draggingItemId !== item.id) {
                      setDropTargetItemId(item.id);
                    }
                  }}
                  onDragLeave={(event) => {
                    if (!event.currentTarget.contains(event.relatedTarget as Node | null) && dropTargetItemId === item.id) {
                      setDropTargetItemId(null);
                    }
                  }}
                  onDragEnd={() => {
                    setDraggingItemId(null);
                    setDropTargetItemId(null);
                  }}
                  onDrop={(event) => {
                    event.preventDefault();
                    void handleDropOn(item.id);
                  }}
                >
                  <div className="workboard-item__drag-handle" title="拖动排序">
                    <Icons.Grip />
                  </div>

                  <div className="workboard-item__main">
                    <div className="workboard-item__title-row">
                      <strong>{item.active_form || item.title}</strong>
                      <div className="workboard-item__badges">
                        <span className={`workboard-item__priority workboard-item__priority--${item.priority}`}>
                          {item.priority === "high" ? "高" : item.priority === "low" ? "低" : "中"}
                        </span>
                        <span className={`workboard-item__status workboard-item__status--${item.status}`}>{statusLabel(item.status)}</span>
                      </div>
                    </div>
                    <div className="workboard-item__subrow">
                      {item.notes ? <p className="workboard-item__notes">{item.notes}</p> : <p className="workboard-item__notes is-muted">暂无备注</p>}
                      {item.owner && item.owner !== "assistant" ? <span className="workboard-item__owner">{item.owner}</span> : null}
                    </div>
                  </div>

                  <div className="workboard-item__actions">
                    <button
                      type="button"
                      className="workboard-item__icon-btn"
                      onClick={(event) => {
                        event.stopPropagation();
                        openEditModal(item);
                      }}
                      disabled={busy}
                      title="编辑"
                    >
                      <Icons.Pencil />
                    </button>
                    <button
                      type="button"
                      className="workboard-item__icon-btn is-danger"
                      onClick={(event) => {
                        event.stopPropagation();
                        void handleDelete(item.id);
                      }}
                      disabled={busy}
                      title="删除"
                    >
                      <Icons.Trash />
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <div className="workboard-dock__empty-state">
              <p>把 AI 拆出来的任务放在这里，也可以手动维护。</p>
              <button type="button" className="workboard-dock__primary-btn" onClick={openCreateModal}>
                新建第一条任务
              </button>
            </div>
          )}
        </div>
      </section>
      {modal}
    </>
  );
}
