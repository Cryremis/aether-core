// frontend/src/components/PanelCard.tsx
import type { PropsWithChildren, ReactNode } from "react";

type PanelCardProps = PropsWithChildren<{
  title: string;
  subtitle?: string;
  action?: ReactNode;
}>;

export function PanelCard({ title, subtitle, action, children }: PanelCardProps) {
  return (
    <section className="panel-card">
      <header className="panel-card__header">
        <div>
          <h2>{title}</h2>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
        {action ? <div>{action}</div> : null}
      </header>
      <div className="panel-card__body">{children}</div>
    </section>
  );
}
