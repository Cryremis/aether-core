// frontend/src/pages/HomePage.tsx
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useAppPreferences } from "../i18n";

type HomePageProps = {
  authed: boolean;
  onOpenChat: () => void;
  onOpenPlatforms: () => void;
};

// 鼠标追光特效
function useSpotlight() {
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let frameId = 0;
    const handleMouseMove = (e: MouseEvent) => {
      const target = e.target instanceof Element ? e.target.closest(".spotlight-card") : null;
      if (!(target instanceof HTMLElement)) return;
      if (frameId) {
        window.cancelAnimationFrame(frameId);
      }
      frameId = window.requestAnimationFrame(() => {
        const rect = target.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        target.style.setProperty("--mouse-x", `${x}px`);
        target.style.setProperty("--mouse-y", `${y}px`);
      });
    };
    container.addEventListener("mousemove", handleMouseMove);
    return () => {
      if (frameId) {
        window.cancelAnimationFrame(frameId);
      }
      container.removeEventListener("mousemove", handleMouseMove);
    };
  }, []);
  return containerRef;
}

// 滚动可见特效 (Scroll Reveal)
function useScrollReveal() {
  useEffect(() => {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-revealed');
        }
      });
    }, { threshold: 0.1, rootMargin: "0px 0px -50px 0px" });

    document.querySelectorAll('.reveal-on-scroll').forEach(el => observer.observe(el));
    return () => observer.disconnect();
  }, []);
}

function useHeroTilt() {
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;

    let frameId = 0;
    const setTilt = (clientX: number, clientY: number) => {
      if (frameId) window.cancelAnimationFrame(frameId);
      frameId = window.requestAnimationFrame(() => {
        const rect = wrapper.getBoundingClientRect();
        const x = (clientX - rect.left) / rect.width - 0.5;
        const y = (clientY - rect.top) / rect.height - 0.5;
        wrapper.style.setProperty("--hero-tilt-x", `${(-y * 7).toFixed(2)}deg`);
        wrapper.style.setProperty("--hero-tilt-y", `${(x * 9).toFixed(2)}deg`);
        wrapper.style.setProperty("--hero-shift-x", `${(x * 18).toFixed(2)}px`);
        wrapper.style.setProperty("--hero-shift-y", `${(y * 14).toFixed(2)}px`);
        wrapper.style.setProperty("--hero-glint-x", `${((x + 0.5) * 100).toFixed(2)}%`);
        wrapper.style.setProperty("--hero-glint-y", `${((y + 0.5) * 100).toFixed(2)}%`);
      });
    };

    const handlePointerMove = (event: PointerEvent) => setTilt(event.clientX, event.clientY);
    const handlePointerLeave = () => {
      wrapper.style.setProperty("--hero-tilt-x", "4deg");
      wrapper.style.setProperty("--hero-tilt-y", "-2deg");
      wrapper.style.setProperty("--hero-shift-x", "0px");
      wrapper.style.setProperty("--hero-shift-y", "0px");
      wrapper.style.setProperty("--hero-glint-x", "50%");
      wrapper.style.setProperty("--hero-glint-y", "42%");
    };

    wrapper.addEventListener("pointermove", handlePointerMove);
    wrapper.addEventListener("pointerleave", handlePointerLeave);
    return () => {
      if (frameId) window.cancelAnimationFrame(frameId);
      wrapper.removeEventListener("pointermove", handlePointerMove);
      wrapper.removeEventListener("pointerleave", handlePointerLeave);
    };
  }, []);

  return wrapperRef;
}

