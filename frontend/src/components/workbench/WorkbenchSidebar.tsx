import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import { Link } from "react-router-dom";

import type { CurrentUserProfile } from "../../api/client";
import type { FileItem, SidebarView, SkillItem, WorkbenchConversation } from "../../pages/workbench/types";
import { WorkbenchIcons as Icons } from "./WorkbenchIcons";

type WorkbenchSidebarProps = {
  conversations: WorkbenchConversation[];
  currentUser?: CurrentUserProfile | null;
  isEmbedMode: boolean;
  isMobile: boolean;
  isSidebarOpen: boolean;
  isResizingSidebar: boolean;
  sidebarWidth: number;
  sidebarView: SidebarView;
  sessionId: string;
  files: FileItem[];
  skills: SkillItem[];
  onCloseSidebar: () => void;
  onSidebarViewChange: (view: SidebarView) => void;
  onNewConversation?: () => void;
  onSessionSelect?: (sessionId: string) => void;
  onRenameSession?: (sessionId: string, currentTitle: string) => void;
  onDeleteSession?: (sessionId: string) => void;
  onUploadFile: (file: File | undefined) => void;
  onUploadSkill: (file: File | undefined) => void;
  onOpenLlmDialog: () => void;
  adminEntryHref?: string;
  onOpenPlatformRegistration?: () => void;
  onLogout?: () => void;
  onSidebarResizeStart: (event: ReactPointerEvent<HTMLDivElement>) => void;
  getDownloadUrl: (fileId: string) => string;
};

