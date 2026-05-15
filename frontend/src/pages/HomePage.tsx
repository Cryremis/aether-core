// frontend/src/pages/HomePage.tsx
import { useEffect, useRef, useState } from "react";
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

// 嵌入式工作台特效组件
function HeroEmbeddedMockup() {
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
        <div className={`beam beam--intent ${step === 1 ? "is-active is-once" : ""}`}><div className="beam-label">Intent</div></div>
        <div className={`beam beam--plan ${step >= 2 ? "is-active" : ""}`}><div className="beam-label">Plan</div></div>
        <div className={`beam beam--tool-call ${step >= 3 ? "is-active" : ""}`}><div className="beam-label">Tool Call</div></div>
        <div className={`beam beam--tool-result ${step >= 4 ? "is-active" : ""}`}><div className="beam-label">Tool Result</div></div>
        <div className={`beam beam--stream ${step >= 2 ? "is-active" : ""}`}><div className="beam-label">AI Stream</div></div>
        <div className={`beam beam--audit ${step >= 3 ? "is-active" : ""}`}><div className="beam-label">Audit Sync</div></div>
      </div>

      {/* 4. 远景/右侧：AetherCore 引擎 */}
      <div className="aether-engine">
        <div className="engine-core">
           <div className="engine-ring ring-1" />
           <div className="engine-ring ring-2" />
           <div className="engine-center">
              <svg viewBox="0 0 24 24" width="24" height="24" stroke="currentColor" strokeWidth="2" fill="none"><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
           </div>
        </div>
        <div className="engine-label">AetherCore Engine</div>
        <div className="engine-status">
           <span className="live-dot" /> Sandbox Ready
        </div>
      </div>
    </div>
  );
}

