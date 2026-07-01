import { useEffect, useMemo, useState, useRef } from "react";

import type {
  AuditConversationDetail,
  AuditConversationSummary,
  CurrentUserProfile,
  PlatformAuditOverviewItem,
  PlatformRegistrationRequestSummary,
  SessionRuntimeSummary,
  SystemAuditOverview,
  TranscriptAssistantBlock,
  TranscriptChatMessage,
  UserSummary,
} from "../api/client";
import {
  approvePlatformRegistrationRequest,
  assignPlatformAdmin,
  collectAdminRuntime,
  getAdminConversationDetail,
  getSystemAuditOverview,
  listAdminConversations,
  listAdminRuntimes,
  listAdminRuntimesHistory,
  listPlatformRegistrationRequests,
  listPlatformAdmins,
  listPlatforms,
  listUsers,
  removePlatformAdmin,
  rejectPlatformRegistrationRequest,
  updatePlatformOwner,
  updateUserRole,
} from "../api/client";
import { AdminPanel } from "./AdminPanel";
import { ChatTimeline } from "./workbench/ChatTimeline";
import type { ChatMessage, AssistantBlock } from "../pages/workbench/types";

type ManagementConsoleProps = {
  currentUser: CurrentUserProfile;
  scope?: "all" | "system";
};

type PlatformOption = {
  platform_id: number;
  platform_key: string;
  display_name: string;
  owner_name: string;
};

type PlatformAdminRecord = {
  user_id: number;
  full_name: string;
  email?: string | null;
  role: string;
  assigned_at?: string | null;
  is_primary: boolean;
};

type ManagementTab = "config" | "approvals" | "users" | "admins" | "runtimes" | "audit" | "systemAudit";

function formatRequestStatus(status: PlatformRegistrationRequestSummary["status"]) {
  if (status === "pending") return "待审批";
  if (status === "approved") return "已通过";
  if (status === "rejected") return "已驳回";
  if (status === "returned") return "待补充";
  return "已取消";
}

