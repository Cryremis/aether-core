import type { PlatformItem } from "./types";

type PlatformListProps = {
  platforms: PlatformItem[];
  activePlatformId: number | null;
  onSelect: (platformId: number) => void;
  onOpenGuide: (platform: PlatformItem) => void;
};

export function PlatformList({ platforms, activePlatformId, onSelect, onOpenGuide }: PlatformListProps) {
  return (
    <div className="admin-panel__list">
      <h4>管理的平台</h4>
      <div className="platform-grid">
        {platforms.length === 0 ? <div className="admin-panel__empty">当前没有可管理的平台。</div> : null}
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
                接入教程
              </button>
            </div>
            <p>{item.platform_key}</p>
            <p className="desc">{item.description || "未填写平台说明"}</p>
            <div className="secret-code"><code>{item.host_secret}</code></div>
          </article>
        ))}
      </div>
    </div>
  );
}