export function HomePage({ authed, onOpenChat, onOpenPlatforms }: HomePageProps) {
  const { t } = useAppPreferences();
  const spotlightRef = useSpotlight();
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
          全托管式企业级 Agent 平台
        </div>
        
        <h1 className="hero-title animate-fade-in-up" style={{ animationDelay: "0.2s" }}>
          <span className="hero-title-main">把强大的 AI 引擎，</span>
          <br />
          <span className="hero-title-gradient">5分钟无缝嵌入你的系统</span>
        </h1>
        
        <p className="hero-subtitle animate-fade-in-up" style={{ animationDelay: "0.3s" }}>
          你只需提供平台 API，我们负责赋予它“大脑”。AetherCore 提供开箱即用的沙箱执行、复杂的会话记忆管理、主动反问能力，以及一套完整的企业级后台治理平面。
        </p>
        
        <div className="hero-actions animate-fade-in-up" style={{ animationDelay: "0.4s" }}>
          <button type="button" className="btn-primary-epic" onClick={onOpenChat}>
            体验 AI 工作台
            <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" strokeWidth="2" fill="none">
              <path d="M5 12h14m-6-6 6 6-6 6" />
            </svg>
          </button>
          <button type="button" className="btn-secondary-epic" onClick={onOpenPlatforms}>
            管理控制台
          </button>
        </div>

        <div className="hero-visual-wrapper animate-fade-in-up" style={{ animationDelay: "0.6s" }}>
           <HeroEmbeddedMockup />
        </div>
      </section>

      {/* ================= 2. 核心协作范式 ================= */}
      <section className="synergy-section reveal-on-scroll">
        <div className="section-header text-center">
          <div className="feature-eyebrow">The Paradigm</div>
          <h2>你提供工具接口，我提供智能大脑</h2>
          <p>打破“大模型不懂业务”的孤岛。宿主系统通过 SDK 暴露平台能力，AetherCore 的 Agent 将自动阅读接口文档、规划任务，并在安全的沙箱中调用你的系统接口完成操作。</p>
        </div>

        <div className="synergy-diagram">
          {/* Host Side */}
          <div className="synergy-box host-box spotlight-card">
            <div className="synergy-box-header">
              <div className="icon-wrap host"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg></div>
              <h3>你的业务系统 (Host)</h3>
            </div>
            <ul className="synergy-list">
              <li><span className="check"/> 提供 REST API / RPC 接口</li>
              <li><span className="check"/> 提供业务数据与上下文</li>
              <li><span className="check"/> 挂载 5 分钟前端 SDK</li>
            </ul>
          </div>

          {/* Connection */}
          <div className="synergy-connection">
            <div className="conn-line forward">
              <span className="conn-line__text">Host Secret Handshake</span>
              <span className="pulse-dot"></span>
            </div>
            <div className="conn-label">安全双向绑定</div>
            <div className="conn-line backward">
              <span className="conn-line__text">Tool Auth Token</span>
              <span className="pulse-dot"></span>
            </div>
            <div className="conn-security-note">身份校验 · 授权调用 · 审计留痕</div>
          </div>

          {/* AetherCore Side */}
          <div className="synergy-box agent-box spotlight-card">
            <div className="synergy-box-header">
              <div className="icon-wrap agent"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg></div>
              <h3>AetherCore 引擎 (Agent)</h3>
            </div>
            <ul className="synergy-list">
              <li><span className="check"/> 意图理解与任务拆解 (Workboard)</li>
              <li><span className="check"/> 安全的代码沙箱与命令执行</li>
              <li><span className="check"/> 多轮上下文与记忆管理</li>
            </ul>
          </div>
        </div>
      </section>

      {/* ================= 3. 开箱即用的神级能力 (Bento) ================= */}
      <section className="capabilities-section reveal-on-scroll">
        <div className="section-header text-center">
          <div className="feature-eyebrow">Out of the Box</div>
          <h2>我们造好了所有轮子，开箱即用</h2>
          <p>不用再手写繁琐的 Prompt 循环。AetherCore 包含了最前沿的 Agent 交互模式，让你的 AI 产品瞬间达到行业顶尖体验。</p>
        </div>

        <div className="bento-grid capabilities-grid">
          
          {/* Timeline */}
          <article className="bento-card spotlight-card">
             <div className="bento-card-content">
                <div className="bento-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12h11c2 0 3-1.5 4-3l4-4M17 5h4v4M15 15c1 1 2.5 2.5 4 4l2 2M17 21h4v-4"></path></svg></div>
                <h3>对话状态机 (Timeline)</h3>
                <p>支持对任意历史消息进行 Edit、Rerun（重跑）或 Fork（分叉成新会话）。就像使用 Git 一样管理多条对话分支。</p>
             </div>
          </article>

          {/* Sandbox & Exec */}
          <article className="bento-card spotlight-card">
             <div className="bento-card-content">
                <div className="bento-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="16" rx="2"></rect><polyline points="7 9 10 12 7 15"></polyline><line x1="12" y1="15" x2="17" y2="15"></line></svg></div>
                <h3>沙箱与命令执行</h3>
                <p>每个会话配备物理隔离的 Docker 容器。AI 可以直接编写、运行 Python/Bash 脚本，执行复杂数据处理或运维命令。</p>
             </div>
          </article>

          {/* Workboard & Elicitation */}
          <article className="bento-card spotlight-card">
             <div className="bento-card-content">
                <div className="bento-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 11l3 3L22 4"></path><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"></path></svg></div>
                <h3>智能任务看板 (Workboard)</h3>
                <p>遇到复杂任务？AI 会自动拆解生成 To-Do List。当需要人工决策时，AI 会主动发起问卷 (Elicitation) 阻断执行。</p>
             </div>
          </article>

          {/* Context Management */}
          <article className="bento-card spotlight-card">
             <div className="bento-card-content">
                <div className="bento-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg></div>
                <h3>上下文与动态压缩</h3>
                <p>内置精准的 Token 计算器。当对话过长时，系统会自动执行上下文压缩或截断策略，保证 LLM 永远在最佳窗口内运行。</p>
             </div>
          </article>

          {/* Files & Skills */}
          <article className="bento-card spotlight-card wide-col-2">
             <div className="bento-card-content row-layout">
                <div className="card-text">
                  <div className="bento-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path><polyline points="13 2 13 9 20 9"></polyline></svg></div>
                  <h3>文件系统与业务技能 (Skills)</h3>
                  <p>平台可为 Agent 预置“技能包(SKILL.md)”。用户与 AI 共享会话工作区，自由上传附件，AI 处理后的产物可直接下载预览。</p>
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
          <div className="feature-eyebrow">Scenarios</div>
          <h2>赋予业务系统“自动驾驶”的能力</h2>
        </div>
        <div className="scenarios-grid">
          <div className="scenario-card spotlight-card">
             <h3>自动化运维 (AIOps)</h3>
             <p>接入云平台或内网监控系统，Agent 可在沙箱内自动拉取日志、分析异常、生成排障脚本并直接执行，将 MTTR 缩短 80%。</p>
          </div>
          <div className="scenario-card spotlight-card">
             <h3>内部 SaaS 领航员 (Copilot)</h3>
             <p>嵌入 CRM、ERP 等业务后台。销售或运营只需用自然语言说话，Agent 调用宿主 API 完成“查询客户、创建工单、发送邮件”的连串操作。</p>
          </div>
          <div className="scenario-card spotlight-card">
             <h3>数据分析与可视化</h3>
             <p>丢给 AI 一份海量业务数据表格，Agent 自动编写 Python Pandas 脚本清洗数据，运行得出结论并直接生成图表文件供你下载。</p>
          </div>
          <div className="scenario-card spotlight-card">
             <h3>企业级智能客服</h3>
             <p>挂载企业的知识库基线 (Baseline)，利用主动反问 (Elicitation) 机制澄清客户模糊意图，提供零幻觉的精准技术支持。</p>
          </div>
        </div>
      </section>

      {/* ================= 5. 企业级后台治理 ================= */}
      <section className="governance-section reveal-on-scroll">
         <div className="governance-box spotlight-card">
            <div className="gov-text">
               <div className="feature-eyebrow">Administration</div>
               <h2>完善的后台治理，满足合规要求</h2>
               <p>AetherCore 不仅提供前台交互，更为管理员准备了强大的后台管控中心。一切尽在掌握。</p>
               <ul className="feature-list gov-list">
                 <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" /></svg> <strong>全量审计回放：</strong> 记录每一次思考、每一次工具调用的入参出参。</li>
                 <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></svg> <strong>细粒度权限管控：</strong> 多平台隔离，独立的负责人审批与资源基线管理。</li>
                 <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="12 2 2 7 12 12 22 7 12 2" /><polyline points="2 17 12 22 22 17" /></svg> <strong>统一模型策略：</strong> 全局配置 LLM 网关、网络搜索白名单与统一系统提示词。</li>
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
               <div className="feature-eyebrow">Developer Experience</div>
               <h2>极简 SDK，无痛接入</h2>
               <p>你的平台无需重构。只需引入 AetherCore SDK，传入业务上下文与你希望暴露的工具接口，剩下的全部交给我们。</p>
            </div>
            <div className="dev-code">
               <pre>
                  <code>
<span className="code-comment">{"// 1. 在你的业务系统前端引入 SDK"}</span><br/>
<span className="code-keyword">import</span> {"{ initAetherCore }"} <span className="code-keyword">from</span> <span className="code-string">'@aethercore/sdk'</span>;<br/>
<br/>
<span className="code-func">initAetherCore</span>{"({"}<br/>
{"  "}platformKey: <span className="code-string">'my-crm-system'</span>,<br/>
{"  "}bindApi: <span className="code-string">'/api/aethercore/bind'</span>, <span className="code-comment">{"// 后端代理，保障安全"}</span><br/>
{"  "}context: {"{ user_id: currentUser.id }"},<br/>
{"  "}hostTools: [<br/>
{"    {"}<br/>
{"      "}name: <span className="code-string">'query_customer'</span>,<br/>
{"      "}description: <span className="code-string">'查询客户详细信息'</span>,<br/>
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
          <h2>即刻释放 AI 生产力</h2>
          <p>无需从零构建繁琐的 AI 基础设施。现在进入工作台，感受下一代 Agent 的真实执行力。</p>
          <div className="hero-actions" style={{ marginTop: '32px' }}>
            <button type="button" className="btn-primary-epic" onClick={onOpenChat}>
              {authed ? "回到 AI 工作台" : "登录并开始体验"}
            </button>
            <button type="button" className="btn-secondary-epic" onClick={onOpenPlatforms}>
              进入管理后台
            </button>
          </div>
        </div>
      </section>
    </main>
  );
}
