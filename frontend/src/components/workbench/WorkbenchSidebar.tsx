import { useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import { Link } from "react-router-dom";

import type { CurrentUserProfile } from "../../api/client";
import { useAppPreferences } from "../../i18n";
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
  onOpenPersonalSettings: () => void;
  onOpenLlmDialog: () => void;
  adminEntryHref?: string;
  onLogout?: () => void;
  onSidebarResizeStart: (event: ReactPointerEvent<HTMLDivElement>) => void;
  getDownloadUrl: (fileId: string) => string;
  onPreviewFile: (file: FileItem) => void;
};

function formatFileSize(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatModifiedAt(value: string | null | undefined, language: string, fallback: string) {
  if (!value) return fallback;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return fallback;
  return new Intl.DateTimeFormat(language, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
}

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
  onOpenPersonalSettings,
  onOpenLlmDialog,
  adminEntryHref,
  onLogout,
  onSidebarResizeStart,
  getDownloadUrl,
  onPreviewFile,
}: WorkbenchSidebarProps) {
  const { language, t } = useAppPreferences();
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const accountMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!accountMenuOpen) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!accountMenuRef.current?.contains(event.target as Node)) {
        setAccountMenuOpen(false);
      }
    };
    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [accountMenuOpen]);

  const roleLabel = currentUser?.can_manage_system
    ? t("account.role.systemAdmin")
    : currentUser?.can_manage_platforms
      ? `${t("account.role.platformAdmin")} · ${currentUser.managed_platform_count}`
      : t("account.role.user");

  const handleMenuAction = (action: () => void) => {
    setAccountMenuOpen(false);
    action();
  };

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
                  <span>{roleLabel}</span>
                </p>
              ) : (
                <p className="sidebar-user-meta">{t("workbench.user.embed")}</p>
              )}
            </div>
            {isMobile ? <button className="icon-button" onClick={onCloseSidebar}><Icons.Menu /></button> : null}
          </div>

          <div className="segment-control">
            <button className={`segment-btn ${sidebarView === "sessions" ? "active" : ""}`} onClick={() => onSidebarViewChange("sessions")}>{t("workbench.sidebar.sessions")}</button>
            <button className={`segment-btn ${sidebarView === "files" ? "active" : ""}`} onClick={() => onSidebarViewChange("files")}>{t("workbench.sidebar.files")}</button>
            <button className={`segment-btn ${sidebarView === "skills" ? "active" : ""}`} onClick={() => onSidebarViewChange("skills")}>{t("workbench.sidebar.skills")}</button>
          </div>

          <div className="sidebar-content">
            {sidebarView === "sessions" ? (
              <div className="tab-pane">
                <div className="pane-header">
                  <h3>{t("workbench.sidebar.history")}</h3>
                  <button type="button" className="action-button small" onClick={onNewConversation}>
                    {t("workbench.sidebar.new")}
                  </button>
                </div>
                <div className="item-list">
                  {conversations.length === 0 ? <div className="empty-state">{t("workbench.sidebar.noHistory")}</div> : null}
                  {conversations.map((item) => (
                    <div key={item.conversation_id} className={`history-item history-item--compact ${item.session_id === sessionId ? "is-active" : ""}`}>
                      <button type="button" className="history-item__main" onClick={() => onSessionSelect?.(item.session_id)}>
                        <span className="history-item__title" title={item.title || t("workbench.sidebar.newConversation")}>{item.title || t("workbench.sidebar.newConversation")}</span>
                      </button>
                      <div className="history-item__actions">
                        <button type="button" className="history-item__action-btn" title={t("workbench.sidebar.rename")} onClick={(e) => { e.stopPropagation(); onRenameSession?.(item.session_id, item.title); }}>
                          <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"></path></svg>
                        </button>
                        <button type="button" className="history-item__action-btn history-item__action-btn--delete" title={t("workbench.sidebar.delete")} onClick={(e) => { e.stopPropagation(); onDeleteSession?.(item.session_id); }}>
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
                  <h3>{t("workbench.sidebar.sessionFiles")}</h3>
                  <label className="action-button small">
                    <span>{t("workbench.sidebar.upload")}</span>
                    <input type="file" onChange={(e) => { onUploadFile(e.target.files?.[0]); e.currentTarget.value = ""; }} />
                  </label>
                </div>
                <div className="item-list">
                  {files.length === 0 ? <div className="empty-state">{t("workbench.sidebar.noFiles")}</div> : null}
                  {files.map((item, index) => (
                    <article key={item.file_id} className="resource-card anim-enter" style={{ animationDelay: `${index * 0.05}s` }}>
                      <div className="resource-icon"><Icons.File /></div>
                      <div className="resource-info">
                        <strong>{item.name}</strong>
                        <p>{formatFileSize(item.size)} · {formatModifiedAt(item.modified_at ?? item.created_at, language, t("workbench.sidebar.unknownTime"))}</p>
                      </div>
                      <div className="resource-actions">
                        <button type="button" className="download-btn" onClick={() => onPreviewFile(item)} title={t("workbench.sidebar.preview")}><Icons.Eye /></button>
                        <a className="download-btn" href={getDownloadUrl(item.file_id)} target="_blank" rel="noreferrer" title={t("workbench.sidebar.download")}><Icons.Download /></a>
                      </div>
                    </article>
                  ))}
                </div>
              </div>
            ) : (
              <div className="tab-pane">
                <div className="pane-header">
                  <h3>{t("workbench.sidebar.skillPackages")}</h3>
                  <label className="action-button small">
                    <span>{t("workbench.sidebar.upload")}</span>
                    <input type="file" accept=".zip,.md" onChange={(e) => { onUploadSkill(e.target.files?.[0]); e.currentTarget.value = ""; }} />
                  </label>
                </div>
                <div className="empty-state">{t("workbench.sidebar.skillHint")}</div>

                <h3 className="sub-title">{t("workbench.sidebar.loadedSkills")} ({skills.length})</h3>
                <div className="item-list">
                  {skills.length === 0 ? <div className="empty-state">{t("workbench.sidebar.noSkills")}</div> : null}
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
            <div className="sidebar-footer sidebar-footer--account" ref={accountMenuRef}>
              {accountMenuOpen ? (
                <div className="account-menu" role="menu">
                  <button type="button" className="account-menu__item" onClick={() => handleMenuAction(onOpenPersonalSettings)}>
                    <Icons.User />
                    <span>{t("workbench.sidebar.personalSettings")}</span>
                  </button>
                  <button type="button" className="account-menu__item" onClick={() => handleMenuAction(onOpenLlmDialog)}>
                    <Icons.Sliders />
                    <span>{t("workbench.sidebar.modelConfig")}</span>
                  </button>
                  {currentUser?.can_manage_platforms && adminEntryHref ? (
                    <Link className="account-menu__item" to={adminEntryHref} onClick={() => setAccountMenuOpen(false)}>
                      <Icons.Console />
                      <span>{t("workbench.sidebar.adminConsole")}</span>
                    </Link>
                  ) : null}
                  <button type="button" className="account-menu__item account-menu__item--danger" onClick={() => handleMenuAction(() => onLogout?.())}>
                    <Icons.LogOut />
                    <span>{t("workbench.sidebar.signOut")}</span>
                  </button>
                </div>
              ) : null}
              <button
                type="button"
                className={`account-menu-trigger${accountMenuOpen ? " is-open" : ""}`}
                aria-expanded={accountMenuOpen}
                aria-label={t("workbench.sidebar.accountMenu")}
                onClick={() => setAccountMenuOpen((current) => !current)}
              >
                <span className="account-menu-trigger__avatar">{(currentUser?.full_name || "A").slice(0, 1).toUpperCase()}</span>
                <span className="account-menu-trigger__meta">
                  <strong>{currentUser?.full_name || "AetherCore"}</strong>
                  <span>{roleLabel}</span>
                </span>
                <Icons.ChevronUp />
              </button>
            </div>
          ) : (
            <div className="sidebar-footer">
              <button type="button" className="action-button sidebar-footer__button sidebar-footer__button--ghost" onClick={onOpenLlmDialog}>
                {t("workbench.sidebar.modelConfig")}
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
