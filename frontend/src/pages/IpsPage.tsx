import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  addRouteFor80Network,
  getSystemNetworkSnapshot,
  type AddRouteFor80NetworkResult,
  type CurrentUserProfile,
  type SystemNetworkAddress,
  type SystemNetworkSnapshot,
} from "../api/client";
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
  const [rawOpen, setRawOpen] = useState(false);
  const [routeBusy, setRouteBusy] = useState(false);
  const [routeResult, setRouteResult] = useState<AddRouteFor80NetworkResult | null>(null);

  const loadData = async () => {
    try {
      setLoading(true);
      setError("");
      setRouteResult(null);
      const result = await getSystemNetworkSnapshot();
      setSnapshot((result.data ?? null) as SystemNetworkSnapshot | null);
      setRawOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载服务器网络信息失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, []);

  const addressesStartingWith80 = snapshot
    ? snapshot.interfaces.flatMap((item) =>
        item.addresses
          .filter((address) => address.family === "ipv4" && address.address.startsWith("80."))
          .map((address) => ({
            interfaceName: item.display_name || item.name,
            address,
          })),
      )
    : [];

  const handleAddRoute = async () => {
    try {
      setRouteBusy(true);
      setError("");
      const result = await addRouteFor80Network();
      setRouteResult((result.data ?? null) as AddRouteFor80NetworkResult | null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "执行 80 网段路由失败");
    } finally {
      setRouteBusy(false);
    }
  };

  return (
    <main className="ips-page">
      <section className="ips-page__hero">
        <div>
          <p className="platforms-hero__eyebrow">{currentUser.can_manage_system ? t("system.title") : currentUser.full_name}</p>
          <h1>{t("ips.title")}</h1>
          <p>{t("ips.subtitle")}</p>
        </div>
        <div className="ips-page__hero-actions">
          <button type="button" className="home-button" onClick={() => setRawOpen(true)}>
            {t("ips.openRaw")}
          </button>
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
          <section className="ips-toolbar">
            <div className="ips-toolbar__primary">
              <strong>{snapshot.hostname}</strong>
              <span>{snapshot.fqdn || snapshot.scope_note}</span>
            </div>
            <div className="ips-toolbar__metrics">
              <div>
                <span>{t("ips.interfaces")}</span>
                <strong>{snapshot.summary.interface_count}</strong>
              </div>
              <div>
                <span>{t("ips.interfacesUp")}</span>
                <strong>{snapshot.summary.up_interface_count}</strong>
              </div>
              <div>
                <span>{t("ips.ipv4")}</span>
                <strong>{snapshot.summary.ipv4_count}</strong>
              </div>
              <div>
                <span>{t("ips.public")}</span>
                <strong>{snapshot.summary.public_address_count}</strong>
              </div>
            </div>
          </section>

          <section className="ips-context-bar">
            <span>{t("ips.platform")}: <strong>{snapshot.platform}</strong></span>
            <span>{t("ips.source")}: <strong>{snapshot.source}</strong></span>
            <span>{t("ips.scope")}: <strong>{t(`ips.scope.${snapshot.namespace_scope}`)}</strong></span>
            <span>{t("ips.collectedAt")}: <strong>{formatTimestamp(snapshot.collected_at, language)}</strong></span>
          </section>

          <section className="ips-note-strip">
            <p>{snapshot.scope_note}</p>
          </section>

          <section className="ips-special-route-panel">
            <div className="ips-special-route-panel__head">
              <div>
                <h2>80 段专用路由</h2>
                <p>单独显示当前实时采集到的 `80.*` IPv4 地址，并可一键执行固定命令 `route add -net 80.0.0.0 netmask 255.0.0.0 gw 80.253.32.1`。</p>
              </div>
              <span className="ips-special-route-panel__count">{addressesStartingWith80.length} 个 80 段地址</span>
            </div>

            {addressesStartingWith80.length === 0 ? (
              <div className="ips-special-route-panel__empty">当前没有发现以 80 开头的 IPv4 地址。</div>
            ) : (
              <div className="ips-special-route-list">
                {addressesStartingWith80.map(({ interfaceName, address }) => (
                  <article key={`${interfaceName}-${address.address}`} className="ips-special-route-card">
                    <div className="ips-special-route-card__meta">
                      <strong>{address.address}</strong>
                      <span>{interfaceName}</span>
                    </div>
                  </article>
                ))}
              </div>
            )}

            <div className="ips-special-route-panel__actions">
              <button
                type="button"
                className="home-button home-button--primary ips-special-route-card__action"
                disabled={routeBusy}
                onClick={() => void handleAddRoute()}
              >
                {routeBusy ? "执行中..." : "添加固定 80 网段路由"}
              </button>
            </div>

            {routeResult ? (
              <div className="ips-special-route-result">
                <strong>最近一次执行成功</strong>
                <code>{routeResult.command}</code>
                <span>固定网关：{routeResult.gateway_ip}</span>
                {routeResult.stdout ? <pre>{routeResult.stdout}</pre> : null}
                {routeResult.stderr ? <pre>{routeResult.stderr}</pre> : null}
              </div>
            ) : null}
          </section>

          {snapshot.interfaces.length === 0 ? <div className="platforms-empty">{t("ips.empty")}</div> : null}

          <section className="ips-list-shell">
            <div className="ips-list-head" aria-hidden="true">
              <span>{t("ips.interfaces")}</span>
              <span>{t("ips.primaryAddress")}</span>
              <span>{t("ips.addressCount")}</span>
              <span>{t("ips.state")}</span>
            </div>
            {snapshot.interfaces.map((item) => (
              <article key={item.name} className="ips-list-row">
                <div className="ips-list-row__summary">
                  <div className="ips-list-row__identity">
                    <strong>{item.display_name || item.name}</strong>
                    <span>{item.interface_type || item.name}</span>
                  </div>
                  <div className="ips-list-row__primary">
                    <code>{item.addresses[0] ? formatAddress(item.addresses[0]) : "-"}</code>
                  </div>
                  <div className="ips-list-row__count">
                    <strong>{item.addresses.length}</strong>
                  </div>
                  <div className="ips-list-row__state">
                    <span className={`ips-state-pill${item.is_up ? " is-up" : ""}`}>{item.state}</span>
                  </div>
                </div>

                <div className="ips-list-row__details">
                  <div>
                    <span>{t("ips.type")}</span>
                    <strong>{item.interface_type || "-"}</strong>
                  </div>
                  <div>
                    <span>{t("ips.mac")}</span>
                    <strong>{item.mac_address || "-"}</strong>
                  </div>
                  <div>
                    <span>{t("ips.mtu")}</span>
                    <strong>{item.mtu ?? "-"}</strong>
                  </div>
                  <div>
                    <span>{t("ips.flags")}</span>
                    <strong>{item.flags.length > 0 ? item.flags.join(" · ") : "-"}</strong>
                  </div>
                </div>

                {item.addresses.length === 0 ? <p className="ips-list-row__empty">{t("ips.noAddresses")}</p> : null}

                <div className="ips-address-column">
                  <span className="ips-address-column__label">{t("ips.addresses")}</span>
                  {item.addresses.map((address) => (
                    <div key={`${item.name}-${address.family}-${address.address}`} className="ips-address-row">
                      <div className="ips-address-row__main">
                        <code>{formatAddress(address)}</code>
                        <span className={`ips-category-pill ips-category-pill--${address.category}`}>{t(`ips.category.${address.category}`)}</span>
                      </div>
                      <div className="ips-address-row__meta">
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
        </>
      ) : null}

      {rawOpen && snapshot ? (
        <div className="ips-modal" role="dialog" aria-modal="true" aria-label={t("ips.raw")}>
          <button type="button" className="ips-modal__backdrop" onClick={() => setRawOpen(false)} aria-label={t("ips.closeRaw")} />
          <div className="ips-modal__panel">
            <div className="ips-modal__header">
              <div>
                <h2>{t("ips.raw")}</h2>
                <p>{snapshot.source}</p>
              </div>
              <button type="button" className="ips-modal__close" onClick={() => setRawOpen(false)}>
                {t("ips.closeRaw")}
              </button>
            </div>
            <pre>{snapshot.raw_text || t("ips.noRaw")}</pre>
          </div>
        </div>
      ) : null}
    </main>
  );
}
