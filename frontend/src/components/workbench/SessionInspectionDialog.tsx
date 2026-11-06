import { useEffect, useState } from "react";
import { inspectSession, type SessionContextStatus, type SessionToolCatalog } from "../../api/client";

const sources: Record<string, string> = {
  deployment: "部署配置",
  endpoint_metadata: "模型服务元数据",
  catalog_unverified: "模型目录（未验证）",
  catalog_or_fallback: "模型目录或默认值",
  unverified_fallback: "安全默认值（未验证）",
};
const toolSources = { built_in: "内置", host: "宿主", mcp: "MCP" };
const tokens = (count: number | null) => count == null ? "暂无" : count.toLocaleString();

export function SessionInspectionDialog({ sessionId, resource, onClose, onManageTools }: {
  sessionId: string;
  resource: "context/status" | "tools";
  onClose: () => void;
  onManageTools: () => void;
}) {
  const [data, setData] = useState<SessionContextStatus | SessionToolCatalog | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void inspectSession(sessionId, resource, controller.signal).then((result) => {
      if (!controller.signal.aborted) setData(result);
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setError(error instanceof Error ? error.message : "读取会话信息失败");
    });
    return () => controller.abort();
  }, [sessionId, resource]);

  const title = resource === "tools" ? "当前会话的工具" : "上下文预算";
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="llm-dialog settings-dialog session-inspection" role="dialog" aria-modal="true"
        aria-label={title} onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}>
        <div className="settings-dialog__header">
          <h3>{title}</h3>
          <button type="button" className="icon-button subtle" aria-label="关闭" autoFocus onClick={onClose}>×</button>
        </div>
        <div className="session-inspection__body">
          {error ? <p role="alert">{error}</p> : !data ? <p role="status">正在读取…</p> : "tools" in data ? (
            <>
              <p>{data.tools.length} 项可用 · 宿主目录版本 {data.revision}</p>
              <ul>
                {data.tools.map((tool) => <li key={tool.name}><code>{tool.name}</code><span>{toolSources[tool.source]}</span></li>)}
              </ul>
              <button type="button" className="btn secondary" onClick={onManageTools}>管理能力</button>
            </>
          ) : (
            <dl>
              <dt>模型</dt><dd>{data.model}</dd>
              <dt>最近估算 / 可用输入</dt><dd>{tokens(data.estimated_tokens)} / {tokens(data.input_budget)} tokens</dd>
              <dt>模型窗口</dt><dd>{tokens(data.context_window)} tokens</dd>
              <dt>输出预留</dt><dd>{tokens(data.output_reserve)} tokens</dd>
              <dt>预算来源</dt><dd>{sources[data.budget_source] ?? data.budget_source}</dd>
              <dt>最近压缩</dt><dd>{data.last_compaction
                ? `${tokens(data.last_compaction.tokens_before)} → ${tokens(data.last_compaction.tokens_after)} tokens`
                : "暂无压缩记录"}</dd>
            </dl>
          )}
        </div>
      </div>
    </div>
  );
}
