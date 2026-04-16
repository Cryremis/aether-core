// frontend/src/pages/AdminPage.tsx
import { AdminPanel } from "../components/AdminPanel";

type AdminPageProps = {
  role: string;
};

export function AdminPage({ role }: AdminPageProps) {
  return (
    <main className="admin-page">
      <section className="admin-page__content">
        <div className="admin-page__header">
          <h1>管理配置</h1>
          <p>集中维护管理员白名单与平台注册信息，不再与工作台文件、技能侧栏混排。</p>
        </div>
        <AdminPanel role={role} />
      </section>
    </main>
  );
}
