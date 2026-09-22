import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import {
  createSchedule,
  deleteSchedule,
  listScheduleRuns,
  listSchedules,
  scheduleAction,
  updateSchedule,
  type ScheduleRun,
  type ScheduleSpec,
  type ScheduleTask,
} from "../../api/client";
import type { WorkbenchConversation } from "../../pages/workbench/types";

type SchedulePanelProps = {
  conversations: WorkbenchConversation[];
  sessionId: string;
  onSessionSelect?: (sessionId: string) => void;
};

type Frequency = "interval" | "daily" | "workday" | "weekly" | "cron";
type StatusFilter = "all" | "pending" | "active" | "paused" | "ended";
type TerminalStatus = "completed" | "expired" | "archived";

type ScheduleForm = {
  title: string;
  prompt: string;
  targetMode: "existing_session" | "new_session_per_run";
  targetSessionId: string;
  newSessionTitlePrefix: string;
  workspacePolicy: "isolated" | "shared_with_parent";
  frequency: Frequency;
  time: string;
  weekdays: number[];
  intervalMinutes: number;
  cron: string;
  timezone: string;
  startsAt: string;
  endsAt: string;
  timeoutSeconds: number;
  maxRuns: string;
  concurrencyPolicy: "skip" | "queue";
  missedRunPolicy: "skip" | "run_once" | "catch_up";
};

const emptyForm: ScheduleForm = {
  title: "",
  prompt: "",
  targetMode: "existing_session",
  targetSessionId: "",
  newSessionTitlePrefix: "",
  workspacePolicy: "isolated",
  frequency: "daily",
  time: "09:00",
  weekdays: [1],
  intervalMinutes: 15,
  cron: "*/15 * * * 1-5",
  timezone: "Asia/Shanghai",
  startsAt: "",
  endsAt: "",
  timeoutSeconds: 900,
  maxRuns: "",
  concurrencyPolicy: "skip",
  missedRunPolicy: "skip",
};

const statusLabels: Record<ScheduleTask["status"], string> = {
  pending_approval: "待确认",
  active: "运行中",
  paused: "已暂停",
  completed: "已完成",
  expired: "已过期",
  archived: "已归档",
};

const runStatusLabels: Record<ScheduleRun["status"], string> = {
  queued: "排队",
  running: "执行中",
  succeeded: "成功",
  failed: "失败",
  skipped: "跳过",
  timed_out: "超时",
  cancelled: "取消",
};

const filters: Array<{ value: StatusFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "pending", label: "待确认" },
  { value: "active", label: "运行中" },
  { value: "paused", label: "暂停" },
  { value: "ended", label: "已结束" },
];

const weekdayNames = ["一", "二", "三", "四", "五", "六", "日"];

function formatTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function describeSchedule(schedule: ScheduleSpec, timezone: string) {
  if (schedule.type === "interval") {
    return `每 ${Math.round(schedule.every_seconds / 60)} 分钟`;
  }
  if (schedule.type === "cron") {
    return `Cron ${schedule.expression} · ${timezone}`;
  }
  if (schedule.type === "weekly") {
    return `每周${schedule.weekdays.map((day) => weekdayNames[day - 1]).join("/")} ${schedule.time} · ${timezone}`;
  }
  const label = schedule.type === "workday" ? "工作日" : "每天";
  return `${label} ${schedule.time} · ${timezone}`;
}

function isoToLocalInput(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16);
}