// 嵌入式工作台特效组件
function HeroEmbeddedMockup() {
  const { t } = useAppPreferences();
  const [step, setStep] = useState(0);

  useEffect(() => {
    let timeout: ReturnType<typeof setTimeout>;

    const runSequence = (currentStep: number) => {
      if (currentStep === 0) {
        // 初始状态，等待一下开始
        timeout = setTimeout(() => runSequence(1), 800); 
      } else if (currentStep === 1) {
        // 用户发送骨架屏
        timeout = setTimeout(() => runSequence(2), 800);
      } else if (currentStep === 2) {
        // AI 简短回复骨架屏
        timeout = setTimeout(() => runSequence(3), 1000);
      } else if (currentStep === 3) {
        // 出现工具调用 (Running)
        timeout = setTimeout(() => runSequence(4), 1800);
      } else if (currentStep === 4) {
        // 工具调用完成，出现 AI 大段回复骨架屏
        timeout = setTimeout(() => runSequence(0), 4500); 
      }
      setStep(currentStep);
    };

    runSequence(0);
    return () => clearTimeout(timeout);
  }, []);

  return (
    <div className="hero-embedded-mockup">
      {/* 1. 背景：模拟的宿主系统 (Host System) */}
      <div className="host-system-ui">
        <div className="host-sidebar">
          <div className="host-logo" />
          <div className="host-nav-item" />
          <div className="host-nav-item active" />
          <div className="host-nav-item" />
        </div>
        <div className="host-main">
          <div className="host-header">
            <div className="host-breadcrumb" />
            <div className="host-avatar" />
          </div>
          <div className="host-content">
            <div className="host-card-row">
               <div className="host-stat-card" />
               <div className="host-stat-card" />
               <div className="host-stat-card" />
            </div>
            <div className="host-table">
               <div className="host-table-header" />
               <div className="host-table-row" />
               <div className="host-table-row" />
               <div className="host-table-row" />
            </div>
          </div>
        </div>
      </div>

      {/* 2. 中景：嵌入的 Agent 面板 */}
      <div className="embedded-agent-panel">
        <div className="agent-panel-header">
          <div className="agent-title">
            <span className="live-dot" /> AI Copilot
          </div>
          <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" strokeWidth="2" fill="none"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
        </div>
        
        <div className="agent-panel-body">
          {/* 用户发送 */}
          <div className={`mock-msg user ${step >= 1 ? 'visible' : ''}`}>
             <div className="skeleton-block w-80" />
             <div className="skeleton-block w-60" />
          </div>
          
          {/* AI 响应 */}
          <div className={`mock-msg assistant ${step >= 2 ? 'visible' : ''}`}>
             <div className="skeleton-block w-40" />
          </div>

          {/* 工具调用 */}
          <div className={`mock-msg assistant ${step >= 3 ? 'visible' : ''}`}>
             <div className={`mock-tool-card ${step >= 4 ? 'done' : 'running'}`}>
                <div className="tool-card-header">
                   <span>
                     {step >= 4 
                       ? <svg viewBox="0 0 24 24" width="12" height="12" stroke="#10b981" strokeWidth="3" fill="none"><polyline points="20 6 9 17 4 12"/></svg>
                       : <span className="loader" />
                     }
                   </span>
                   <div className="skeleton-block w-40" style={{marginBottom: 0, height: 8}} />
                </div>
                <div className="tool-card-body">
                   <div className="skeleton-block w-80" style={{height: 8}} />
                   {step >= 4 && <div className="skeleton-block w-60" style={{height: 8}} />}
                </div>
             </div>
          </div>

          {/* AI 大段输出 */}
          <div className={`mock-msg assistant ${step >= 4 ? 'visible' : ''}`}>
             <div className="skeleton-block w-100" />
             <div className="skeleton-block w-100" />
             <div className="skeleton-block w-80" />
             <div className="skeleton-block w-60" />
          </div>
        </div>
        
        <div className="agent-panel-footer">
          <div className="fake-input">
             <div className="skeleton-block w-40" style={{marginBottom: 0}} />
          </div>
        </div>
      </div>

      {/* 3. 连线特效：数据流转 */}
      <div className="data-beams">
        <div className={`beam beam--intent ${step === 1 ? "is-active is-once" : ""}`}><div className="beam-label">{t("home.mock.intent")}</div></div>
        <div className={`beam beam--plan ${step >= 2 ? "is-active" : ""}`}><div className="beam-label">{t("home.mock.plan")}</div></div>
        <div className={`beam beam--tool-call ${step >= 3 ? "is-active" : ""}`}><div className="beam-label">{t("home.mock.toolCall")}</div></div>
        <div className={`beam beam--tool-result ${step >= 4 ? "is-active" : ""}`}><div className="beam-label">{t("home.mock.toolResult")}</div></div>
        <div className={`beam beam--stream ${step >= 2 ? "is-active" : ""}`}><div className="beam-label">{t("home.mock.stream")}</div></div>
        <div className={`beam beam--audit ${step >= 3 ? "is-active" : ""}`}><div className="beam-label">{t("home.mock.audit")}</div></div>
      </div>

      {/* 4. 远景/右侧：AetherCore 引擎 */}
      <div className="aether-engine">
        <div className="engine-core">
           <div className="engine-aura" />
           <div className="engine-ring ring-1" />
           <div className="engine-ring ring-2" />
           <div className="engine-ring ring-3" />
           <div className="engine-orbit orbit-1"><span /></div>
           <div className="engine-orbit orbit-2"><span /></div>
           <div className="engine-center">
              <span className="engine-core-mark" />
              <svg viewBox="0 0 24 24" width="24" height="24" stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round"><path d="M7 12h10"></path><path d="M12 7v10"></path><path d="M8.5 8.5 15.5 15.5"></path><path d="m15.5 8.5-7 7"></path></svg>
           </div>
        </div>
        <div className="engine-label">{t("home.mock.engine")}</div>
        <div className="engine-status">
           <span className="live-dot" /> {t("home.mock.ready")}
        </div>
      </div>
    </div>
  );
}

