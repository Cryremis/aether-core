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
      <section className="admin-page__content">
        <div className="admin-page__header">
          <div className="admin-page__header-top">
            {onBack ? (
              <button type="button" className="admin-page__back" onClick={onBack}>
                <span className="admin-page__back-arrow">‹</span>
                <span>工作台</span>
              </button>
            ) : null}
          </div>
          <h1>管理配置</h1>
          <p>统一处理平台注册审批、负责人治理、系统角色授权，以及平台基线与模型配置。</p>
        </div>
        <ManagementConsole currentUser={currentUser} />
      </section>
    </main>
  );
}