function localInputToIso(value: string) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function taskToForm(task: ScheduleTask): ScheduleForm {
  const timedSchedule =
    task.schedule.type === "daily" || task.schedule.type === "workday" || task.schedule.type === "weekly";
  return {
    ...emptyForm,
    title: task.title,
    prompt: task.prompt,
    targetMode: task.target_mode,
    targetSessionId: task.target_session_id ?? "",
    newSessionTitlePrefix: task.new_session_title_prefix ?? "",
    workspacePolicy: task.workspace_policy,
    frequency: task.schedule.type,
    time: timedSchedule ? task.schedule.time : "09:00",
    weekdays: task.schedule.type === "weekly" ? task.schedule.weekdays : [1],
    intervalMinutes:
      task.schedule.type === "interval" ? Math.round(task.schedule.every_seconds / 60) : 15,
    cron: task.schedule.type === "cron" ? task.schedule.expression : "*/15 * * * 1-5",
    timezone: task.timezone,
    startsAt: isoToLocalInput(task.starts_at),
    endsAt: isoToLocalInput(task.ends_at),
    timeoutSeconds: task.execution.timeout_seconds,
    maxRuns: task.max_runs ? String(task.max_runs) : "",
    concurrencyPolicy: task.execution.concurrency_policy,
    missedRunPolicy: task.execution.missed_run_policy,
  };
}