export function WorkbenchSidebar({
  conversations,
  currentUser,
  isEmbedMode,
  isMobile,
  isSidebarOpen,
  isResizingSidebar,
  sidebarWidth,
  sidebarView,
  sessionId,
  files,
  skills,
  onCloseSidebar,
  onSidebarViewChange,
  onNewConversation,
  onSessionSelect,
  onRenameSession,
  onDeleteSession,
  onUploadFile,
  onUploadSkill,
  onOpenLlmDialog,
  adminEntryHref,
  onOpenPlatformRegistration,
  onLogout,
  onSidebarResizeStart,
  getDownloadUrl,
}: WorkbenchSidebarProps) {
  return (
    <>
      <aside
        className={`sidebar ${isSidebarOpen ? "is-open" : "is-closed"} ${isResizingSidebar ? "is-resizing" : ""}`}
        style={{ "--sidebar-width": `${sidebarWidth}px` } as CSSProperties}
      >
        <div className="sidebar-inner">
          <div className="sidebar-header">
            <div className="sidebar-header__title">
              <h1 className="brand-title">AetherCore</h1>
              {!isEmbedMode && currentUser ? (
                <p className="sidebar-user-meta">
                  {currentUser.full_name}
                  <span>
                    {currentUser.can_manage_system
                      ? "系统管理员"
                      : currentUser.can_manage_platforms
                        ? `平台负责人 · ${currentUser.managed_platform_count}`
                        : "普通用户"}
                  </span>
                </p>
              ) : (
                <p className="sidebar-user-meta">嵌入工作台</p>
              )}
            </div>
            {isMobile ? <button className="icon-button" onClick={onCloseSidebar}><Icons.Menu /></button> : null}
          </div>

          <div className="segment-control">
            <button className={`segment-btn ${sidebarView === "sessions" ? "active" : ""}`} onClick={() => onSidebarViewChange("sessions")}>会话</button>
            <button className={`segment-btn ${sidebarView === "files" ? "active" : ""}`} onClick={() => onSidebarViewChange("files")}>文件</button>
            <button className={`segment-btn ${sidebarView === "skills" ? "active" : ""}`} onClick={() => onSidebarViewChange("skills")}>技能</button>
          </div>

          <div className="sidebar-content">
            {sidebarView === "sessions" ? (
              <div className="tab-pane">
                <div className="pane-header">
                  <h3>历史会话</h3>
                  <button type="button" className="action-button small" onClick={onNewConversation}>
                    新建
                  </button>
                </div>
                <div className="item-list">
                  {conversations.length === 0 ? <div className="empty-state">暂无历史会话</div> : null}
                  {conversations.map((item) => (
                    <div key={item.conversation_id} className={`history-item history-item--compact ${item.session_id === sessionId ? "is-active" : ""}`}>
                      <button type="button" className="history-item__main" onClick={() => onSessionSelect?.(item.session_id)}>
                        <span className="history-item__title" title={item.title || "新对话"}>{item.title || "新对话"}</span>
                      </button>
                      <div className="history-item__actions">
                        <button type="button" className="history-item__action-btn" title="重命名" onClick={(e) => { e.stopPropagation(); onRenameSession?.(item.session_id, item.title); }}>
                          <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"></path></svg>
                        </button>
                        <button type="button" className="history-item__action-btn history-item__action-btn--delete" title="删除" onClick={(e) => { e.stopPropagation(); onDeleteSession?.(item.session_id); }}>
                          <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : sidebarView === "files" ? (
              <div className="tab-pane">
                <div className="pane-header">
                  <h3>会话文件</h3>
                  <label className="action-button small">
                    <span>上传</span>
                    <input type="file" onChange={(e) => { onUploadFile(e.target.files?.[0]); e.currentTarget.value = ""; }} />
                  </label>
                </div>
                <div className="item-list">
                  {files.length === 0 ? <div className="empty-state">当前暂无上传文件</div> : null}
                  {files.map((item, index) => (
                    <article key={item.file_id} className="resource-card anim-enter" style={{ animationDelay: `${index * 0.05}s` }}>
                      <div className="resource-icon"><Icons.File /></div>
                      <div className="resource-info">
                        <strong>{item.name}</strong>
                        <p>{item.category} · {(item.size / 1024).toFixed(1)} KB</p>
                      </div>
                      <a className="download-btn" href={getDownloadUrl(item.file_id)} target="_blank" rel="noreferrer" title="下载"><Icons.Download /></a>
                    </article>
                  ))}
                </div>
              </div>
            ) : (
              <div className="tab-pane">
                <div className="pane-header">
                  <h3>技能包</h3>
                  <label className="action-button small">
                    <span>上传</span>
                    <input type="file" accept=".zip,.md" onChange={(e) => { onUploadSkill(e.target.files?.[0]); e.currentTarget.value = ""; }} />
                  </label>
                </div>
                <div className="empty-state">支持上传真实技能包目录压缩成的 `.zip`，或单个 `SKILL.md` 文件。</div>

                <h3 className="sub-title">已加载技能 ({skills.length})</h3>
                <div className="item-list">
                  {skills.length === 0 ? <div className="empty-state">暂无已加载技能</div> : null}
                  {skills.map((item, index) => (
                    <article key={`${item.name}-${index}`} className="resource-card block anim-enter" style={{ animationDelay: `${index * 0.05 + 0.1}s` }}>
                      <div className="flex-row">
                        <strong>{item.name}</strong>
                        <span className="badge">{item.source}</span>
                      </div>
                      <p className="desc">{item.description}</p>
                    </article>
                  ))}
                </div>
              </div>
            )}
          </div>

          {!isEmbedMode ? (
            <div className="sidebar-footer">
              <button type="button" className="action-button sidebar-footer__button sidebar-footer__button--ghost" onClick={onOpenLlmDialog}>
                模型配置
              </button>
              <button type="button" className="action-button sidebar-footer__button sidebar-footer__button--ghost" onClick={onOpenPlatformRegistration}>
                申请注册新平台
              </button>
              {currentUser?.can_manage_platforms && adminEntryHref ? (
                <Link className="action-button sidebar-footer__button" to={adminEntryHref}>
                  管理配置
                </Link>
              ) : null}
              <button type="button" className="action-button sidebar-footer__button sidebar-footer__button--ghost" onClick={onLogout}>
                退出登录
              </button>
            </div>
          ) : (
            <div className="sidebar-footer">
              <button type="button" className="action-button sidebar-footer__button sidebar-footer__button--ghost" onClick={onOpenLlmDialog}>
                模型配置
              </button>
            </div>
          )}
        </div>
        {!isMobile ? (
          <div
            className="sidebar-resizer"
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize sidebar"
            onPointerDown={onSidebarResizeStart}
          />
        ) : null}
      </aside>

      {isMobile && isSidebarOpen ? <div className="sidebar-backdrop" onClick={onCloseSidebar}></div> : null}
    </>
  );
}
