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

type ScheduleForm = {
  title: string;
  prompt: string;
  targetMode: "existing_session" | "new_session_per_run";
  targetSessionId: string;
  newSessionTitlePrefix: string;
  workspacePolicy: "isolated" | "shared_with_parent";
  frequency: "interval" | "daily" | "workday" | "weekly" | "cron";
  time: string;
  weekdays: number[];
  intervalMinutes: number;
  cron: string;
  timezone: string;
  timeoutSeconds: number;
  maxRuns: string;
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
  cron: "*/15 * * * *",
  timezone: "Asia/Shanghai",
  timeoutSeconds: 900,
  maxRuns: "",
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
  queued: "排队中",
  running: "执行中",
  succeeded: "成功",
  failed: "失败",
  skipped: "跳过",
  timed_out: "超时",
  cancelled: "取消",
};

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
    const names = ["一", "二", "三", "四", "五", "六", "日"];
    return `每周${schedule.weekdays.map((day) => names[day - 1]).join("/")} ${schedule.time} · ${timezone}`;
  }
  const label = schedule.type === "workday" ? "工作日" : "每天";
  return `${label} ${schedule.time} · ${timezone}`;
}

function taskToForm(task: ScheduleTask): ScheduleForm {
  return {
    ...emptyForm,
    title: task.title,
    prompt: task.prompt,
    targetMode: task.target_mode,
    targetSessionId: task.target_session_id ?? "",
    newSessionTitlePrefix: task.new_session_title_prefix ?? "",
    workspacePolicy: task.workspace_policy,
    frequency: task.schedule.type,
    time:
      task.schedule.type === "daily" || task.schedule.type === "workday" || task.schedule.type === "weekly"
        ? task.schedule.time
        : "09:00",
    weekdays: task.schedule.type === "weekly" ? task.schedule.weekdays : [1],
    intervalMinutes:
      task.schedule.type === "interval" ? Math.round(task.schedule.every_seconds / 60) : 15,
    cron: task.schedule.type === "cron" ? task.schedule.expression : "*/15 * * * *",
    timezone: task.timezone,
    timeoutSeconds: task.execution.timeout_seconds,
    maxRuns: task.max_runs ? String(task.max_runs) : "",
  };
}

