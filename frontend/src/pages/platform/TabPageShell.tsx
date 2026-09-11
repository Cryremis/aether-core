import type { ReactNode } from "react";
import { useOutletContext } from "react-router-dom";

import type { PlatformDetailContext } from "./context";

type TabPageShellProps = {
  /** 卡片头部标题；配置类面板自带标题，传 null 则不渲染头部 */
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
};

/* 各 tab 页统一外壳：单层 epic-glass 大卡，与 /system、平台列表同构 */
export function TabPageShell({ title, description, actions, children }: TabPageShellProps) {
  return (
    <section className="platform-tab-page">
      <div className="management-console__section epic-glass platform-section-card">
        {title ? (
          <div className="management-console__section-head">
            <div>
              <h4>{title}</h4>
              {description ? <p>{description}</p> : null}
            </div>
            {actions ? <div className="platform-tab-page__actions">{actions}</div> : null}
          </div>
        ) : null}
        {children}
      </div>
    </section>
  );
}

/* 读取平台详情布局注入的上下文 */
export function usePlatformDetail(): PlatformDetailContext {
  return useOutletContext<PlatformDetailContext>();
}

/* 懒加载 tab 页的占位骨架 */
export function TabPageFallback() {
  return (
    <section className="platform-tab-page">
      <div className="management-console__section epic-glass platform-section-card">
        <div className="platform-tab-skeleton">
          <span className="platform-tab-skeleton__line platform-tab-skeleton__line--lg" />
          <span className="platform-tab-skeleton__line platform-tab-skeleton__line--md" />
          <span className="platform-tab-skeleton__line platform-tab-skeleton__line--sm" />
          <span className="platform-tab-skeleton__line platform-tab-skeleton__line--md" />
        </div>
      </div>
    </section>
  );
}
