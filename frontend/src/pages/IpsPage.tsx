import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { getSystemNetworkSnapshot, type CurrentUserProfile, type SystemNetworkAddress, type SystemNetworkSnapshot } from "../api/client";
import { useAppPreferences } from "../i18n";

type IpsPageProps = {
  currentUser: CurrentUserProfile;
};

function formatTimestamp(value: string, language: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(language, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(date);
}

function formatAddress(address: SystemNetworkAddress) {
  if (address.prefix_length == null) return address.address;
  return `${address.address}/${address.prefix_length}`;
}

export function IpsPage({ currentUser }: IpsPageProps) {
  const { language, t } = useAppPreferences();
  const [snapshot, setSnapshot] = useState<SystemNetworkSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadData = async () => {
    try {
      setLoading(true);
      setError("");
      const result = await getSystemNetworkSnapshot();
      setSnapshot((result.data ?? null) as SystemNetworkSnapshot | null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载服务器网络信息失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, []);

  return (
    <main className="ips-page">
      <section className="ips-page__hero">
        <div>
          <p className="platforms-hero__eyebrow">{currentUser.can_manage_system ? t("system.title") : currentUser.full_name}</p>
          <h1>{t("ips.title")}</h1>
          <p>{t("ips.subtitle")}</p>
        </div>
        <div className="ips-page__hero-actions">
          <Link className="home-button" to="/system">
            {t("ips.backSystem")}
          </Link>
          <button type="button" className="home-button home-button--primary" onClick={() => void loadData()}>
            {t("ips.refresh")}
          </button>
        </div>
      </section>

      {error ? (
        <div className="platforms-error">
          <span>{error}</span>
          <button type="button" onClick={() => void loadData()}>
            {t("common.retry")}
          </button>
        </div>
      ) : null}

      {loading ? <div className="platforms-loading">{t("ips.loading")}</div> : null}

      {!loading && snapshot ? (
        <>
          <section className="ips-summary-grid">
            <article className="ips-stat-card">
              <span>{t("ips.hostname")}</span>
              <strong>{snapshot.hostname}</strong>
              <p>{snapshot.fqdn || snapshot.scope_note}</p>
            </article>
            <article className="ips-stat-card">
              <span>{t("ips.interfaces")}</span>
              <strong>{snapshot.summary.interface_count}</strong>
              <p>{t("ips.interfacesUp")}: {snapshot.summary.up_interface_count}</p>
            </article>
            <article className="ips-stat-card">
              <span>{t("ips.ipv4")}</span>
              <strong>{snapshot.summary.ipv4_count}</strong>
              <p>{t("ips.ipv6")}: {snapshot.summary.ipv6_count}</p>
            </article>
            <article className="ips-stat-card">
              <span>{t("ips.public")}</span>
              <strong>{snapshot.summary.public_address_count}</strong>
              <p>{t(`ips.scope.${snapshot.namespace_scope}`)} · {snapshot.source}</p>
            </article>
          </section>

          <section className="ips-meta-card">
            <div>
              <span>{t("ips.platform")}</span>
              <strong>{snapshot.platform}</strong>
            </div>
            <div>
              <span>{t("ips.source")}</span>
              <strong>{snapshot.source}</strong>
            </div>
            <div>
              <span>{t("ips.scope")}</span>
              <strong>{t(`ips.scope.${snapshot.namespace_scope}`)}</strong>
            </div>
            <div>
              <span>{t("ips.collectedAt")}</span>
              <strong>{formatTimestamp(snapshot.collected_at, language)}</strong>
            </div>
          </section>

          <section className="ips-note-card">
            <p>{snapshot.scope_note}</p>
          </section>

          {snapshot.interfaces.length === 0 ? <div className="platforms-empty">{t("ips.empty")}</div> : null}

          <section className="ips-interface-grid">
            {snapshot.interfaces.map((item) => (
              <article key={item.name} className="ips-interface-card">
                <div className="ips-interface-card__head">
                  <div>
                    <h2>{item.display_name || item.name}</h2>
                    <p>{item.interface_type || item.name}</p>
                  </div>
                  <span className={`ips-state-pill${item.is_up ? " is-up" : ""}`}>{item.state}</span>
                </div>

                <div className="ips-interface-card__meta">
                  <div>
                    <span>{t("ips.mtu")}</span>
                    <strong>{item.mtu ?? "-"}</strong>
                  </div>
                  <div>
                    <span>{t("ips.mac")}</span>
                    <strong>{item.mac_address || "-"}</strong>
                  </div>
                </div>

                <div className="ips-flag-row">
                  <span>{t("ips.flags")}</span>
                  <div>
                    {item.flags.map((flag) => (
                      <code key={flag}>{flag}</code>
                    ))}
                  </div>
                </div>

                {item.addresses.length === 0 ? <p className="ips-interface-card__empty">{t("ips.noAddresses")}</p> : null}

                <div className="ips-address-list">
                  {item.addresses.map((address) => (
                    <div key={`${item.name}-${address.family}-${address.address}`} className="ips-address-card">
                      <div className="ips-address-card__top">
                        <code>{formatAddress(address)}</code>
                        <span className={`ips-category-pill ips-category-pill--${address.category}`}>{t(`ips.category.${address.category}`)}</span>
                      </div>
                      <div className="ips-address-card__meta">
                        <span>{address.family.toUpperCase()}</span>
                        <span>{address.scope || "-"}</span>
                        <span>{address.broadcast || "-"}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </article>
            ))}
          </section>

          <section className="ips-raw-card">
            <div className="ips-raw-card__head">
              <h2>{t("ips.raw")}</h2>
              <span>{snapshot.source}</span>
            </div>
            <pre>{snapshot.raw_text || t("ips.noRaw")}</pre>
          </section>
        </>
      ) : null}
    </main>
  );
}
