
import { useEffect, useMemo, useState } from "react";

import type {
  CurrentUserProfile,
  PlatformRegistrationRequestSummary,
  UserSummary,
} from "../api/client";
import {
  approvePlatformRegistrationRequest,
  assignPlatformAdmin,
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

type ManagementConsoleProps = {
  currentUser: CurrentUserProfile;
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

export function ManagementConsole({ currentUser }: ManagementConsoleProps) {
  const [activeTab, setActiveTab] = useState<"config" | "approvals" | "users" | "admins">("config");
  const [requests, setRequests] = useState<PlatformRegistrationRequestSummary[]>([]);
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [platforms, setPlatforms] = useState<PlatformOption[]>([]);
  const[selectedPlatformId, setSelectedPlatformId] = useState<number | null>(null);
  const[selectedPlatformAdmins, setSelectedPlatformAdmins] = useState<PlatformAdminRecord[]>([]);
  const [assignUserId, setAssignUserId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const[busyRequestId, setBusyRequestId] = useState<number | null>(null);
  const [busyUserId, setBusyUserId] = useState<number | null>(null);
  const [platformAdminBusy, setPlatformAdminBusy] = useState(false);

  const pendingRequests = useMemo(
    () => requests.filter((item) => item.status === "pending"),
    [requests],
  );

  const loadSystemGovernance = async () => {
    const[requestResult, userResult, platformResult] = await Promise.all([
      listPlatformRegistrationRequests(),
      listUsers(),
      listPlatforms(),
    ]);
    setRequests((requestResult.data ?? []) as PlatformRegistrationRequestSummary[]);
    setUsers((userResult.data ??[]) as UserSummary[]);
    const nextPlatforms = (platformResult.data ?? []) as PlatformOption[];
    setPlatforms(nextPlatforms);
    if (nextPlatforms.length > 0) {
      setSelectedPlatformId((current) =>
        current && nextPlatforms.some((item) => item.platform_id === current) ? current : nextPlatforms[0].platform_id,
      );
    }
  };

  useEffect(() => {
    if (!currentUser.can_manage_system) {
      return;
    }
    void loadSystemGovernance().catch((err) => {
      setError(err instanceof Error ? err.message : "加载治理数据失败");
    });
  }, [currentUser.can_manage_system]);

  useEffect(() => {
    if (!currentUser.can_manage_system || !selectedPlatformId) {
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
  },[currentUser.can_manage_system, selectedPlatformId]);

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
      setSelectedPlatformAdmins((result.data ??[]) as PlatformAdminRecord[]);
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

  return (
    <div className="management-console">
      {error ? <div className="admin-panel__error epic-error stagger-1">{error}</div> : null}

      {currentUser.can_manage_system && (
        <div className="admin-tabs-wrapper stagger-2">
          <nav className="admin-tabs">
            <button type="button" className={`admin-tab-btn ${activeTab === 'config' ? 'is-active' : ''}`} onClick={() => setActiveTab('config')}>
              平台配置
            </button>
            <button type="button" className={`admin-tab-btn ${activeTab === 'approvals' ? 'is-active' : ''}`} onClick={() => setActiveTab('approvals')}>
              注册审批 {pendingRequests.length > 0 && <span className="tab-badge">{pendingRequests.length}</span>}
            </button>
            <button type="button" className={`admin-tab-btn ${activeTab === 'users' ? 'is-active' : ''}`} onClick={() => setActiveTab('users')}>
              用户授权
            </button>
            <button type="button" className={`admin-tab-btn ${activeTab === 'admins' ? 'is-active' : ''}`} onClick={() => setActiveTab('admins')}>
              负责人治理
            </button>
          </nav>
        </div>
      )}

      {/* 利用 key 强制触发重新挂载和 CSS 动画 */}
      <div key={activeTab} className="admin-tab-content">
        {currentUser.can_manage_system && activeTab === 'approvals' && (
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

        {currentUser.can_manage_system && activeTab === 'users' && (
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

        {currentUser.can_manage_system && activeTab === 'admins' && (
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

        {(!currentUser.can_manage_system || activeTab === 'config') && (
          <section className="management-console__section">
            <AdminPanel role={currentUser.can_manage_system ? "system_admin" : currentUser.role} />
          </section>
        )}
      </div>
    </div>
  );
}
