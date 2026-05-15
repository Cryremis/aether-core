import type { PlatformItem } from "./types";
import { useAppPreferences } from "../../i18n";

type PlatformListProps = {
  platforms: PlatformItem[];
  activePlatformId: number | null;
  onSelect: (platformId: number) => void;
  onOpenGuide: (platform: PlatformItem) => void;
};

export function PlatformList({ platforms, activePlatformId, onSelect, onOpenGuide }: PlatformListProps) {
  const { t } = useAppPreferences();

  return (
    <div className="admin-panel__list">
      <h4>{t("platforms.title")}</h4>
      <div className="platform-grid">
        {platforms.length === 0 ? <div className="admin-panel__empty">{t("platforms.empty")}</div> : null}
        {platforms.map((item) => (
          <article key={item.platform_id} className={`admin-panel__card ${activePlatformId === item.platform_id ? "is-active" : ""}`} onClick={() => onSelect(item.platform_id)}>
            <div className="platform-card__head">
              <strong>{item.display_name}</strong>
              <button
                type="button"
                className="action-button small action-button--ghost"
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenGuide(item);
                }}
              >
                {t("guide.title")}
              </button>
            </div>
            <p>{item.platform_key}</p>
            <p className="desc">{item.description || t("platforms.noDescription")}</p>
            <div className="platform-card__meta">
              <span>{t("platforms.runtimeImage")}</span>
              <code>{item.resolved_sandbox_image}</code>
            </div>
            <div className="secret-code"><code>{item.host_secret}</code></div>
          </article>
        ))}
      </div>
    </div>
  );
}
