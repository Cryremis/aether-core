import type { WhitelistItem } from "./types";

type WhitelistRecordsProps = {
  whitelist: WhitelistItem[];
};

export function WhitelistRecords({ whitelist }: WhitelistRecordsProps) {
  return (
    <div className="admin-panel__list">
      <h4>白名单记录</h4>
      {whitelist.length === 0 ? <div className="admin-panel__empty">当前没有白名单记录。</div> : null}
      <div className="whitelist-grid">
        {whitelist.map((item) => (
          <article key={item.whitelist_id} className="admin-panel__card">
            <div className="flex-row">
              <strong>{item.full_name}</strong>
              <span className="badge">{item.role}</span>
            </div>
            <code>{item.provider} : {item.provider_user_id}</code>
          </article>
        ))}
      </div>
    </div>
  );
}
