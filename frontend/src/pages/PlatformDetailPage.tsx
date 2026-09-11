import { useEffect, useState } from "react";
import { Link, Navigate, NavLink, Outlet, useParams } from "react-router-dom";

import { getPlatformDetail, type CurrentUserProfile } from "../api/client";
import type { PlatformItem } from "../components/admin/types";
import { useAppPreferences } from "../i18n";

const TAB_ITEMS = [
  { key: "tutorial", path: "tutorial" },
  { key: "llm", path: "llm" },
  { key: "prompt", path: "prompt" },
  { key: "baseline", path: "baseline" },
  { key: "mcp", path: "mcp" },
  { key: "image", path: "image" },
  { key: "proxy", path: "proxy" },
  { key: "runtime", path: "runtime" },
  { key: "audit", path: "audit" },
] as const;

type PlatformDetailPageProps = {
  currentUser: CurrentUserProfile;
};

export function PlatformDetailPage({ currentUser }: PlatformDetailPageProps) {
  const { t } = useAppPreferences();
  const params = useParams();
  const platformId = Number(params.platformId);
  const [platform, setPlatform] = useState<PlatformItem | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!Number.isFinite(platformId) || platformId <= 0) return;
    void (async () => {
      try {
        setLoading(true);
        setError("");
        const result = await getPlatformDetail(platformId);
        setPlatform((result.data ?? null) as PlatformItem | null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载平台失败");
      } finally {
        setLoading(false);
      }
    })();
  }, [platformId]);

  if (!currentUser.can_manage_platforms) {
    return <Navigate to="/workbench" replace />;
  }

  if (!Number.isFinite(platformId) || platformId <= 0) {
    return <Navigate to="/platforms" replace />;
  }

  return (
    <main className="admin-page">
      <div className="admin-page__bg-mesh" />
      <section className="admin-page__content">
        <div className="admin-page__header stagger-1">
          <Link className="admin-page__back" to="/platforms">
            <span className="admin-page__back-arrow">‹</span>
            <span>{t("platformDetail.back")}</span>
          </Link>
          <div className="admin-page__title-group">
            <div className="admin-page__title-block">
              <div className="admin-page__icon">
                <svg viewBox="0 0 24 24" width="28" height="28" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 6a2 2 0 0 1 2-2h5l2 2h5a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"></path>
                  <path d="M8 12h8"></path>
                </svg>
              </div>
              <div>
                <p className="platforms-hero__eyebrow">{platform?.platform_key || "platform"}</p>
                <h1>{platform?.display_name || (loading ? t("common.loading") : "Platform")}</h1>
                <p>{platform?.description || t("platformDetail.descriptionFallback")}</p>
              </div>
            </div>
          </div>
        </div>
        <section className="platform-detail-page">
          {error ? <div className="platforms-error">{error}</div> : null}

          <nav className="platform-detail-nav stagger-2" aria-label={t("platformDetail.tabsLabel")}>
            <div className="admin-tabs">
              {TAB_ITEMS.map((item) => (
                <NavLink
                  key={item.key}
                  to={item.path}
                  className={({ isActive }) => `admin-tab-btn${isActive ? " is-active" : ""}`}
                >
                  {t(`platformDetail.${item.key}` as "platformDetail.tutorial")}
                </NavLink>
              ))}
            </div>
          </nav>

          <Outlet context={{ platform, loading }} />
        </section>
      </section>
    </main>
  );
}