function formatTime(value?: string | null) {
  if (!value) return "未记录";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function formatAuditOwnerLabel(item: {
  owner_user_name?: string | null;
  external_user_name?: string | null;
  external_user_id?: string | null;
}) {
  return item.owner_user_name || item.external_user_name || item.external_user_id || "未知";
}

function getAuditListTimestamp(item: {
  updated_at?: string | null;
  last_message_at?: string | null;
  created_at?: string | null;
}) {
  return formatTime(item.updated_at || item.last_message_at || item.created_at);
}

export function isRuntimeActive(status: string) {
  return ["provisioning", "running", "busy"].includes(status);
}

export function getRuntimeStatusLabel(status: string) {
  if (status === "running") return "运行中";
  if (status === "busy") return "执行中";
  if (status === "provisioning") return "创建中";
  if (status === "expired") return "已过期";
  if (status === "collected") return "已回收";
  if (status === "failed") return "已失效";
  if (status === "missing") return "容器丢失";
  return status;
}

export function getRuntimeStatusClass(status: string) {
  if (status === "running" || status === "busy" || status === "provisioning") return "approved";
  if (status === "expired" || status === "failed" || status === "missing") return "rejected";
  return "returned";
}

export function convertAuditDetailToChatMessages(detail: AuditConversationDetail | null): ChatMessage[] {
  if (!detail) return [];
  
  const transcript = detail.transcript;
  if (transcript && transcript.length > 0) {
    return transcript.map((msg, index): ChatMessage => {
      if (msg.role === "user") {
        return {
          id: `audit-${detail.session_id}-t-${index}`,
          role: "user",
          content: msg.content || "",
        };
      }
      
      if (msg.role === "elicitation_response") {
        return {
          id: `audit-${detail.session_id}-t-${index}`,
          role: "elicitation_response",
          title: msg.title,
          summary: msg.summary,
          answers: msg.answers,
        };
      }
      
      const msgBlocks = (msg as Extract<TranscriptChatMessage, { role: "assistant" }>).blocks ?? [];
      const blocks: AssistantBlock[] = msgBlocks.map((block, blockIndex): AssistantBlock => {
        if (block.kind === "reasoning") {
          return {
            id: `audit-${detail.session_id}-t-${index}-b-${blockIndex}`,
            kind: "reasoning",
            content: block.content || "",
          };
        }
        if (block.kind === "content") {
          return {
            id: `audit-${detail.session_id}-t-${index}-b-${blockIndex}`,
            kind: "content",
            content: block.content || "",
            status: "done",
          };
        }
        if (block.kind === "tool") {
          const toolBlock = block as Extract<TranscriptAssistantBlock, { kind: "tool" }>;
          return {
            id: `audit-${detail.session_id}-t-${index}-b-${blockIndex}`,
            kind: "tool",
            title: toolBlock.title || "Tool",
            meta: toolBlock.meta || "",
            argumentsText: toolBlock.argumentsText || "",
            outputText: toolBlock.outputText || "",
            status: "done",
          };
        }
        if (block.kind === "runtime_notice") {
          return {
            id: `audit-${detail.session_id}-t-${index}-b-${blockIndex}`,
            kind: "runtime_notice",
            eventType: "runtime_recreated",
            title: block.title || "",
            detail: block.detail,
          };
        }
        return {
          id: `audit-${detail.session_id}-t-${index}-b-${blockIndex}`,
          kind: "content",
          content: "",
          status: "done",
        };
      });
      
      return {
        id: `audit-${detail.session_id}-t-${index}`,
        role: "assistant",
        blocks,
        elapsedMs: null,
        streaming: false,
      };
    });
  }
  
  const messages = detail.messages ?? [];
  return messages.map((msg, index): ChatMessage => {
    if (msg.role === "user") {
      return {
        id: `audit-${detail.session_id}-m-${index}`,
        role: "user",
        content: msg.content || "",
      };
    }
    
    const blocks: AssistantBlock[] = [];
    const normalizedTopLevelContent = msg.content?.trim() ?? "";
    const normalizedBlockContents = Array.isArray(msg.blocks)
      ? msg.blocks
          .filter((block: Record<string, unknown>) => block.kind === "content" && typeof block.content === "string")
          .map((block: Record<string, unknown>) => String(block.content).trim())
          .filter(Boolean)
      : [];
    const shouldIncludeTopLevelContent = Boolean(
      normalizedTopLevelContent &&
      !normalizedBlockContents.some((content) => content === normalizedTopLevelContent),
    );

    if (shouldIncludeTopLevelContent) {
      blocks.push({
        id: `audit-${detail.session_id}-m-${index}-c`,
        kind: "content",
        content: normalizedTopLevelContent,
        status: "done",
      });
    }
    if (Array.isArray(msg.blocks) && msg.blocks.length > 0) {
      msg.blocks.forEach((block: Record<string, unknown>, blockIndex: number) => {
        const kind = block.kind as string;
        const content = typeof block.content === "string" ? block.content : "";
        if (kind === "reasoning") {
          blocks.push({
            id: `audit-${detail.session_id}-m-${index}-b-${blockIndex}`,
            kind: "reasoning",
            content,
          });
        } else if (kind === "content") {
          blocks.push({
            id: `audit-${detail.session_id}-m-${index}-b-${blockIndex}`,
            kind: "content",
            content,
            status: "done",
          });
        } else {
          blocks.push({
            id: `audit-${detail.session_id}-m-${index}-b-${blockIndex}`,
            kind: "content",
            content: JSON.stringify(block, null, 2),
            status: "done",
          });
        }
      });
    }
    
    return {
      id: `audit-${detail.session_id}-m-${index}`,
      role: "assistant",
      blocks,
      elapsedMs: null,
      streaming: false,
    };
  });
}

export function ManagementConsole({ currentUser, scope = "all" }: ManagementConsoleProps) {
  const [activeTab, setActiveTab] = useState<ManagementTab>(scope === "system" ? "approvals" : "config");
  const [requests, setRequests] = useState<PlatformRegistrationRequestSummary[]>([]);
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [runtimes, setRuntimes] = useState<SessionRuntimeSummary[]>([]);
  const [systemAuditOverview, setSystemAuditOverview] = useState<SystemAuditOverview | null>(null);
  const [auditConversations, setAuditConversations] = useState<AuditConversationSummary[]>([]);
  const [selectedAuditSessionId, setSelectedAuditSessionId] = useState<string>("");
  const [selectedAuditDetail, setSelectedAuditDetail] = useState<AuditConversationDetail | null>(null);
  const [platforms, setPlatforms] = useState<PlatformOption[]>([]);
  const [selectedPlatformId, setSelectedPlatformId] = useState<number | null>(null);
  const [selectedAuditPlatformId, setSelectedAuditPlatformId] = useState<number | null>(null);
  const [selectedPlatformAdmins, setSelectedPlatformAdmins] = useState<PlatformAdminRecord[]>([]);
  const [assignUserId, setAssignUserId] = useState<number | null>(null);
  const [showRuntimeHistory, setShowRuntimeHistory] = useState(false);
  const [auditBusy, setAuditBusy] = useState(false);
  const [systemAuditBusy, setSystemAuditBusy] = useState(false);
  const [error, setError] = useState("");
  const [busyRequestId, setBusyRequestId] = useState<number | null>(null);
  const [busyUserId, setBusyUserId] = useState<number | null>(null);
  const [platformAdminBusy, setPlatformAdminBusy] = useState(false);

  const pendingRequests = useMemo(
    () => requests.filter((item) => item.status === "pending"),
    [requests],
  );

  const canManage = currentUser.can_manage_platforms;
  const canManageSystem = currentUser.can_manage_system;

  const loadManagedPlatforms = async () => {
    const platformResult = await listPlatforms();
    const nextPlatforms = (platformResult.data ?? []) as PlatformOption[];
    setPlatforms(nextPlatforms);
    if (nextPlatforms.length === 0) {
      setSelectedPlatformId(null);
      setSelectedAuditPlatformId(null);
      return;
    }
    setSelectedPlatformId((current) =>
      current && nextPlatforms.some((item) => item.platform_id === current) ? current : nextPlatforms[0].platform_id,
    );
    setSelectedAuditPlatformId((current) =>
      current && nextPlatforms.some((item) => item.platform_id === current) ? current : nextPlatforms[0].platform_id,
    );
  };

  const loadSystemGovernance = async () => {
    const [requestResult, userResult, platformResult] = await Promise.all([
      listPlatformRegistrationRequests(),
      listUsers(),
      listPlatforms(),
    ]);
    setRequests((requestResult.data ?? []) as PlatformRegistrationRequestSummary[]);
    setUsers((userResult.data ?? []) as UserSummary[]);
    const nextPlatforms = (platformResult.data ?? []) as PlatformOption[];
    setPlatforms(nextPlatforms);
    if (nextPlatforms.length === 0) {
      setSelectedPlatformId(null);
      setSelectedAuditPlatformId(null);
      return;
    }
    setSelectedPlatformId((current) =>
      current && nextPlatforms.some((item) => item.platform_id === current) ? current : nextPlatforms[0].platform_id,
    );
    setSelectedAuditPlatformId((current) =>
      current && nextPlatforms.some((item) => item.platform_id === current) ? current : nextPlatforms[0].platform_id,
    );
  };

  const loadRuntimes = async (includeHistory: boolean) => {
    const result = includeHistory ? await listAdminRuntimesHistory() : await listAdminRuntimes();
    setRuntimes((result.data ?? []) as SessionRuntimeSummary[]);
  };

  const loadSystemAuditOverview = async () => {
    setSystemAuditBusy(true);
    try {
      const result = await getSystemAuditOverview();
      setSystemAuditOverview((result.data ?? null) as SystemAuditOverview | null);
    } finally {
      setSystemAuditBusy(false);
    }
  };

  const loadAuditSessions = async (platformId?: number | null) => {
    setAuditBusy(true);
    try {
      const result = await listAdminConversations(platformId ?? undefined);
      const items = (result.data ?? []) as AuditConversationSummary[];
      setAuditConversations(items);
      const nextSelectedId =
        selectedAuditSessionId && items.some((item) => item.session_id === selectedAuditSessionId)
          ? selectedAuditSessionId
          : items[0]?.session_id ?? "";
      setSelectedAuditSessionId(nextSelectedId);
      if (!nextSelectedId) {
        setSelectedAuditDetail(null);
        return;
      }
      const detailResult = await getAdminConversationDetail(nextSelectedId);
      setSelectedAuditDetail((detailResult.data ?? null) as AuditConversationDetail | null);
    } finally {
      setAuditBusy(false);
    }
  };

  useEffect(() => {
    if (!canManage) {
      return;
    }
    void (async () => {
      try {
        setError("");
        if (canManageSystem) {
          await loadSystemGovernance();
        } else {
          await loadManagedPlatforms();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载管理数据失败");
      }
    })();
  }, [canManage, canManageSystem]);

  useEffect(() => {
    if (!canManageSystem || !selectedPlatformId) {
      setSelectedPlatformAdmins([]);
      return;
    }
    void listPlatformAdmins(selectedPlatformId)
      .then((result) => {
        setSelectedPlatformAdmins((result.data ?? []) as PlatformAdminRecord[]);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "加载平台负责人失败");
      });
  }, [canManageSystem, selectedPlatformId]);

  useEffect(() => {
    if (!canManage || activeTab !== "runtimes") {
      return;
    }
    void loadRuntimes(showRuntimeHistory).catch((err) => {
      setError(err instanceof Error ? err.message : "加载 runtime 列表失败");
    });
  }, [activeTab, canManage, showRuntimeHistory]);

  useEffect(() => {
    if (!canManage || activeTab !== "audit") {
      return;
    }
    void loadAuditSessions(selectedAuditPlatformId).catch((err) => {
      setError(err instanceof Error ? err.message : "加载审计会话失败");
    });
  }, [activeTab, canManage, selectedAuditPlatformId]);

  useEffect(() => {
    if (!canManage || activeTab !== "audit" || !selectedAuditSessionId) {
      return;
    }
    void getAdminConversationDetail(selectedAuditSessionId)
      .then((result) => {
        setSelectedAuditDetail((result.data ?? null) as AuditConversationDetail | null);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "加载审计会话详情失败");
      });
  }, [activeTab, canManage, selectedAuditSessionId]);

  useEffect(() => {
    if (!canManageSystem || activeTab !== "systemAudit") {
      return;
    }
    void loadSystemAuditOverview().catch((err) => {
      setError(err instanceof Error ? err.message : "加载系统审计概览失败");
    });
  }, [activeTab, canManageSystem]);

  const handleApprove = async (requestId: number) => {
    const reviewComment = window.prompt("审批备注（可选）", "") ?? "";
    try {
      setBusyRequestId(requestId);
      setError("");
      await approvePlatformRegistrationRequest(requestId, { review_comment: reviewComment });
      await loadSystemGovernance();
    } catch (err) {
      setError(err instanceof Error ? err.message : "审批失败");
    } finally {
      setBusyRequestId(null);
    }
  };

  const handleReject = async (requestId: number) => {
    const reviewComment = window.prompt("驳回原因", "") ?? "";
    if (!reviewComment.trim()) {
      return;
    }
    try {
      setBusyRequestId(requestId);
      setError("");
      await rejectPlatformRegistrationRequest(requestId, { review_comment: reviewComment.trim() });
      await loadSystemGovernance();
    } catch (err) {
      setError(err instanceof Error ? err.message : "驳回失败");
    } finally {
      setBusyRequestId(null);
    }
  };

  const handleRoleChange = async (userId: number, role: UserSummary["role"]) => {
    try {
      setBusyUserId(userId);
      setError("");
      await updateUserRole(userId, { role });
      await loadSystemGovernance();
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新用户角色失败");
    } finally {
      setBusyUserId(null);
    }
  };

  const handleAssignPlatformAdmin = async () => {
    if (!selectedPlatformId || !assignUserId) {
      return;
    }
    try {
      setPlatformAdminBusy(true);
      setError("");
      await assignPlatformAdmin(selectedPlatformId, assignUserId);
      const result = await listPlatformAdmins(selectedPlatformId);
      setSelectedPlatformAdmins((result.data ?? []) as PlatformAdminRecord[]);
      setAssignUserId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "新增平台负责人失败");
    } finally {
      setPlatformAdminBusy(false);
    }
  };

  const handleRemovePlatformAdmin = async (userId: number) => {
    if (!selectedPlatformId) {
      return;
    }
    if (!window.confirm("确定移除该平台负责人吗？")) {
      return;
    }
    try {
      setPlatformAdminBusy(true);
      setError("");
      await removePlatformAdmin(selectedPlatformId, userId);
      const result = await listPlatformAdmins(selectedPlatformId);
      setSelectedPlatformAdmins((result.data ?? []) as PlatformAdminRecord[]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "移除平台负责人失败");
    } finally {
      setPlatformAdminBusy(false);
    }
  };

  const handlePromotePlatformOwner = async (userId: number) => {
    if (!selectedPlatformId) {
      return;
    }
    try {
      setPlatformAdminBusy(true);
      setError("");
      await updatePlatformOwner(selectedPlatformId, userId);
      const [platformResult, adminResult] = await Promise.all([
        listPlatforms(),
        listPlatformAdmins(selectedPlatformId),
      ]);
      setPlatforms((platformResult.data ?? []) as PlatformOption[]);
      setSelectedPlatformAdmins((adminResult.data ?? []) as PlatformAdminRecord[]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新平台主负责人失败");
    } finally {
      setPlatformAdminBusy(false);
    }
  };

  const handleCollectRuntime = async (sessionId: string) => {
    if (!window.confirm("确定立即回收这个会话 runtime 吗？下次执行命令时会自动重建。")) {
      return;
    }
    try {
      setPlatformAdminBusy(true);
      setError("");
      await collectAdminRuntime(sessionId);
      await loadRuntimes(showRuntimeHistory);
    } catch (err) {
      setError(err instanceof Error ? err.message : "回收 runtime 失败");
    } finally {
      setPlatformAdminBusy(false);
    }
  };

  return (
    <div className="management-console">
      {error ? <div className="admin-panel__error epic-error stagger-1">{error}</div> : null}

      {canManage && (
        <div className="admin-tabs-wrapper stagger-2">
          <nav className="admin-tabs">
            {scope === "all" ? (
              <button type="button" className={`admin-tab-btn ${activeTab === "config" ? "is-active" : ""}`} onClick={() => setActiveTab("config")}>
                平台配置
              </button>
            ) : null}
            {canManageSystem ? (
              <button type="button" className={`admin-tab-btn ${activeTab === "approvals" ? "is-active" : ""}`} onClick={() => setActiveTab("approvals")}>
                注册审批 {pendingRequests.length > 0 && <span className="tab-badge">{pendingRequests.length}</span>}
              </button>
            ) : null}
            {canManageSystem ? (
              <button type="button" className={`admin-tab-btn ${activeTab === "users" ? "is-active" : ""}`} onClick={() => setActiveTab("users")}>
                用户授权
              </button>
            ) : null}
            {canManageSystem ? (
              <button type="button" className={`admin-tab-btn ${activeTab === "systemAudit" ? "is-active" : ""}`} onClick={() => setActiveTab("systemAudit")}>
                审计概览
              </button>
            ) : null}
            {canManageSystem ? (
              <button type="button" className={`admin-tab-btn ${activeTab === "admins" ? "is-active" : ""}`} onClick={() => setActiveTab("admins")}>
                负责人治理
              </button>
            ) : null}
            {scope === "all" ? (
              <>
                <button type="button" className={`admin-tab-btn ${activeTab === "runtimes" ? "is-active" : ""}`} onClick={() => setActiveTab("runtimes")}>
                  Runtime
                </button>
                <button type="button" className={`admin-tab-btn ${activeTab === "audit" ? "is-active" : ""}`} onClick={() => setActiveTab("audit")}>
                  审计会话
                </button>
              </>
            ) : null}
          </nav>
        </div>
      )}

      <div key={activeTab} className="admin-tab-content">
        {canManageSystem && activeTab === "approvals" && (
          <section className="management-console__section epic-glass stagger-3">
            <div className="management-console__section-head">
              <div>
                <h4>平台注册审批</h4>
                <p>审批通过后会自动创建平台，并把申请人设为平台负责人。</p>
              </div>
              <span className="management-console__metric">{pendingRequests.length} 待处理</span>
            </div>
            <div className="management-console__cards">
              {requests.length === 0 ? (
                <div className="admin-panel__empty">当前没有平台注册申请。</div>
              ) : (
                requests.map((item) => (
                  <article key={item.request_id} className="management-console__card">
                    <div className="management-console__card-head">
                      <div>
                        <strong>{item.display_name}</strong>
                        <p>{item.platform_key}</p>
                      </div>
                      <span className={`request-status request-status--${item.status}`}>{formatRequestStatus(item.status)}</span>
                    </div>
                    <p>申请人：{item.applicant_name}{item.applicant_email ? ` · ${item.applicant_email}` : ""}</p>
                    <p>平台说明：{item.description || "未填写"}</p>
                    <p>申请理由：{item.justification || "未填写"}</p>
                    <p>提交时间：{formatTime(item.created_at)}</p>
                    {item.review_comment ? <p>审批备注：{item.review_comment}</p> : null}
                    {item.status === "pending" ? (
                      <div className="management-console__actions">
                        <button
                          type="button"
                          className="action-button primary"
                          disabled={busyRequestId === item.request_id}
                          onClick={() => void handleApprove(item.request_id)}
                        >
                          {busyRequestId === item.request_id ? "处理中..." : "通过"}
                        </button>
                        <button
                          type="button"
                          className="action-button action-button--ghost"
                          disabled={busyRequestId === item.request_id}
                          onClick={() => void handleReject(item.request_id)}
                        >
                          驳回
                        </button>
                      </div>
                    ) : null}
                  </article>
                ))
              )}
            </div>
          </section>
        )}

        {canManageSystem && activeTab === "users" && (
          <section className="management-console__section epic-glass stagger-3">
            <div className="management-console__section-head">
              <div>
                <h4>系统用户授权</h4>
                <p>系统管理员可以任命或调整已登录用户的全局角色。</p>
              </div>
              <span className="management-console__metric">{users.length} 用户</span>
            </div>
            <div className="management-console__table">
              {users.map((item) => (
                <article key={item.user_id} className="management-console__user-row">
                  <div>
                    <strong>{item.full_name}</strong>
                    <p>{item.username || item.email || item.provider}</p>
                    <p>已负责平台：{item.managed_platform_ids.length}</p>
                  </div>
                  <div className="management-console__user-controls">
                    <select
                      value={item.role}
                      disabled={busyUserId === item.user_id}
                      onChange={(event) => void handleRoleChange(item.user_id, event.target.value as UserSummary["role"])}
                    >
                      <option value="user">普通用户</option>
                      <option value="system_admin">系统管理员</option>
                    </select>
                    <span>{formatTime(item.last_login_at)}</span>
                  </div>
                </article>
              ))}
            </div>
          </section>
        )}

        {canManageSystem && activeTab === "admins" && (
          <section className="management-console__section epic-glass stagger-3">
            <div className="management-console__section-head">
              <div>
                <h4>平台负责人治理</h4>
                <p>系统管理员可以调整任一平台的负责人名单，平台通过审批后申请人会自动加入。</p>
              </div>
              <span className="management-console__metric">{platforms.length} 平台</span>
            </div>
            <div className="management-console__toolbar">
              <select
                value={selectedPlatformId ?? ""}
                onChange={(event) => setSelectedPlatformId(Number(event.target.value) || null)}
              >
                {platforms.map((platform) => (
                  <option key={platform.platform_id} value={platform.platform_id}>
                    {platform.display_name} · {platform.platform_key}
                  </option>
                ))}
              </select>
              <select
                value={assignUserId ?? ""}
                onChange={(event) => setAssignUserId(Number(event.target.value) || null)}
              >
                <option value="">选择已登录用户</option>
                {users.map((item) => (
                  <option key={item.user_id} value={item.user_id}>
                    {item.full_name} · {item.username || item.email || item.provider}
                  </option>
                ))}
              </select>
              <button type="button" className="action-button primary" disabled={platformAdminBusy || !assignUserId} onClick={() => void handleAssignPlatformAdmin()}>
                {platformAdminBusy ? "处理中..." : "添加负责人"}
              </button>
            </div>
            <div className="management-console__table">
              {selectedPlatformAdmins.map((item) => (
                <article key={item.user_id} className="management-console__user-row">
                  <div>
                    <strong>{item.full_name}</strong>
                    <p>{item.email || item.role}</p>
                    <p>{item.is_primary ? "主负责人" : "平台负责人"}</p>
                  </div>
                  <div className="management-console__user-controls">
                    <span>{formatTime(item.assigned_at)}</span>
                    {!item.is_primary ? (
                      <>
                        <button
                          type="button"
                          className="action-button primary"
                          disabled={platformAdminBusy}
                          onClick={() => void handlePromotePlatformOwner(item.user_id)}
                        >
                          设为主负责人
                        </button>
                        <button
                          type="button"
                          className="action-button action-button--ghost danger-button"
                          disabled={platformAdminBusy}
                          onClick={() => void handleRemovePlatformAdmin(item.user_id)}
                        >
                          移除
                        </button>
                      </>
                    ) : (
                      <span>当前主负责人</span>
                    )}
                  </div>
                </article>
              ))}
            </div>
          </section>
        )}

        {canManageSystem && activeTab === "systemAudit" && (
          <section className="management-console__section epic-glass stagger-3">
            <div className="management-console__section-head">
              <div>
                <h4>系统审计概览</h4>
                <p>按宿主平台聚合查看承载用户、会话消息和 runtime 规模，便于判断整体接入与负载情况。</p>
              </div>
              <button type="button" className="action-button action-button--ghost" disabled={systemAuditBusy} onClick={() => void loadSystemAuditOverview()}>
                {systemAuditBusy ? "刷新中..." : "刷新"}
              </button>
            </div>

            <div className="management-console__stats-grid">
              <article className="management-console__stat-card">
                <span>承载用户数</span>
                <strong>{systemAuditOverview?.hosted_user_count ?? 0}</strong>
                <p>跨所有宿主平台去重后的用户总数</p>
              </article>
              <article className="management-console__stat-card">
                <span>承载消息数</span>
                <strong>{systemAuditOverview?.message_count ?? 0}</strong>
                <p>所有宿主平台会话累计消息量</p>
              </article>
              <article className="management-console__stat-card">
                <span>宿主平台数</span>
                <strong>{systemAuditOverview?.platform_count ?? 0}</strong>
                <p>已注册的 embedded 宿主平台总数</p>
              </article>
              <article className="management-console__stat-card">
                <span>活跃 Runtime</span>
                <strong>{systemAuditOverview?.active_runtime_count ?? 0}</strong>
                <p>当前正在运行、创建中或执行中的容器</p>
              </article>
              <article className="management-console__stat-card">
                <span>宿主会话数</span>
                <strong>{systemAuditOverview?.conversation_count ?? 0}</strong>
                <p>所有宿主平台累计会话数</p>
              </article>
              <article className="management-console__stat-card">
                <span>内部账号数</span>
                <strong>{systemAuditOverview?.internal_user_count ?? 0}</strong>
                <p>AetherCore 当前已存在的登录账号数</p>
              </article>
              <article className="management-console__stat-card">
                <span>负责人指派数</span>
                <strong>{systemAuditOverview?.platform_admin_assignment_count ?? 0}</strong>
                <p>所有宿主平台上的负责人绑定总数</p>
              </article>
              <article className="management-console__stat-card">
                <span>待审批申请</span>
                <strong>{systemAuditOverview?.pending_registration_request_count ?? 0}</strong>
                <p>当前待处理的平台接入申请</p>
              </article>
            </div>

            <div className="management-console__section-head management-console__section-head--subtle">
              <div>
                <h4>平台承载明细</h4>
                <p>按平台查看用户、消息、会话和 runtime 分布。</p>
              </div>
              <span className="management-console__metric">{systemAuditOverview?.platforms_with_traffic_count ?? 0} 个平台已有业务流量</span>
            </div>

            <div className="management-console__table">
              {!systemAuditOverview || systemAuditOverview.platforms.length === 0 ? (
                <div className="admin-panel__empty">当前还没有宿主平台审计数据。</div>
              ) : (
                systemAuditOverview.platforms.map((platform: PlatformAuditOverviewItem) => (
                  <article key={platform.platform_id} className="management-console__audit-summary-card">
                    <div className="management-console__card-head">
                      <div>
                        <strong>{platform.display_name}</strong>
                        <p>{platform.platform_key}</p>
                      </div>
                      <span className="request-status request-status--returned">{platform.hosted_user_count} 用户</span>
                    </div>
                    <div className="management-console__summary-metrics">
                      <span>负责人：{platform.owner_name}</span>
                      <span>负责人席位：{platform.admin_count}</span>
                      <span>会话：{platform.conversation_count}</span>
                      <span>消息：{platform.message_count}</span>
                      <span>活跃 Runtime：{platform.active_runtime_count}</span>
                      <span>Runtime 总数：{platform.runtime_count}</span>
                      <span>最近活动：{formatTime(platform.last_activity_at)}</span>
                    </div>
                  </article>
                ))
              )}
            </div>
          </section>
        )}

        {canManage && activeTab === "runtimes" && (
          <section className="management-console__section epic-glass stagger-3">
            <div className="management-console__section-head">
              <div>
                <h4>会话 Runtime</h4>
                <p>默认只展示当前活跃 runtime。打开历史后可查看已回收、已过期或已失效的容器记录。</p>
              </div>
              <span className="management-console__metric">{runtimes.length} Runtime</span>
            </div>
            <div className="management-console__toolbar">
              <label className="management-console__toggle">
                <input type="checkbox" checked={showRuntimeHistory} onChange={(event) => setShowRuntimeHistory(event.target.checked)} />
                <span>显示已关闭 runtime</span>
              </label>
              <button type="button" className="action-button action-button--ghost" disabled={platformAdminBusy} onClick={() => void loadRuntimes(showRuntimeHistory)}>
                刷新列表
              </button>
            </div>
            <div className="management-console__cards">
              {runtimes.length === 0 ? (
                <div className="admin-panel__empty">{showRuntimeHistory ? "当前没有 runtime 记录。" : "当前没有活跃 runtime。"}</div>
              ) : (
                runtimes.map((item) => (
                  <article key={item.session_id} className={`management-console__card runtime-card ${isRuntimeActive(item.status) ? "" : "runtime-card--closed"}`}>
                    <div className="management-console__card-head">
                      <div>
                        <strong>{item.conversation_title || item.session_id}</strong>
                        <p>{item.container_name || "尚未创建容器"}</p>
                      </div>
                      <span className={`request-status request-status--${getRuntimeStatusClass(item.status)}`}>
                        {getRuntimeStatusLabel(item.status)}
                      </span>
                    </div>
                    <p>所属平台：{item.platform_display_name || "未绑定"} · 用户：{item.owner_user_name || item.external_user_id || "未知"}</p>
                    <p>Session：{item.session_id} · 代次：{item.generation ?? 0}</p>
                    <p>最近使用：{formatTime(item.last_used_at)} · 闲置到期：{formatTime(item.idle_expires_at)}</p>
                    <p>创建时间：{formatTime(item.created_at)} · 最大寿命：{formatTime(item.max_expires_at)}</p>
                    <p>销毁原因：{item.destroy_reason || "未销毁"}</p>
                    <div className="management-console__actions">
                      {isRuntimeActive(item.status) ? (
                        <button
                          type="button"
                          className="action-button action-button--ghost danger-button"
                          disabled={platformAdminBusy || item.status === "busy"}
                          onClick={() => void handleCollectRuntime(item.session_id)}
                        >
                          {platformAdminBusy ? "处理中..." : "回收容器"}
                        </button>
                      ) : (
                        <span className="management-console__hint">这个 runtime 已关闭；下次命令会按当前配置自动重建。</span>
                      )}
                    </div>
                  </article>
                ))
              )}
            </div>
          </section>
        )}

        {canManage && activeTab === "audit" && (
          <section className="management-console__section epic-glass stagger-3">
            <div className="management-console__section-head">
              <div>
                <h4>平台会话审计</h4>
                <p>查看当前可管理平台下的所有用户会话，并按需展开完整消息、runtime 与工作区状态。</p>
              </div>
              <span className="management-console__metric">{auditConversations.length} 会话</span>
            </div>
            <div className="management-console__audit-platform-grid">
              {platforms.length === 0 ? (
                <div className="admin-panel__empty">当前没有可管理的平台。</div>
              ) : (
                platforms.map((platform) => (
                  <button
                    key={platform.platform_id}
                    type="button"
                    className={`management-console__audit-platform-card ${selectedAuditPlatformId === platform.platform_id ? "is-active" : ""}`}
                    onClick={() => setSelectedAuditPlatformId(platform.platform_id)}
                  >
                    <strong>{platform.display_name}</strong>
                    <p className="platform-key">{platform.platform_key}</p>
                  </button>
                ))
              )}
            </div>
            <div className="management-console__audit-layout">
              <div className="management-console__audit-list">
                <div className="management-console__audit-list-header">
                  <span>历史会话</span>
                  <button
                    type="button"
                    className="action-button action-button--ghost small"
                    disabled={auditBusy}
                    onClick={() => void loadAuditSessions(selectedAuditPlatformId)}
                  >
                    {auditBusy ? "刷新中..." : "刷新"}
                  </button>
                </div>
                <div className="management-console__audit-list-scroll">
                  {auditConversations.length === 0 ? (
                    <div className="admin-panel__empty">当前平台还没有可审计会话。</div>
                  ) : (
                    auditConversations.map((item) => (
                      <button
                        key={item.session_id}
                        type="button"
                        className={`management-console__audit-card ${selectedAuditSessionId === item.session_id ? "is-active" : ""}`}
                        onClick={() => setSelectedAuditSessionId(item.session_id)}
                      >
                        <div className="management-console__audit-card-top">
                          <strong>{item.title || "新对话"}</strong>
                          <span className="request-status request-status--returned">{item.message_count} 条</span>
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
                    ))
                  )}
                </div>
              </div>
              <div className="management-console__audit-detail">
                {selectedAuditDetail ? (
                  <>
                    <div className="management-console__audit-detail-header">
                      <div className="management-console__card-head">
                        <div>
                          <strong>{selectedAuditDetail.audit.title || "新对话"}</strong>
                          <p>{selectedAuditDetail.audit.platform_display_name || selectedAuditDetail.host_name}</p>
                        </div>
                        <span className="request-status request-status--approved">{selectedAuditDetail.message_count} 条消息</span>
                      </div>
                      <div className="management-console__audit-detail-meta">
                        <span>用户：{formatAuditOwnerLabel(selectedAuditDetail.audit)}</span>
                        <span>Session：{selectedAuditDetail.session_id}</span>
                        <span>创建：{formatTime(selectedAuditDetail.created_at)}</span>
                        <span>更新：{formatTime(selectedAuditDetail.audit.updated_at)}</span>
                        <span>最后消息：{formatTime(selectedAuditDetail.audit.last_message_at)}</span>
                        <span>网络：{selectedAuditDetail.allow_network ? "允许" : "受限"}</span>
                        {selectedAuditDetail.runtime ? (
                          <span className={`runtime-status runtime-status--${getRuntimeStatusClass(selectedAuditDetail.runtime.status)}`}>
                            Runtime：{getRuntimeStatusLabel(selectedAuditDetail.runtime.status)}
                          </span>
                        ) : (
                          <span className="runtime-status runtime-status--none">Runtime：尚未创建</span>
                        )}
                      </div>
                    </div>
                    <div className="management-console__audit-timeline-wrapper">
                      <ChatTimeline
                        loading={auditBusy}
                        messages={convertAuditDetailToChatMessages(selectedAuditDetail)}
                        actionsDisabled={true}
                      />
                    </div>
                  </>
                ) : (
                  <div className="admin-panel__empty management-console__audit-empty-detail">
                    <div className="audit-empty-icon">
                      <svg viewBox="0 0 24 24" width="48" height="48" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                      </svg>
                    </div>
                    <p>选择左侧会话后，可在这里查看完整审计详情</p>
                  </div>
                )}
              </div>
            </div>
          </section>
        )}

        {activeTab === "config" && (
          <section className="management-console__section">
            <AdminPanel role={canManageSystem ? "system_admin" : currentUser.role} />
          </section>
        )}

        {scope === "system" && !canManageSystem ? (
          <section className="management-console__section epic-glass stagger-3">
            <div className="admin-panel__empty">当前账号没有系统管理权限。</div>
          </section>
        ) : null}
      </div>
    </div>
  );
}
