import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";

import {
  collectAdminRuntime,
  getAdminConversationDetail,
  getPlatformIntegrationGuide,
  listAdminConversations,
  listAdminRuntimes,
  listAdminRuntimesHistory,
  listPlatforms,
  type AuditConversationDetail,
  type AuditConversationSummary,
  type CurrentUserProfile,
  type PlatformIntegrationGuide,
  type SessionRuntimeSummary,
} from "../api/client";
import { AdminPanel } from "../components/AdminPanel";
import {
  convertAuditDetailToChatMessages,
  getRuntimeStatusClass,
  getRuntimeStatusLabel,
  isRuntimeActive,
} from "../components/ManagementConsole";
import { IntegrationGuidePanel } from "../components/admin/IntegrationGuideModal";
import type { PlatformItem } from "../components/admin/types";
import { ChatTimeline } from "../components/workbench/ChatTimeline";
import { useAppPreferences } from "../i18n";

type PlatformDetailPageProps = {
  currentUser: CurrentUserProfile;
};

type DetailTab = "governance" | "integration" | "runtime" | "audit";

function formatTime(value?: string | null) {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

export function PlatformDetailPage({ currentUser }: PlatformDetailPageProps) {
  const { t } = useAppPreferences();
  const params = useParams();
  const platformId = Number(params.platformId);
  const [tab, setTab] = useState<DetailTab>("governance");
  const [platforms, setPlatforms] = useState<PlatformItem[]>([]);
  const [integrationGuide, setIntegrationGuide] = useState<PlatformIntegrationGuide | null>(null);
  const [runtimes, setRuntimes] = useState<SessionRuntimeSummary[]>([]);
  const [showRuntimeHistory, setShowRuntimeHistory] = useState(false);
  const [auditConversations, setAuditConversations] = useState<AuditConversationSummary[]>([]);
  const [selectedAuditSessionId, setSelectedAuditSessionId] = useState("");
  const [selectedAuditDetail, setSelectedAuditDetail] = useState<AuditConversationDetail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const platform = useMemo(
    () => platforms.find((item) => item.platform_id === platformId) ?? null,
    [platformId, platforms],
  );

  useEffect(() => {
    if (!Number.isFinite(platformId) || platformId <= 0) return;
    void (async () => {
      try {
        setLoading(true);
        setError("");
        const result = await listPlatforms();
        setPlatforms((result.data ?? []) as PlatformItem[]);
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载平台失败");
      } finally {
        setLoading(false);
      }
    })();
  }, [platformId]);

  useEffect(() => {
    if (!platformId || tab !== "integration") return;
    void getPlatformIntegrationGuide(platformId)
      .then((result) => setIntegrationGuide((result.data ?? null) as PlatformIntegrationGuide | null))
      .catch((err) => setError(err instanceof Error ? err.message : "加载接入教程失败"));
  }, [platformId, tab]);

  const loadRuntimes = async (includeHistory: boolean) => {
    const result = includeHistory ? await listAdminRuntimesHistory() : await listAdminRuntimes();
    const items = ((result.data ?? []) as SessionRuntimeSummary[]).filter((item) => item.platform_id === platformId);
    setRuntimes(items);
  };

  useEffect(() => {
    if (!platformId || tab !== "runtime") return;
    void loadRuntimes(showRuntimeHistory).catch((err) => setError(err instanceof Error ? err.message : "加载 runtime 列表失败"));
  }, [platformId, showRuntimeHistory, tab]);

  const loadAuditSessions = async () => {
    setBusy(true);
    try {
      const result = await listAdminConversations(platformId);
      const items = (result.data ?? []) as AuditConversationSummary[];
      setAuditConversations(items);
      const nextSelected = selectedAuditSessionId && items.some((item) => item.session_id === selectedAuditSessionId) ? selectedAuditSessionId : items[0]?.session_id ?? "";
      setSelectedAuditSessionId(nextSelected);
      if (!nextSelected) {
        setSelectedAuditDetail(null);
        return;
      }
      const detail = await getAdminConversationDetail(nextSelected);
      setSelectedAuditDetail((detail.data ?? null) as AuditConversationDetail | null);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!platformId || tab !== "audit") return;
    void loadAuditSessions().catch((err) => setError(err instanceof Error ? err.message : "加载审计会话失败"));
  }, [platformId, tab]);

  useEffect(() => {
    if (tab !== "audit" || !selectedAuditSessionId) return;
    void getAdminConversationDetail(selectedAuditSessionId)
      .then((result) => setSelectedAuditDetail((result.data ?? null) as AuditConversationDetail | null))
      .catch((err) => setError(err instanceof Error ? err.message : "加载审计会话详情失败"));
  }, [selectedAuditSessionId, tab]);

  const copyGuideText = async (value: string) => {
    await navigator.clipboard.writeText(value);
  };

  const renderHighlightedSnippet = (snippet: string | undefined) => {
    if (!snippet) return null;
    const parts = snippet.split(/(\{\{[A-Z0-9_]+\}\})/g);
    return parts.map((part, index) =>
      /^\{\{[A-Z0-9_]+\}\}$/.test(part) ? (
        <span key={`placeholder-${index}`} className="guide-placeholder">
          {part}
        </span>
      ) : (
        part
      ),
    );
  };

  const handleCollectRuntime = async (sessionId: string) => {
    if (!window.confirm("确定立即回收这个会话 runtime 吗？下次执行命令时会自动重建。")) return;
    try {
      setBusy(true);
      await collectAdminRuntime(sessionId);
      await loadRuntimes(showRuntimeHistory);
    } catch (err) {
      setError(err instanceof Error ? err.message : "回收 runtime 失败");
    } finally {
      setBusy(false);
    }
  };

  if (!currentUser.can_manage_platforms) {
    return <Navigate to="/workbench" replace />;
  }

  if (!Number.isFinite(platformId) || platformId <= 0) {
    return <Navigate to="/platforms" replace />;
  }

  return (
    <main className="platform-detail-page">
      <section className="platform-detail-hero">
        <Link className="admin-page__back" to="/platforms">
          <span className="admin-page__back-arrow">‹</span>
          <span>{t("platformDetail.back")}</span>
        </Link>
        <div className="platform-detail-hero__main">
          <div>
            <p className="platforms-hero__eyebrow">{platform?.platform_key || "platform"}</p>
            <h1>{platform?.display_name || (loading ? t("common.loading") : "Platform")}</h1>
            <p>{platform?.description || t("platformDetail.descriptionFallback")}</p>
          </div>
        </div>
      </section>

      {error ? <div className="platforms-error">{error}</div> : null}

      <nav className="admin-tabs platform-detail-tabs">
        {(["governance", "integration", "runtime", "audit"] as DetailTab[]).map((item) => (
          <button key={item} type="button" className={`admin-tab-btn ${tab === item ? "is-active" : ""}`} onClick={() => setTab(item)}>
            {t(`platformDetail.${item}` as "platformDetail.governance")}
          </button>
        ))}
      </nav>

      {tab === "integration" && (
        <IntegrationGuidePanel
          integrationGuide={integrationGuide}
          integrationGuideBusy={!integrationGuide && !error}
          integrationGuideError={error}
          integrationGuidePlatformName={platform?.display_name ?? ""}
          renderHighlightedSnippet={renderHighlightedSnippet}
          onCopy={(value) => void copyGuideText(value)}
        />
      )}

      {tab === "governance" && <AdminPanel role={currentUser.can_manage_system ? "system_admin" : currentUser.role} mode="detail" initialPlatformId={platformId} />}

      {tab === "runtime" && (
        <section className="management-console__section epic-glass platform-section-card">
          <div className="management-console__section-head">
            <div>
              <h4>{t("platformDetail.runtime")}</h4>
              <p>{t("platformDetail.runtimeHint")}</p>
            </div>
            <span className="management-console__metric">{runtimes.length} Runtime</span>
          </div>
          <div className="management-console__toolbar">
            <label className="management-console__toggle">
              <input type="checkbox" checked={showRuntimeHistory} onChange={(event) => setShowRuntimeHistory(event.target.checked)} />
              <span>{t("platformDetail.showHistory")}</span>
            </label>
            <button type="button" className="action-button action-button--ghost" disabled={busy} onClick={() => void loadRuntimes(showRuntimeHistory)}>
              {t("platformDetail.refresh")}
            </button>
          </div>
          <div className="management-console__cards">
            {runtimes.length === 0 ? <div className="admin-panel__empty">{t("platformDetail.noRuntime")}</div> : null}
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
        </section>
      )}

      {tab === "audit" && (
        <section className="management-console__section epic-glass platform-section-card">
          <div className="management-console__section-head">
            <div>
              <h4>{t("platformDetail.audit")}</h4>
              <p>{t("platformDetail.auditHint")}</p>
            </div>
            <button type="button" className="action-button action-button--ghost" disabled={busy} onClick={() => void loadAuditSessions()}>
              {busy ? t("platformDetail.refreshing") : t("platformDetail.refresh")}
            </button>
          </div>
          <div className="management-console__audit-layout">
            <div className="management-console__audit-list">
              {auditConversations.length === 0 ? <div className="admin-panel__empty">{t("platformDetail.noAudit")}</div> : null}
              {auditConversations.map((item) => (
                <button key={item.session_id} type="button" className={`management-console__audit-card ${selectedAuditSessionId === item.session_id ? "is-active" : ""}`} onClick={() => setSelectedAuditSessionId(item.session_id)}>
                  <div className="management-console__card-head">
                    <div>
                      <strong>{item.title || "新对话"}</strong>
                      <p>{item.owner_user_name || item.external_user_name || item.external_user_id || t("common.unknown")}</p>
                    </div>
                    <span className="request-status request-status--returned">{item.message_count} {t("platformDetail.messages")}</span>
                  </div>
                  <div className="management-console__audit-card-session"><code>{item.session_id.slice(0, 12)}...</code></div>
                </button>
              ))}
            </div>
            <div className="management-console__audit-detail">
              {selectedAuditDetail ? (
                <div className="management-console__audit-timeline-wrapper">
                  <ChatTimeline loading={busy} messages={convertAuditDetailToChatMessages(selectedAuditDetail)} actionsDisabled={true} />
                </div>
              ) : (
                <div className="admin-panel__empty management-console__audit-empty-detail">{t("platformDetail.selectAudit")}</div>
              )}
            </div>
          </div>
        </section>
      )}
    </main>
  );
}
