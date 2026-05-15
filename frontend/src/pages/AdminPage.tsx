
// frontend/src/pages/AdminPage.tsx
import type { CurrentUserProfile } from "../api/client";
import { ManagementConsole } from "../components/ManagementConsole";
import { useAppPreferences } from "../i18n";

type AdminPageProps = {
  currentUser: CurrentUserProfile;
  onBack?: () => void;
  scope?: "all" | "system";
};

export function AdminPage({ currentUser, onBack, scope = "all" }: AdminPageProps) {
  const { t } = useAppPreferences();
  return (
    <main className="admin-page">
      <div className="admin-page__bg-mesh" />
      <section className="admin-page__content">
        <div className="admin-page__header stagger-1">
          {onBack ? (
            <button type="button" className="admin-page__back" onClick={onBack}>
              <span className="admin-page__back-arrow">‹</span>
              <span>{t("admin.backWorkbench")}</span>
            </button>
          ) : null}
          <div className="admin-page__title-group">
            <div className="admin-page__icon">
              <svg viewBox="0 0 24 24" width="28" height="28" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"></path>
              </svg>
            </div>
            <div>
              <h1>{scope === "system" ? t("system.title") : t("admin.console.title")}</h1>
              <p>{scope === "system" ? t("admin.system.subtitle") : t("admin.console.subtitle")}</p>
            </div>
          </div>
        </div>
        <ManagementConsole currentUser={currentUser} scope={scope} />
      </section>
    </main>
  );
}
