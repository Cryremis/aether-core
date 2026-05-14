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
    <main className="platforms-page">
      <section className="platforms-hero">
        <div>
          <p className="platforms-hero__eyebrow">{currentUser.can_manage_system ? t("platforms.systemEntry") : currentUser.full_name}</p>
          <h1>{t("platforms.title")}</h1>
          <p>{t("platforms.subtitle")}</p>
        </div>
        {currentUser.can_manage_system ? (
          <Link className="home-button home-button--primary" to="/system">
            {t("platforms.systemEntry")}
          </Link>
        ) : null}
      </section>

      {error ? (
        <div className="platforms-error">
          <span>{error}</span>
          <button type="button" onClick={() => void loadData()}>
            {t("common.retry")}
          </button>
        </div>
      ) : null}

      {loading ? <div className="platforms-loading">{t("common.loading")}</div> : null}

      {!loading && platforms.length === 0 ? <div className="platforms-empty">{t("platforms.empty")}</div> : null}

      <section className="platforms-grid">
        {platforms.map((platform) => (
          <article key={platform.platform_id} className="platform-entry-card">
            <div className="platform-entry-card__head">
              <div>
                <h2>{platform.display_name}</h2>
                <p>{platform.platform_key}</p>
              </div>
              <span>{platform.host_type || "embedded"}</span>
            </div>
            <p className="platform-entry-card__desc">{platform.description || "未填写平台说明"}</p>
            <div className="platform-entry-card__meta">
              <div>
                <span>{t("platforms.owner")}</span>
                <strong>{platform.owner_name || "未记录"}</strong>
              </div>
              <div>
                <span>{t("platforms.runtimeImage")}</span>
                <code>{platform.resolved_sandbox_image}</code>
              </div>
            </div>
            <div className="platform-entry-card__footer">
              <code>{platform.host_secret}</code>
              <Link to={`/platforms/${platform.platform_id}`} className="home-button home-button--primary">
                {t("platforms.open")}
              </Link>
            </div>
          </article>
        ))}
      </section>

      <section className="platform-registration-entry">
        <div>
          <span>Platform Registration</span>
          <strong>需要接入新的业务平台？</strong>
          <p>提交申请后，管理员审批通过会自动开通平台，并保留完整申请记录。</p>
        </div>
        <button type="button" className="home-button home-button--primary" onClick={() => void openRegistrationDialog()}>
          注册新接入平台
        </button>
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
