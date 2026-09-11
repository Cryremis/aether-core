import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import {
  getAdminConversationDetail, listAdminConversations,
  type AuditConversationDetail, type AuditConversationSummary,
} from "../../api/client";
import { convertAuditDetailToChatMessages, getRuntimeStatusClass, getRuntimeStatusLabel } from "../../components/ManagementConsole";
import { ChatTimeline } from "../../components/workbench/ChatTimeline";
import { useAppPreferences } from "../../i18n";
import { TabPageShell } from "./TabPageShell";

function formatTime(value?: string | null) {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function formatAuditOwnerLabel(item: { owner_user_name?: string | null; external_user_name?: string | null; external_user_id?: string | null }) {
  return item.owner_user_name || item.external_user_name || item.external_user_id || "未知";
}

function getAuditListTimestamp(item: { updated_at?: string | null; last_message_at?: string | null; created_at?: string | null }) {
  return formatTime(item.updated_at || item.last_message_at || item.created_at) || "未记录";
}

export default function PlatformAuditPage() {
  const { t } = useAppPreferences();
  const params = useParams();
  const platformId = Number(params.platformId);
  const [conversations, setConversations] = useState<AuditConversationSummary[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [selectedDetail, setSelectedDetail] = useState<AuditConversationDetail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  const loadAuditSessions = async () => {
    setBusy(true);
    setLoading(true);
    setError("");
    try {
      const result = await listAdminConversations(platformId);
      const items = (result.data ?? []) as AuditConversationSummary[];
      setConversations(items);
      const nextSelected = selectedSessionId && items.some((item) => item.session_id === selectedSessionId) ? selectedSessionId : items[0]?.session_id ?? "";
      setSelectedSessionId(nextSelected);
      if (!nextSelected) {
        setSelectedDetail(null);
        return;
      }
      const detail = await getAdminConversationDetail(nextSelected);
      setSelectedDetail((detail.data ?? null) as AuditConversationDetail | null);
    } finally {
      setBusy(false);
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!Number.isFinite(platformId) || platformId <= 0) return;
    void loadAuditSessions().catch((err) => {
      setError(err instanceof Error ? err.message : "加载审计会话失败");
      setLoading(false);
    });
  }, [platformId]);

  useEffect(() => {
    if (!selectedSessionId) return;
    void getAdminConversationDetail(selectedSessionId)
      .then((result) => setSelectedDetail((result.data ?? null) as AuditConversationDetail | null))
      .catch((err) => setError(err instanceof Error ? err.message : "加载审计会话详情失败"));
  }, [selectedSessionId]);

  return (
    <TabPageShell
      title={t("platformDetail.audit")}
      description={t("platformDetail.auditHint")}
      actions={(
        <button type="button" className="action-button action-button--ghost" disabled={busy} onClick={() => void loadAuditSessions()}>
          {busy ? t("platformDetail.refreshing") : t("platformDetail.refresh")}
        </button>
      )}
    >
        {error ? <div className="platforms-error">{error}</div> : null}
        <div className="management-console__audit-layout">
          <div className="management-console__audit-list">
            <div className="management-console__audit-list-scroll">
              {loading ? <div className="admin-panel__empty">正在加载审计会话...</div> : null}
              {!loading && conversations.length === 0 ? <div className="admin-panel__empty">{t("platformDetail.noAudit")}</div> : null}
              {conversations.map((item) => (
                <button key={item.session_id} type="button" className={`management-console__audit-card ${selectedSessionId === item.session_id ? "is-active" : ""}`} onClick={() => setSelectedSessionId(item.session_id)}>
                  <div className="management-console__audit-card-top">
                    <strong>{item.title || "新对话"}</strong>
                    <span className="request-status request-status--returned">{item.message_count} {t("platformDetail.messages")}</span>
                  </div>
                  <div className="management-console__audit-card-meta">
                    <span className="audit-meta-item">
                      <svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" strokeWidth="2" fill="none"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
                      {formatAuditOwnerLabel(item)}
                    </span>
                    <span className="audit-meta-item">
                      <svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" strokeWidth="2" fill="none"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                      {getAuditListTimestamp(item)}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          </div>
          <div className="management-console__audit-detail">
            {selectedDetail ? (
              <>
                <div className="management-console__audit-detail-header">
                  <div className="management-console__card-head">
                    <div>
                      <strong>{selectedDetail.audit.title || "新对话"}</strong>
                      <p>{selectedDetail.audit.platform_display_name || selectedDetail.host_name}</p>
                    </div>
                    <span className="request-status request-status--approved">{selectedDetail.message_count} {t("platformDetail.messages")}</span>
                  </div>
                  <div className="management-console__audit-detail-meta">
                    <span>用户：{formatAuditOwnerLabel(selectedDetail.audit)}</span>
                    <span>Session：{selectedDetail.session_id}</span>
                    <span>创建：{formatTime(selectedDetail.created_at) || t("common.notRecorded")}</span>
                    <span>更新：{formatTime(selectedDetail.audit.updated_at) || t("common.notRecorded")}</span>
                    <span>最后消息：{formatTime(selectedDetail.audit.last_message_at) || t("common.notRecorded")}</span>
                    <span>网络：{selectedDetail.allow_network ? "允许" : "受限"}</span>
                    {selectedDetail.runtime ? (
                      <span className={`runtime-status runtime-status--${getRuntimeStatusClass(selectedDetail.runtime.status)}`}>
                        Runtime：{getRuntimeStatusLabel(selectedDetail.runtime.status)}
                      </span>
                    ) : (
                      <span className="runtime-status runtime-status--none">Runtime：尚未创建</span>
                    )}
                  </div>
                </div>
                <div className="management-console__audit-timeline-wrapper">
                  <ChatTimeline loading={busy} messages={convertAuditDetailToChatMessages(selectedDetail)} actionsDisabled={true} />
                </div>
              </>
            ) : (
              <div className="admin-panel__empty management-console__audit-empty-detail">{t("platformDetail.selectAudit")}</div>
            )}
          </div>
        </div>
    </TabPageShell>
  );
}
