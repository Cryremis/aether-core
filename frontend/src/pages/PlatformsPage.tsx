import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  createPlatformRegistrationRequest,
  listMyPlatformRegistrationRequests,
  listPlatforms,
  type CurrentUserProfile,
  type PlatformRegistrationRequestPayload,
  type PlatformRegistrationRequestSummary,
} from "../api/client";
import type { PlatformItem } from "../components/admin/types";
import { PlatformRegistrationDialog } from "../components/workbench/PlatformRegistrationDialog";
import { useAppPreferences } from "../i18n";

type PlatformsPageProps = {
  currentUser: CurrentUserProfile;
};

export function PlatformsPage({ currentUser }: PlatformsPageProps) {
  const { t } = useAppPreferences();
  const [platforms, setPlatforms] = useState<PlatformItem[]>([]);
  const [registrationOpen, setRegistrationOpen] = useState(false);
  const [registrationBusy, setRegistrationBusy] = useState(false);
  const [registrationError, setRegistrationError] = useState("");
  const [registrationRequests, setRegistrationRequests] = useState<PlatformRegistrationRequestSummary[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const loadData = async () => {
    try {
      setLoading(true);
      setError("");
      const result = await listPlatforms();
      setPlatforms((result.data ?? []) as PlatformItem[]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载平台失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, []);

  const loadRegistrationRequests = async () => {
    const result = await listMyPlatformRegistrationRequests();
    setRegistrationRequests((result.data ?? []) as PlatformRegistrationRequestSummary[]);
  };

  const openRegistrationDialog = async () => {
    try {
      setRegistrationError("");
      await loadRegistrationRequests();
    } catch (err) {
      setRegistrationError(err instanceof Error ? err.message : "加载平台注册申请失败");
    } finally {
      setRegistrationOpen(true);
    }
  };

  const submitRegistrationRequest = async (payload: PlatformRegistrationRequestPayload) => {
    try {
      setRegistrationBusy(true);
      setRegistrationError("");
      await createPlatformRegistrationRequest(payload);
      await loadRegistrationRequests();
      await loadData();
    } catch (err) {
      setRegistrationError(err instanceof Error ? err.message : "提交平台注册申请失败");
      throw err;
    } finally {
      setRegistrationBusy(false);
    }
  };

  return (
    <main className="admin-page">
      <div className="admin-page__bg-mesh" />
      <section className="admin-page__content">
        <div className="admin-page__header stagger-1">
          <div className="admin-page__title-group platforms-page__title-group">
            <div className="admin-page__title-block">
              <div className="admin-page__icon">
                <svg viewBox="0 0 24 24" width="28" height="28" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="4" width="18" height="16" rx="3"></rect>
                  <path d="M7 8h10"></path>
                  <path d="M7 12h6"></path>
                  <path d="M7 16h4"></path>
                </svg>
              </div>
              <div className="platforms-hero__content">
                <h1>{t("platforms.title")}</h1>
                <p>{t("platforms.subtitle")}</p>
              </div>
            </div>
          </div>
        </div>

        <section className="platforms-page stagger-2">
          {error ? (
            <div className="platforms-error">
              <span>{error}</span>
              <button type="button" className="action-button action-button--ghost small" onClick={() => void loadData()}>
                {t("common.retry")}
              </button>
            </div>
          ) : null}

          {loading ? (
            <section className="platforms-grid platforms-grid--skeleton" aria-hidden="true">
              {Array.from({ length: 6 }).map((_, index) => (
                <article key={`platform-skeleton-${index}`} className="platform-entry-card platform-entry-card--skeleton epic-glass">
                  <div className="platform-entry-card__head">
                    <div className="platform-skeleton-line platform-skeleton-line--title" />
                    <div className="platform-skeleton-pill" />
                  </div>
                  <div className="platform-skeleton-line platform-skeleton-line--key" />
                  <div className="platform-skeleton-line platform-skeleton-line--body" />
                  <div className="platform-skeleton-line platform-skeleton-line--body short" />
                  <div className="platform-entry-card__meta">
                    <div>
                      <div className="platform-skeleton-line platform-skeleton-line--meta" />
                      <div className="platform-skeleton-line platform-skeleton-line--code" />
                    </div>
                    <div>
                      <div className="platform-skeleton-line platform-skeleton-line--meta" />
                      <div className="platform-skeleton-line platform-skeleton-line--code" />
                    </div>
                  </div>
                  <div className="platform-entry-card__footer">
                    <div className="platform-skeleton-line platform-skeleton-line--footer" />
                    <div className="platform-skeleton-button" />
                  </div>
                </article>
              ))}
            </section>
          ) : null}

          {!loading && platforms.length === 0 ? <div className="platforms-empty">{t("platforms.empty")}</div> : null}

          <section className="platforms-grid stagger-3">
            {platforms.map((platform) => (
              <article key={platform.platform_id} className="platform-entry-card epic-glass">
                <div className="platform-entry-card__head">
                  <div>
                    <h2>{platform.display_name}</h2>
                    <p>{platform.platform_key}</p>
                  </div>
                  <span>{platform.host_type || "embedded"}</span>
                </div>
                <p className="platform-entry-card__desc">{platform.description || t("platforms.noDescription")}</p>
                <div className="platform-entry-card__meta">
                  <div>
                    <span>{t("platforms.owner")}</span>
                    <strong>{platform.owner_name || t("platforms.unknownOwner")}</strong>
                  </div>
                  <div>
                    <span>{t("platforms.runtimeImage")}</span>
                    <code>{platform.resolved_sandbox_image}</code>
                  </div>
                </div>
                <div className="platform-entry-card__footer">
                  <code>{platform.host_secret}</code>
                  <Link to={`/platforms/${platform.platform_id}`} className="action-button primary small">
                    {t("platforms.open")}
                  </Link>
                </div>
              </article>
            ))}
          </section>

          <section className="platform-registration-entry epic-glass">
            <div className="platform-registration-entry__content">
              <strong>{t("platforms.registration.title")}</strong>
              <p>{t("platforms.registration.copy")}</p>
            </div>
            <button type="button" className="action-button primary platforms-page__header-action" onClick={() => void openRegistrationDialog()}>
              {t("platforms.registration.action")}
            </button>
          </section>
        </section>
      </section>

      <PlatformRegistrationDialog
        open={registrationOpen}
        busy={registrationBusy}
        error={registrationError}
        recentRequests={registrationRequests}
        onClose={() => setRegistrationOpen(false)}
        onSubmit={submitRegistrationRequest}
      />
    </main>
  );
}
