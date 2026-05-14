import { useAppPreferences } from "../i18n";

type HomePageProps = {
  authed: boolean;
  onOpenChat: () => void;
  onOpenPlatforms: () => void;
};

export function HomePage({ authed, onOpenChat, onOpenPlatforms }: HomePageProps) {
  const { t } = useAppPreferences();

  const heroStats = [
    { value: "01", label: t("home.hero.metric1"), tone: "cyan" },
    { value: "12+", label: t("home.hero.metric2"), tone: "green" },
    { value: "100%", label: t("home.hero.metric3"), tone: "amber" },
  ];

  const capabilityCards = [
    { title: t("home.capability.runtime"), text: t("home.capability.runtimeDesc"), icon: "RUN", metric: "sandbox/runtime" },
    { title: t("home.capability.audit"), text: t("home.capability.auditDesc"), icon: "AUD", metric: "timeline replay" },
    { title: t("home.capability.baseline"), text: t("home.capability.baselineDesc"), icon: "BASE", metric: "skills/files" },
    { title: t("home.capability.policy"), text: t("home.capability.policyDesc"), icon: "LLM", metric: "model policy" },
  ];

  const showcaseItems = [
    { title: t("home.section.platform.title"), text: t("home.section.platform.copy"), index: "01" },
    { title: t("home.section.chat.title"), text: t("home.section.chat.copy"), index: "02" },
    { title: t("home.section.access.title"), text: t("home.section.access.copy"), index: "03" },
  ];

  return (
    <main className="home-page">
      <section className="home-hero">
        <div className="home-hero__backdrop" aria-hidden="true">
          <span className="home-aurora home-aurora--cyan" />
          <span className="home-aurora home-aurora--green" />
          <span className="home-grid-glow" />
        </div>

        <div className="home-hero__content">
          <div className="home-hero__kicker">
            <span className="home-live-dot" />
            {t("home.hero.kicker")}
          </div>
          <h1>
            <span>AetherCore</span>
            <strong>Enterprise AI Control Plane</strong>
          </h1>
          <p className="home-hero__subtitle">{t("home.hero.subtitle")}</p>
          <div className="home-hero__actions">
            <button type="button" className="home-button home-button--primary" onClick={onOpenChat}>
              <span>{t("home.hero.primary")}</span>
              <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" strokeWidth="2" fill="none">
                <path d="M5 12h14m-6-6 6 6-6 6" />
              </svg>
            </button>
            <button type="button" className="home-button home-button--glass" onClick={onOpenPlatforms}>
              {t("home.hero.secondary")}
            </button>
          </div>
          <div className="home-hero__stats">
            {heroStats.map((item) => (
              <div key={item.label} className={`home-stat home-stat--${item.tone}`}>
                <strong>{item.value}</strong>
                <span>{item.label}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="home-orchestrator" aria-label="AetherCore product preview">
          <div className="home-orchestrator__chrome">
            <span />
            <span />
            <span />
            <strong>live platform run</strong>
          </div>
          <div className="home-orchestrator__body">
            <div className="home-orchestrator__left">
              <div className="home-platform-stack">
                {["CRM Console", "DevOps Portal", "Data Studio", "Internal SaaS"].map((item, index) => (
                  <div key={item} className={index === 0 ? "is-active" : ""}>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <strong>{item}</strong>
                    <small>{index === 0 ? "agent enabled" : "ready"}</small>
                  </div>
                ))}
              </div>
            </div>

            <div className="home-orchestrator__center">
              <div className="home-agent-card">
                <span className="home-agent-card__status">RUNNING</span>
                <h2>Agent Runtime</h2>
                <p>isolated workspace · network policy · file tools · command execution</p>
                <div className="home-runtime-rings">
                  <span />
                  <span />
                  <span />
                  <b />
                </div>
              </div>
              <div className="home-pipeline">
                {[t("home.flow.1"), t("home.flow.2"), t("home.flow.3"), t("home.flow.4")].map((item, index) => (
                  <div key={item}>
                    <span>{index + 1}</span>
                    <strong>{item}</strong>
                  </div>
                ))}
              </div>
            </div>

            <div className="home-orchestrator__right">
              <div className="home-signal-card">
                <strong>{t("home.hero.signal")}</strong>
                <p>{t("home.hero.signalText")}</p>
              </div>
              <div className="home-terminal">
                <code>&gt; runtime provisioned</code>
                <code>&gt; policy synced</code>
                <code>&gt; audit stream active</code>
                <code className="is-live">&gt; agent ready</code>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="home-marquee" aria-label="AetherCore capabilities">
        <div>
          <span>Sandbox Execution</span>
          <span>Platform Integration</span>
          <span>Prompt Governance</span>
          <span>Runtime Images</span>
          <span>Audit Replay</span>
          <span>Resource Baselines</span>
        </div>
      </section>

      <section className="home-showcase">
        {showcaseItems.map((item) => (
          <article key={item.index}>
            <span>{item.index}</span>
            <h2>{item.title}</h2>
            <p>{item.text}</p>
          </article>
        ))}
      </section>

      <section className="home-capabilities">
        {capabilityCards.map((item) => (
          <article key={item.title}>
            <div className="home-capability-icon">{item.icon}</div>
            <span>{item.metric}</span>
            <h3>{item.title}</h3>
            <p>{item.text}</p>
          </article>
        ))}
      </section>

      <section className="home-proof">
        <div>
          <span>{authed ? "SIGNED IN" : "ENTERPRISE READY"}</span>
          <h2>从接入到执行，从运行到审计，一套平台闭环。</h2>
        </div>
        <button type="button" className="home-button home-button--primary" onClick={onOpenPlatforms}>
          {t("home.hero.secondary")}
        </button>
      </section>
    </main>
  );
}
