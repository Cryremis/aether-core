// frontend/src/pages/AdminPage.tsx
import { AdminPanel } from "../components/AdminPanel";

type AdminPageProps = {
  role: string;
  onBack?: () => void;
};

export function AdminPage({ role, onBack }: AdminPageProps) {
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
          <p>集中维护管理员白名单、平台接入与默认平台配置，不再与工作台文件、技能侧栏混排。</p>
        </div>
        <AdminPanel role={role} />
      </section>
    </main>
  );
}
