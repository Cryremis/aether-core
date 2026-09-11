import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { collectAdminRuntime, listAdminRuntimes, listAdminRuntimesHistory, type SessionRuntimeSummary } from "../../api/client";
import { getRuntimeStatusClass, getRuntimeStatusLabel, isRuntimeActive } from "../../components/ManagementConsole";
import { useAppPreferences } from "../../i18n";
import { TabPageShell } from "./TabPageShell";

function formatTime(value?: string | null) {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

export default function PlatformRuntimePage() {
  const { t } = useAppPreferences();
  const params = useParams();
  const platformId = Number(params.platformId);
  const [runtimes, setRuntimes] = useState<SessionRuntimeSummary[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  const loadRuntimes = async (includeHistory: boolean) => {
    setLoading(true);
    setError("");
    try {
      const result = includeHistory ? await listAdminRuntimesHistory() : await listAdminRuntimes();
      setRuntimes(((result.data ?? []) as SessionRuntimeSummary[]).filter((item) => item.platform_id === platformId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载 runtime 列表失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!Number.isFinite(platformId) || platformId <= 0) return;
    void loadRuntimes(showHistory);
  }, [platformId, showHistory]);

  const handleCollectRuntime = async (sessionId: string) => {
    if (!window.confirm("确定立即回收这个会话 runtime 吗？下次执行命令时会自动重建。")) return;
    try {
      setBusy(true);
      await collectAdminRuntime(sessionId);
      await loadRuntimes(showHistory);
    } catch (err) {
      setError(err instanceof Error ? err.message : "回收 runtime 失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <TabPageShell
      title={t("platformDetail.runtime")}
      description={t("platformDetail.runtimeHint")}
      actions={<span className="management-console__metric">{runtimes.length} Runtime</span>}
    >
        {error ? <div className="platforms-error">{error}</div> : null}
        <div className="management-console__toolbar">
          <label className="management-console__toggle">
            <input type="checkbox" checked={showHistory} onChange={(event) => setShowHistory(event.target.checked)} />
            <span>{t("platformDetail.showHistory")}</span>
          </label>
          <button type="button" className="action-button action-button--ghost" disabled={busy} onClick={() => void loadRuntimes(showHistory)}>
            {t("platformDetail.refresh")}
          </button>
        </div>
        <div className="management-console__cards">
          {loading ? <div className="admin-panel__empty">正在加载 runtime...</div> : null}
          {!loading && runtimes.length === 0 ? <div className="admin-panel__empty">{t("platformDetail.noRuntime")}</div> : null}
          {runtimes.map((item) => (
            <article key={item.session_id} className={`management-console__card runtime-card ${isRuntimeActive(item.status) ? "" : "runtime-card--closed"}`}>
              <div className="management-console__card-head">
                <div>
                  <strong>{item.conversation_title || item.session_id}</strong>
                  <p>{item.container_name || t("common.notRecorded")}</p>
                </div>
                <span className={`request-status request-status--${getRuntimeStatusClass(item.status)}`}>{getRuntimeStatusLabel(item.status)}</span>
              </div>
              <p>{t("platformDetail.owner")}：{item.owner_user_name || item.external_user_id || t("common.unknown")}</p>
              <p>{t("platformDetail.session")}：{item.session_id} · {t("platformDetail.generation")}：{item.generation ?? 0}</p>
              <p>{t("platformDetail.lastUsed")}：{formatTime(item.last_used_at) || t("common.notRecorded")} · {t("platformDetail.idleExpires")}：{formatTime(item.idle_expires_at) || t("common.notRecorded")}</p>
              {isRuntimeActive(item.status) ? (
                <button type="button" className="action-button action-button--ghost danger-button" disabled={busy || item.status === "busy"} onClick={() => void handleCollectRuntime(item.session_id)}>
                  {t("platformDetail.collectRuntime")}
                </button>
              ) : null}
            </article>
          ))}
        </div>
    </TabPageShell>
  );
}
