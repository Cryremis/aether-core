import type { SubagentRunSummary } from "../../api/client";

const STATUS_LABELS: Record<string, string> = {
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  timed_out: "已超时",
};

export function SubagentPanel({ subagents }: { subagents: SubagentRunSummary[] }) {
  if (subagents.length === 0) return null;

  return (
    <section className="subagent-panel" aria-label="子 Agent 状态">
      <header className="subagent-panel__header">
        <div>
          <span>Subagents</span>
          <strong>只读协作者</strong>
        </div>
        <span className="subagent-panel__count">{subagents.length}</span>
      </header>
      <div className="subagent-panel__list">
        {subagents.map((subagent) => (
          <article key={subagent.subagent_run_id} className="subagent-card">
            <header>
              <strong>{subagent.name}</strong>
              <span className={`subagent-card__status subagent-card__status--${subagent.status}`}>
                {STATUS_LABELS[subagent.status] ?? subagent.status}
              </span>
            </header>
            <p>{subagent.task}</p>
            {subagent.result ? <div className="subagent-card__result">{subagent.result}</div> : null}
            {subagent.error ? <div className="subagent-card__error">{subagent.error}</div> : null}
            <footer>
              <span>用户不能直接发消息</span>
              <span>{new Date(subagent.finished_at ?? subagent.created_at ?? Date.now()).toLocaleString()}</span>
            </footer>
          </article>
        ))}
      </div>
    </section>
  );
}