export function SchedulePanel({ conversations, sessionId, onSessionSelect }: SchedulePanelProps) {
  const [tasks, setTasks] = useState<ScheduleTask[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<ScheduleTask | null>(null);
  const [form, setForm] = useState<ScheduleForm>({ ...emptyForm, targetSessionId: sessionId });
  const [saving, setSaving] = useState(false);
  const [runsTaskId, setRunsTaskId] = useState("");
  const [runs, setRuns] = useState<ScheduleRun[]>([]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listSchedules(statusFilter || undefined);
      setTasks(result.items);
      setTotal(result.total);
      setError("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "获取定时任务失败");
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (editing || formOpen) return;
    setForm((current) => ({ ...current, targetSessionId: sessionId }));
  }, [editing, formOpen, sessionId]);

  useEffect(() => {
    if (!runsTaskId) return;
    let cancelled = false;
    listScheduleRuns(runsTaskId)
      .then((result) => {
        if (!cancelled) setRuns(result);
      })
      .catch((exc) => {
        if (!cancelled) setError(exc instanceof Error ? exc.message : "获取执行历史失败");
      });
    return () => {
      cancelled = true;
    };
  }, [runsTaskId]);

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
    setFormOpen(true);
  };

  const openEdit = (task: ScheduleTask) => {
    setEditing(task);
    setForm(taskToForm(task));
    setFormOpen(true);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      const execution = {
        timeout_seconds: form.timeoutSeconds,
        concurrency_policy: "skip" as const,
        missed_run_policy: "skip" as const,
        max_runs: form.maxRuns ? Number(form.maxRuns) : null,
      };
      const target = {
        mode: form.targetMode,
        session_id: form.targetMode === "existing_session" ? form.targetSessionId : null,
        new_session_title_prefix: form.targetMode === "new_session_per_run" ? form.newSessionTitlePrefix : null,
        workspace_policy: form.workspacePolicy,
      };
      if (editing) {
        await updateSchedule(editing.task_id, {
          expected_revision: editing.revision,
          title: form.title,
          prompt: form.prompt,
          target,
          schedule: scheduleFromForm,
          timezone: form.timezone,
          execution,
        });
      } else {
        await createSchedule({
          title: form.title,
          prompt: form.prompt,
          target,
          schedule: scheduleFromForm,
          timezone: form.timezone,
          execution,
        });
      }
      setFormOpen(false);
      setEditing(null);
      await refresh();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "保存定时任务失败");
    } finally {
      setSaving(false);
    }
  };

  const runAction = async (task: ScheduleTask, action: "pause" | "resume" | "approve" | "reject" | "run") => {
    try {
      await scheduleAction(task.task_id, action);
      await refresh();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "操作定时任务失败");
    }
  };

  const archive = async (task: ScheduleTask) => {
    try {
      await deleteSchedule(task.task_id);
      await refresh();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "归档定时任务失败");
    }
  };

  return (
    <section className="schedule-panel">
      <div className="schedule-panel__toolbar">
        <select
          className="schedule-panel__select"
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value)}
          aria-label="筛选定时任务状态"
        >
          <option value="">全部状态</option>
          <option value="pending_approval">待确认</option>
          <option value="active">运行中</option>
          <option value="paused">已暂停</option>
          <option value="completed">已完成</option>
          <option value="archived">已归档</option>
        </select>
        <button type="button" className="action-button small" onClick={openCreate}>
          新建
        </button>
      </div>

      {error ? <div className="schedule-panel__error">{error}</div> : null}
      {loading ? <div className="empty-state">正在加载定时任务…</div> : null}
      {!loading && tasks.length === 0 ? <div className="empty-state">暂无定时任务</div> : null}

      <div className="schedule-panel__list">
        {tasks.map((task) => (
          <article key={task.task_id} className="schedule-card">
            <header className="schedule-card__header">
              <div>
                <strong>{task.title}</strong>
                <p>{describeSchedule(task.schedule, task.timezone)}</p>
              </div>
              <span className={`schedule-status schedule-status--${task.status}`}>
                {statusLabels[task.status]}
              </span>
            </header>
            <div className="schedule-card__meta">
              <span>下次 {formatTime(task.next_run_at)}</span>
              <span>已执行 {task.run_count}{task.max_runs ? ` / ${task.max_runs}` : ""}</span>
              <span>{task.target_mode === "existing_session" ? "固定会话" : "每次新会话"}</span>
            </div>
            {task.last_failure_reason ? (
              <p className="schedule-card__failure">{task.last_failure_reason}</p>
            ) : null}
            <div className="schedule-card__actions">
              {task.status === "pending_approval" ? (
                <>
                  <button type="button" className="download-btn" onClick={() => runAction(task, "approve")}>确认</button>
                  <button type="button" className="download-btn" onClick={() => runAction(task, "reject")}>拒绝</button>
                </>
              ) : null}
              {task.status === "active" ? (
                <button type="button" className="download-btn" onClick={() => runAction(task, "pause")}>暂停</button>
              ) : null}
              {task.status === "paused" ? (
                <button type="button" className="download-btn" onClick={() => runAction(task, "resume")}>恢复</button>
              ) : null}
              <button type="button" className="download-btn" onClick={() => runAction(task, "run")}>立即执行</button>
              <button type="button" className="download-btn" onClick={() => openEdit(task)}>编辑</button>
              <button
                type="button"
                className="download-btn"
                onClick={() => setRunsTaskId(runsTaskId === task.task_id ? "" : task.task_id)}
              >
                历史
              </button>
              <button type="button" className="download-btn" onClick={() => archive(task)}>归档</button>
            </div>

            {runsTaskId === task.task_id ? (
              <div className="schedule-runs">
                {runs.length === 0 ? <div className="empty-state">暂无执行历史</div> : null}
                {runs.map((run) => (
                  <button
                    key={run.run_id}
                    type="button"
                    className="schedule-run"
                    onClick={() => run.session_id && onSessionSelect?.(run.session_id)}
                  >
                    <span>{formatTime(run.scheduled_for)}</span>
                    <span className={`schedule-status schedule-status--${run.status}`}>
                      {runStatusLabels[run.status]}
                    </span>
                    <span>{run.duration_ms ? `${Math.round(run.duration_ms / 1000)}s` : "-"}</span>
                    <span>{run.result_summary || run.error || "-"}</span>
                  </button>
                ))}
              </div>
            ) : null}
          </article>
        ))}
      </div>
      <p className="schedule-panel__total">共 {total} 个任务</p>

      {formOpen ? (
        <form className="schedule-form" onSubmit={submit}>
          <h3>{editing ? "编辑定时任务" : "新建定时任务"}</h3>
          <label>
            标题
            <input
              value={form.title}
              onChange={(event) => setForm({ ...form, title: event.target.value })}
              required
              maxLength={128}
            />
          </label>
          <label>
            执行提示词
            <textarea
              value={form.prompt}
              onChange={(event) => setForm({ ...form, prompt: event.target.value })}
              required
              rows={5}
            />
          </label>
          <label>
            目标
            <select
              value={form.targetMode}
              onChange={(event) =>
                setForm({ ...form, targetMode: event.target.value as ScheduleForm["targetMode"] })
              }
            >
              <option value="existing_session">固定会话</option>
              <option value="new_session_per_run">每次新会话</option>
            </select>
          </label>
          {form.targetMode === "existing_session" ? (
            <label>
              会话
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
            <>
              <label>
                新会话标题前缀
                <input
                  value={form.newSessionTitlePrefix}
                  onChange={(event) => setForm({ ...form, newSessionTitlePrefix: event.target.value })}
                  maxLength={80}
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
                  <option value="shared_with_parent">共享父会话 Workspace</option>
                </select>
              </label>
            </>
          )}
          <div className="schedule-form__grid">
            <label>
              频率
              <select
                value={form.frequency}
                onChange={(event) =>
                  setForm({ ...form, frequency: event.target.value as ScheduleForm["frequency"] })
                }
              >
                <option value="interval">间隔</option>
                <option value="daily">每天</option>
                <option value="workday">工作日</option>
                <option value="weekly">每周</option>
                <option value="cron">自定义 Cron</option>
              </select>
            </label>
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
          </div>
          {form.frequency === "weekly" ? (
            <div className="schedule-form__weekdays">
              {[1, 2, 3, 4, 5, 6, 7].map((day) => (
                <label key={day}>
                  <input
                    type="checkbox"
                    checked={form.weekdays.includes(day)}
                    onChange={(event) => {
                      const next = event.target.checked
                        ? [...form.weekdays, day]
                        : form.weekdays.filter((item) => item !== day);
                      setForm({ ...form, weekdays: next.sort((left, right) => left - right) });
                    }}
                  />
                  {["一", "二", "三", "四", "五", "六", "日"][day - 1]}
                </label>
              ))}
            </div>
          ) : null}
          <div className="schedule-form__actions">
            <button type="submit" className="action-button small" disabled={saving}>
              {saving ? "保存中…" : "保存"}
            </button>
            <button
              type="button"
              className="download-btn"
              onClick={() => {
                setFormOpen(false);
                setEditing(null);
              }}
            >
              取消
            </button>
          </div>
        </form>
      ) : null}
    </section>
  );
}