export function SchedulePanel({ conversations, sessionId, onSessionSelect }: SchedulePanelProps) {
  const [tasks, setTasks] = useState<ScheduleTask[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<ScheduleTask | null>(null);
  const [form, setForm] = useState<ScheduleForm>({ ...emptyForm, targetSessionId: sessionId });
  const [saving, setSaving] = useState(false);
  const [expandedTaskId, setExpandedTaskId] = useState("");
  const [runs, setRuns] = useState<ScheduleRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [actionTaskId, setActionTaskId] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listSchedules();
      setTasks(result.items);
      setTotal(result.total);
      setError("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "获取定时任务失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshRuns = useCallback(async (taskId: string) => {
    setRunsLoading(true);
    try {
      const result = await listScheduleRuns(taskId);
      setRuns(result);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "获取执行历史失败");
    } finally {
      setRunsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (editorOpen) return;
    setForm((current) => ({ ...current, targetSessionId: current.targetSessionId || sessionId }));
  }, [editorOpen, sessionId]);

  useEffect(() => {
    if (!expandedTaskId) return;
    void refreshRuns(expandedTaskId);
  }, [expandedTaskId, refreshRuns]);

  const visibleTasks = useMemo(() => {
    if (statusFilter === "all") return tasks;
    if (statusFilter === "pending") return tasks.filter((task) => task.status === "pending_approval");
    if (statusFilter === "active") return tasks.filter((task) => task.status === "active");
    if (statusFilter === "paused") return tasks.filter((task) => task.status === "paused");
    return tasks.filter((task) => ["completed", "expired", "archived"].includes(task.status));
  }, [statusFilter, tasks]);

  const scheduleFromForm = useMemo<ScheduleSpec>(() => {
    if (form.frequency === "interval") {
      return { type: "interval", every_seconds: Math.max(1, form.intervalMinutes) * 60 };
    }
    if (form.frequency === "cron") {
      return { type: "cron", expression: form.cron.trim() };
    }
    if (form.frequency === "weekly") {
      return { type: "weekly", weekdays: form.weekdays, time: form.time };
    }
    if (form.frequency === "workday") {
      return { type: "workday", time: form.time };
    }
    return { type: "daily", time: form.time };
  }, [form.cron, form.frequency, form.intervalMinutes, form.time, form.weekdays]);

  const openCreate = () => {
    setEditing(null);
    setForm({ ...emptyForm, targetSessionId: sessionId });
    setEditorOpen(true);
  };

  const openEdit = (task: ScheduleTask) => {
    setEditing(task);
    setForm(taskToForm(task));
    setEditorOpen(true);
  };

  const closeEditor = () => {
    setEditorOpen(false);
    setEditing(null);
  };

  useEffect(() => {
    if (!editorOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) closeEditor();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [editorOpen, saving]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      const execution = {
        timeout_seconds: form.timeoutSeconds,
        concurrency_policy: form.concurrencyPolicy,
        missed_run_policy: form.missedRunPolicy,
        max_runs: form.maxRuns ? Number(form.maxRuns) : null,
      };
      const target = {
        mode: form.targetMode,
        session_id: form.targetMode === "existing_session" ? form.targetSessionId : null,
        new_session_title_prefix: form.targetMode === "new_session_per_run" ? form.newSessionTitlePrefix : null,
        workspace_policy: form.workspacePolicy,
      };
      const payload = {
        title: form.title,
        prompt: form.prompt,
        target,
        schedule: scheduleFromForm,
        timezone: form.timezone,
        starts_at: localInputToIso(form.startsAt),
        ends_at: localInputToIso(form.endsAt),
        execution,
      };
      if (editing) {
        await updateSchedule(editing.task_id, {
          ...payload,
          expected_revision: editing.revision,
        });
      } else {
        await createSchedule(payload);
      }
      closeEditor();
      await refresh();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "保存定时任务失败");
    } finally {
      setSaving(false);
    }
  };

  const runAction = async (
    task: ScheduleTask,
    action: "pause" | "resume" | "approve" | "reject" | "run" | "restore",
  ) => {
    setActionTaskId(task.task_id);
    try {
      await scheduleAction(task.task_id, action);
      await refresh();
      if (expandedTaskId === task.task_id && action === "run") {
        await refreshRuns(task.task_id);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "操作定时任务失败");
    } finally {
      setActionTaskId("");
    }
  };

  const archive = async (task: ScheduleTask) => {
    setActionTaskId(task.task_id);
    try {
      await deleteSchedule(task.task_id);
      await refresh();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "归档定时任务失败");
    } finally {
      setActionTaskId("");
    }
  };

  const toggleHistory = (taskId: string) => {
    const nextTaskId = expandedTaskId === taskId ? "" : taskId;
    setExpandedTaskId(nextTaskId);
    setRuns([]);
  };

  return (
    <section className="schedule-panel">
      <header className="schedule-panel__header">
        <div>
          <h3>定时任务</h3>
          <p>{total} 个任务 · 支持固定会话与每次新会话</p>
        </div>
        <button type="button" className="schedule-primary-btn" onClick={openCreate}>
          <span aria-hidden>+</span> 新建
        </button>
      </header>

      <div className="schedule-filter" role="tablist" aria-label="任务状态筛选">
        {filters.map((filter) => (
          <button
            key={filter.value}
            type="button"
            className={`schedule-filter__chip${statusFilter === filter.value ? " is-active" : ""}`}
            role="tab"
            aria-selected={statusFilter === filter.value}
            onClick={() => setStatusFilter(filter.value)}
          >
            {filter.label}
          </button>
        ))}
      </div>

      {error ? (
        <div className="schedule-alert" role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => setError("")} aria-label="关闭错误">×</button>
        </div>
      ) : null}

      <div className="schedule-list">
        {loading ? <div className="schedule-empty">正在加载定时任务…</div> : null}
        {!loading && visibleTasks.length === 0 ? (
          <div className="schedule-empty">
            <strong>暂无任务</strong>
            <span>创建一个定时任务，让 Agent 按计划自动执行。</span>
          </div>
        ) : null}

        {visibleTasks.map((task) => {
          const terminal = ["completed", "expired", "archived"].includes(task.status);
          return (
            <article key={task.task_id} className={`schedule-card schedule-card--${task.status}`}>
              <div className="schedule-card__top">
                <div className="schedule-card__icon" aria-hidden>
                  {terminal ? "⏹" : task.status === "paused" ? "⏸" : "⏱"}
                </div>
                <div className="schedule-card__title">
                  <strong>{task.title}</strong>
                  <span>{describeSchedule(task.schedule, task.timezone)}</span>
                </div>
                <span className={`schedule-pill schedule-pill--${task.status}`}>
                  {statusLabels[task.status]}
                </span>
              </div>

              <dl className="schedule-card__metrics">
                <div>
                  <dt>下次执行</dt>
                  <dd>{formatTime(task.next_run_at)}</dd>
                </div>
                <div>
                  <dt>执行次数</dt>
                  <dd>{task.run_count}{task.max_runs ? `/${task.max_runs}` : ""}</dd>
                </div>
                <div>
                  <dt>目标</dt>
                  <dd>{task.target_mode === "existing_session" ? "固定会话" : "新会话"}</dd>
                </div>
              </dl>

              {task.next_runs.length > 0 && task.status === "active" ? (
                <div className="schedule-preview">
                  {task.next_runs.map((value) => <span key={value}>{formatTime(value)}</span>)}
                </div>
              ) : null}

              {task.last_failure_reason ? (
                <p className="schedule-card__failure">{task.last_failure_reason}</p>
              ) : null}

              <div className="schedule-card__actions">
                {task.status === "pending_approval" ? (
                  <>
                    <button
                      type="button"
                      className="schedule-primary-btn small"
                      disabled={actionTaskId === task.task_id}
                      onClick={() => runAction(task, "approve")}
                    >
                      确认
                    </button>
                    <button
                      type="button"
                      className="schedule-ghost-btn"
                      disabled={actionTaskId === task.task_id}
                      onClick={() => runAction(task, "reject")}
                    >
                      拒绝
                    </button>
                  </>
                ) : null}
                {task.status === "active" ? (
                  <button
                    type="button"
                    className="schedule-ghost-btn"
                    disabled={actionTaskId === task.task_id}
                    onClick={() => runAction(task, "pause")}
                  >
                    暂停
                  </button>
                ) : null}
                {task.status === "paused" ? (
                  <button
                    type="button"
                    className="schedule-primary-btn small"
                    disabled={actionTaskId === task.task_id}
                    onClick={() => runAction(task, "resume")}
                  >
                    恢复
                  </button>
                ) : null}
                {task.status === "pending_approval" ? (
                  <button
                    type="button"
                    className="schedule-ghost-btn"
                    disabled={actionTaskId === task.task_id}
                    onClick={() => openEdit(task)}
                  >
                    编辑
                  </button>
                ) : null}
                {terminal ? (
                  <>
                    <button
                      type="button"
                      className="schedule-primary-btn small"
                      disabled={actionTaskId === task.task_id}
                      onClick={() => runAction(task, "restore")}
                    >
                      恢复任务
                    </button>
                    <button type="button" className="schedule-ghost-btn" onClick={() => openEdit(task)}>调整配置</button>
                  </>
                ) : task.status === "pending_approval" ? null : (
                  <>
                    <button
                      type="button"
                      className="schedule-ghost-btn"
                      disabled={actionTaskId === task.task_id}
                      onClick={() => runAction(task, "run")}
                    >
                      立即执行
                    </button>
                    <button type="button" className="schedule-ghost-btn" onClick={() => openEdit(task)}>编辑</button>
                    <button
                      type="button"
                      className="schedule-ghost-btn danger"
                      disabled={actionTaskId === task.task_id}
                      onClick={() => archive(task)}
                    >
                      归档
                    </button>
                  </>
                )}
                <button
                  type="button"
                  className={`schedule-icon-btn${expandedTaskId === task.task_id ? " is-active" : ""}`}
                  onClick={() => toggleHistory(task.task_id)}
                  aria-expanded={expandedTaskId === task.task_id}
                >
                  历史
                </button>
              </div>

              {expandedTaskId === task.task_id ? (
                <div className="schedule-history">
                  <header>
                    <span>执行历史</span>
                    <button type="button" onClick={() => void refreshRuns(task.task_id)}>刷新</button>
                  </header>
                  {runsLoading ? <div className="schedule-history__empty">正在加载…</div> : null}
                  {!runsLoading && runs.length === 0 ? (
                    <div className="schedule-history__empty">还没有执行记录</div>
                  ) : null}
                  {runs.map((run) => (
                    <div key={run.run_id} className="schedule-history__row">
                      <span className={`schedule-dot schedule-dot--${run.status}`} aria-hidden />
                      <div>
                        <strong>{formatTime(run.scheduled_for)}</strong>
                        <span>{run.result_summary || run.error || runStatusLabels[run.status]}</span>
                      </div>
                      <em>{run.duration_ms ? `${Math.round(run.duration_ms / 1000)}s` : runStatusLabels[run.status]}</em>
                      {run.session_id ? (
                        <button type="button" onClick={() => onSessionSelect?.(run.session_id || "")}>查看</button>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </article>
          );
        })}
      </div>

      {editorOpen ? (
        <div className="schedule-editor-overlay" role="dialog" aria-modal="true" aria-labelledby="schedule-editor-title">
          <form className="schedule-editor" onSubmit={submit}>
            <header className="schedule-editor__header">
              <div>
                <h3 id="schedule-editor-title">{editing ? "编辑定时任务" : "新建定时任务"}</h3>
                <p>任务会持久化保存，即使服务重启也不会丢失。</p>
              </div>
              <button type="button" className="schedule-icon-btn" onClick={closeEditor} aria-label="关闭">×</button>
            </header>

            <div className="schedule-editor__body">
              <section className="schedule-editor__section">
                <h4>基本信息</h4>
                <label>
                  标题
                  <input
                    value={form.title}
                    onChange={(event) => setForm({ ...form, title: event.target.value })}
                    required
                    maxLength={128}
                    placeholder="例如：每周项目进展总结"
                  />
                </label>
                <label>
                  执行提示词
                  <textarea
                    value={form.prompt}
                    onChange={(event) => setForm({ ...form, prompt: event.target.value })}
                    required
                    rows={5}
                    placeholder="描述希望 Agent 在每次触发时做什么"
                  />
                </label>
              </section>

              <section className="schedule-editor__section">
                <h4>运行位置</h4>
                <div className="schedule-editor__choice">
                  <label className={form.targetMode === "existing_session" ? "is-active" : ""}>
                    <input
                      type="radio"
                      name="schedule-target"
                      checked={form.targetMode === "existing_session"}
                      onChange={() => setForm({ ...form, targetMode: "existing_session" })}
                    />
                    <strong>固定会话</strong>
                    <span>延续上下文，适合长期跟踪同一任务</span>
                  </label>
                  <label className={form.targetMode === "new_session_per_run" ? "is-active" : ""}>
                    <input
                      type="radio"
                      name="schedule-target"
                      checked={form.targetMode === "new_session_per_run"}
                      onChange={() => setForm({ ...form, targetMode: "new_session_per_run" })}
                    />
                    <strong>每次新会话</strong>
                    <span>上下文隔离，适合独立报告和检查</span>
                  </label>
                </div>

                {form.targetMode === "existing_session" ? (
                  <label>
                    选择会话
                    <select
                      value={form.targetSessionId}
                      onChange={(event) => setForm({ ...form, targetSessionId: event.target.value })}
                      required
                    >
                      <option value="">请选择会话</option>
                      {conversations.map((conversation) => (
                        <option key={conversation.session_id} value={conversation.session_id}>
                          {conversation.title}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : (
                  <div className="schedule-editor__grid">
                    <label>
                      新会话标题前缀
                      <input
                        value={form.newSessionTitlePrefix}
                        onChange={(event) => setForm({ ...form, newSessionTitlePrefix: event.target.value })}
                        maxLength={80}
                        placeholder="默认使用任务标题"
                      />
                    </label>
                    <label>
                      Workspace
                      <select
                        value={form.workspacePolicy}
                        onChange={(event) =>
                          setForm({ ...form, workspacePolicy: event.target.value as ScheduleForm["workspacePolicy"] })
                        }
                      >
                        <option value="isolated">独立 Workspace</option>
                        <option value="shared_with_parent">共享父会话</option>
                      </select>
                    </label>
                  </div>
                )}
              </section>

              <section className="schedule-editor__section">
                <h4>频率</h4>
                <div className="schedule-frequency">
                  {([
                    ["interval", "间隔"],
                    ["daily", "每天"],
                    ["workday", "工作日"],
                    ["weekly", "每周"],
                    ["cron", "Cron"],
                  ] as Array<[Frequency, string]>).map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      className={form.frequency === value ? "is-active" : ""}
                      onClick={() => setForm({ ...form, frequency: value })}
                    >
                      {label}
                    </button>
                  ))}
                </div>

                <div className="schedule-editor__grid">
                  {form.frequency === "interval" ? (
                    <label>
                      间隔（分钟）
                      <input
                        type="number"
                        min={1}
                        value={form.intervalMinutes}
                        onChange={(event) => setForm({ ...form, intervalMinutes: Number(event.target.value) })}
                        required
                      />
                    </label>
                  ) : null}
                  {form.frequency === "cron" ? (
                    <label>
                      Cron 表达式
                      <input
                        value={form.cron}
                        onChange={(event) => setForm({ ...form, cron: event.target.value })}
                        placeholder="*/15 * * * 1-5"
                        required
                      />
                    </label>
                  ) : null}
                  {form.frequency !== "interval" && form.frequency !== "cron" ? (
                    <label>
                      时间
                      <input
                        type="time"
                        value={form.time}
                        onChange={(event) => setForm({ ...form, time: event.target.value })}
                        required
                      />
                    </label>
                  ) : null}
                  <label>
                    时区
                    <input
                      value={form.timezone}
                      onChange={(event) => setForm({ ...form, timezone: event.target.value })}
                      required
                    />
                  </label>
                </div>

                {form.frequency === "weekly" ? (
                  <div className="schedule-weekdays">
                    {weekdayNames.map((label, index) => {
                      const day = index + 1;
                      return (
                        <button
                          key={label}
                          type="button"
                          className={form.weekdays.includes(day) ? "is-active" : ""}
                          onClick={() => {
                            const next = form.weekdays.includes(day)
                              ? form.weekdays.filter((item) => item !== day)
                              : [...form.weekdays, day];
                            setForm({ ...form, weekdays: next.sort((left, right) => left - right) });
                          }}
                        >
                          {label}
                        </button>
                      );
                    })}
                  </div>
                ) : null}
              </section>

              <section className="schedule-editor__section">
                <h4>执行策略</h4>
                <div className="schedule-editor__grid">
                  <label>
                    开始时间
                    <input
                      type="datetime-local"
                      value={form.startsAt}
                      onChange={(event) => setForm({ ...form, startsAt: event.target.value })}
                    />
                  </label>
                  <label>
                    结束时间
                    <input
                      type="datetime-local"
                      value={form.endsAt}
                      onChange={(event) => setForm({ ...form, endsAt: event.target.value })}
                    />
                  </label>
                  <label>
                    超时（秒）
                    <input
                      type="number"
                      min={30}
                      max={7200}
                      value={form.timeoutSeconds}
                      onChange={(event) => setForm({ ...form, timeoutSeconds: Number(event.target.value) })}
                      required
                    />
                  </label>
                  <label>
                    最大次数
                    <input
                      type="number"
                      min={1}
                      value={form.maxRuns}
                      onChange={(event) => setForm({ ...form, maxRuns: event.target.value })}
                      placeholder="不限制"
                    />
                  </label>
                  <label>
                    会话忙碌时
                    <select
                      value={form.concurrencyPolicy}
                      onChange={(event) =>
                        setForm({ ...form, concurrencyPolicy: event.target.value as ScheduleForm["concurrencyPolicy"] })
                      }
                    >
                      <option value="skip">跳过本次</option>
                      <option value="queue">等待执行</option>
                    </select>
                  </label>
                  <label>
                    错过触发时
                    <select
                      value={form.missedRunPolicy}
                      onChange={(event) =>
                        setForm({ ...form, missedRunPolicy: event.target.value as ScheduleForm["missedRunPolicy"] })
                      }
                    >
                      <option value="skip">跳到下一次</option>
                      <option value="run_once">补跑一次</option>
                      <option value="catch_up">全部补跑</option>
                    </select>
                  </label>
                </div>
              </section>

            </div>

            <footer className="schedule-editor__footer">
              <span>{editing ? `修订版本 ${editing.revision}` : "创建后可随时暂停或归档"}</span>
              <div>
                <button type="button" className="schedule-ghost-btn" onClick={closeEditor}>取消</button>
                <button type="submit" className="schedule-primary-btn" disabled={saving}>
                  {saving ? "保存中…" : "保存任务"}
                </button>
              </div>
            </footer>
          </form>
        </div>
      ) : null}
    </section>
  );
}