export function HomePage({ authed, onOpenChat, onOpenPlatforms }: HomePageProps) {
  const { t } = useAppPreferences();
  const spotlightRef = useSpotlight();
  const heroTiltRef = useHeroTilt();
  useScrollReveal();

  return (
    <main className="landing-page" ref={spotlightRef}>
      {/* 动态背景层 */}
      <div className="landing-bg-glows" aria-hidden="true">
        <div className="glow glow-1" />
        <div className="glow glow-2" />
        <div className="glow glow-3" />
        <div className="grid-overlay" />
      </div>

      {/* ================= 1. HERO 首屏 ================= */}
      <section className="hero-section">
        <div className="hero-badge animate-fade-in-up" style={{ animationDelay: "0.1s" }}>
          <span className="hero-badge-dot" />
          {t("home.hero.badge")}
        </div>
        
        <h1 className="hero-title animate-fade-in-up" style={{ animationDelay: "0.2s" }}>
          <span className="hero-title-main">{t("home.hero.titleMain")}</span>
          <br />
          <span className="hero-title-gradient">{t("home.hero.titleGradient")}</span>
        </h1>
        
        <p className="hero-subtitle animate-fade-in-up" style={{ animationDelay: "0.3s" }}>
          {t("home.hero.longSubtitle")}
        </p>
        
        <div className="hero-actions animate-fade-in-up" style={{ animationDelay: "0.4s" }}>
          <button type="button" className="btn-primary-epic" onClick={onOpenChat}>
            {t("home.hero.primary")}
            <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" strokeWidth="2" fill="none">
              <path d="M5 12h14m-6-6 6 6-6 6" />
            </svg>
          </button>
          <button type="button" className="btn-secondary-epic" onClick={onOpenPlatforms}>
            {t("home.adminConsole")}
          </button>
        </div>

        <div
          className="hero-visual-wrapper animate-fade-in-up"
          ref={heroTiltRef}
          style={{ animationDelay: "0.6s" } as CSSProperties}
        >
           <HeroEmbeddedMockup />
        </div>
      </section>

      {/* ================= 2. 核心协作范式 ================= */}
      <section className="synergy-section reveal-on-scroll">
        <div className="section-header text-center">
          <div className="feature-eyebrow">{t("home.paradigm.eyebrow")}</div>
          <h2>{t("home.paradigm.title")}</h2>
          <p>{t("home.paradigm.copy")}</p>
        </div>

        <div className="synergy-diagram">
          {/* Host Side */}
          <div className="synergy-box host-box spotlight-card">
            <div className="synergy-box-header">
              <div className="icon-wrap host"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg></div>
              <h3>{t("home.synergy.host")}</h3>
            </div>
            <ul className="synergy-list">
              <li><span className="check"/> {t("home.synergy.hostApi")}</li>
              <li><span className="check"/> {t("home.synergy.hostData")}</li>
              <li><span className="check"/> {t("home.synergy.hostSdk")}</li>
            </ul>
          </div>

          {/* Connection */}
          <div className="synergy-connection">
            <div className="conn-line forward">
              <span className="conn-line__text">{t("home.synergy.handshake")}</span>
              <span className="pulse-dot"></span>
            </div>
            <div className="conn-label">{t("home.synergy.binding")}</div>
            <div className="conn-line backward">
              <span className="conn-line__text">{t("home.synergy.token")}</span>
              <span className="pulse-dot"></span>
            </div>
            <div className="conn-security-note">{t("home.synergy.security")}</div>
          </div>

          {/* AetherCore Side */}
          <div className="synergy-box agent-box spotlight-card">
            <div className="synergy-box-header">
              <div className="icon-wrap agent"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg></div>
              <h3>{t("home.synergy.agent")}</h3>
            </div>
            <ul className="synergy-list">
              <li><span className="check"/> {t("home.synergy.intent")}</li>
              <li><span className="check"/> {t("home.synergy.sandbox")}</li>
              <li><span className="check"/> {t("home.synergy.memory")}</li>
            </ul>
          </div>
        </div>
      </section>

      {/* ================= 3. 开箱即用的神级能力 (Bento) ================= */}
      <section className="capabilities-section reveal-on-scroll">
        <div className="section-header text-center">
          <div className="feature-eyebrow">{t("home.capabilities.eyebrow")}</div>
          <h2>{t("home.capabilities.title")}</h2>
          <p>{t("home.capabilities.copy")}</p>
        </div>

        <div className="bento-grid capabilities-grid">
          
          {/* Timeline */}
          <article className="bento-card spotlight-card">
             <div className="bento-card-content">
                <div className="bento-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12h11c2 0 3-1.5 4-3l4-4M17 5h4v4M15 15c1 1 2.5 2.5 4 4l2 2M17 21h4v-4"></path></svg></div>
                <h3>{t("home.bento.timeline.title")}</h3>
                <p>{t("home.bento.timeline.copy")}</p>
             </div>
          </article>

          {/* Sandbox & Exec */}
          <article className="bento-card spotlight-card">
             <div className="bento-card-content">
                <div className="bento-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="16" rx="2"></rect><polyline points="7 9 10 12 7 15"></polyline><line x1="12" y1="15" x2="17" y2="15"></line></svg></div>
                <h3>{t("home.bento.sandbox.title")}</h3>
                <p>{t("home.bento.sandbox.copy")}</p>
             </div>
          </article>

          {/* Workboard & Elicitation */}
          <article className="bento-card spotlight-card">
             <div className="bento-card-content">
                <div className="bento-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 11l3 3L22 4"></path><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"></path></svg></div>
                <h3>{t("home.bento.workboard.title")}</h3>
                <p>{t("home.bento.workboard.copy")}</p>
             </div>
          </article>

          {/* Context Management */}
          <article className="bento-card spotlight-card">
             <div className="bento-card-content">
                <div className="bento-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg></div>
                <h3>{t("home.bento.context.title")}</h3>
                <p>{t("home.bento.context.copy")}</p>
             </div>
          </article>

          {/* Files & Skills */}
          <article className="bento-card spotlight-card wide-col-2">
             <div className="bento-card-content row-layout">
                <div className="card-text">
                  <div className="bento-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path><polyline points="13 2 13 9 20 9"></polyline></svg></div>
                  <h3>{t("home.bento.files.title")}</h3>
                  <p>{t("home.bento.files.copy")}</p>
                </div>
                <div className="card-visual mini-files">
                   <div className="file-chip">data_clean.py</div>
                   <div className="file-chip">CRM_API_SKILL.md</div>
                   <div className="file-chip highlight">report.csv (Output)</div>
                </div>
             </div>
          </article>

        </div>
      </section>

      {/* ================= 4. 四大应用场景 (Scenarios) ================= */}
      <section className="scenarios-section reveal-on-scroll">
        <div className="section-header text-center">
          <div className="feature-eyebrow">{t("home.scenarios.eyebrow")}</div>
          <h2>{t("home.scenarios.title")}</h2>
        </div>
        <div className="scenarios-grid">
          <div className="scenario-card spotlight-card">
             <h3>{t("home.scenario.aiops.title")}</h3>
             <p>{t("home.scenario.aiops.copy")}</p>
          </div>
          <div className="scenario-card spotlight-card">
             <h3>{t("home.scenario.copilot.title")}</h3>
             <p>{t("home.scenario.copilot.copy")}</p>
          </div>
          <div className="scenario-card spotlight-card">
             <h3>{t("home.scenario.data.title")}</h3>
             <p>{t("home.scenario.data.copy")}</p>
          </div>
          <div className="scenario-card spotlight-card">
             <h3>{t("home.scenario.support.title")}</h3>
             <p>{t("home.scenario.support.copy")}</p>
          </div>
        </div>
      </section>

      {/* ================= 5. 企业级后台治理 ================= */}
      <section className="governance-section reveal-on-scroll">
         <div className="governance-box spotlight-card">
            <div className="gov-text">
               <div className="feature-eyebrow">{t("home.governance.eyebrow")}</div>
               <h2>{t("home.governance.title")}</h2>
               <p>{t("home.governance.copy")}</p>
               <ul className="feature-list gov-list">
                 <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" /></svg> <strong>{t("home.governance.audit")}</strong> {t("home.governance.auditCopy")}</li>
                 <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></svg> <strong>{t("home.governance.permission")}</strong> {t("home.governance.permissionCopy")}</li>
                 <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="12 2 2 7 12 12 22 7 12 2" /><polyline points="2 17 12 22 22 17" /></svg> <strong>{t("home.governance.policy")}</strong> {t("home.governance.policyCopy")}</li>
               </ul>
            </div>
            <div className="gov-visual">
               <div className="gov-dash-mock">
                  <div className="mock-header">Admin Console</div>
                  <div className="mock-row"><span>Session: a83j2x</span><span className="badge green">Running</span></div>
                  <div className="mock-row"><span>Policy: Default GPT-4o</span><span className="badge gray">Active</span></div>
                  <div className="mock-row"><span>Audit: Tool invoked [CRM_Query]</span><span className="badge blue">Log</span></div>
               </div>
            </div>
         </div>
      </section>

      {/* ================= 6. 开发者极速接入 ================= */}
      <section className="developer-section reveal-on-scroll">
         <div className="developer-box spotlight-card">
            <div className="dev-text">
               <div className="feature-eyebrow">{t("home.developer.eyebrow")}</div>
               <h2>{t("home.developer.title")}</h2>
               <p>{t("home.developer.copy")}</p>
            </div>
            <div className="dev-code">
               <pre>
                  <code>
<span className="code-comment">{t("home.code.commentImport")}</span><br/>
<span className="code-keyword">import</span> {"{ initAetherCore }"} <span className="code-keyword">from</span> <span className="code-string">'@aethercore/sdk'</span>;<br/>
<br/>
<span className="code-func">initAetherCore</span>{"({"}<br/>
{"  "}platformKey: <span className="code-string">'my-crm-system'</span>,<br/>
{"  "}bindApi: <span className="code-string">'/api/aethercore/bind'</span>, <span className="code-comment">{t("home.code.commentProxy")}</span><br/>
{"  "}context: {"{ user_id: currentUser.id }"},<br/>
{"  "}hostTools: [<br/>
{"    {"}<br/>
{"      "}name: <span className="code-string">'query_customer'</span>,<br/>
{"      "}description: <span className="code-string">'{t("home.code.toolDescription")}'</span>,<br/>
{"      "}endpoint: <span className="code-string">'/api/internal/customer'</span><br/>
{"    }"}<br/>
{"  ]"}<br/>
{"});"}
                  </code>
               </pre>
            </div>
         </div>
      </section>

      {/* ================= 7. 底部 CTA ================= */}
      <section className="cta-section reveal-on-scroll">
        <div className="cta-box spotlight-card">
          <h2>{t("home.cta.title")}</h2>
          <p>{t("home.cta.copy")}</p>
          <div className="hero-actions" style={{ marginTop: '32px' }}>
            <button type="button" className="btn-primary-epic" onClick={onOpenChat}>
              {authed ? t("home.cta.authed") : t("home.cta.guest")}
            </button>
            <button type="button" className="btn-secondary-epic" onClick={onOpenPlatforms}>
              {t("home.cta.admin")}
            </button>
          </div>
        </div>
      </section>
    </main>
  );
}
