
// frontend/src/pages/AdminPage.tsx
import type { CurrentUserProfile } from "../api/client";
import { ManagementConsole } from "../components/ManagementConsole";

type AdminPageProps = {
  currentUser: CurrentUserProfile;
  onBack?: () => void;
};

export function AdminPage({ currentUser, onBack }: AdminPageProps) {
  return (
    <main className="admin-page">
      <div className="admin-page__bg-mesh" />
      <section className="admin-page__content">
        <div className="admin-page__header stagger-1">
          {onBack ? (
            <button type="button" className="admin-page__back" onClick={onBack}>
              <span className="admin-page__back-arrow">‹</span>
              <span>返回工作台</span>
            </button>
          ) : null}
          <div className="admin-page__title-group">
            <div className="admin-page__icon">
              <svg viewBox="0 0 24 24" width="28" height="28" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"></path>
              </svg>
            </div>
            <div>
              <h1>管理控制台</h1>
              <p>统一处理平台注册审批、负责人治理、系统角色授权，以及平台基线与模型配置。</p>
            </div>
          </div>
        </div>
        <ManagementConsole currentUser={currentUser} />
      </section>
    </main>
  );
}
