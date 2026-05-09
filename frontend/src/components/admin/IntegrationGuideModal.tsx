import React, { useEffect, useMemo, useState } from "react";
import type { PlatformIntegrationGuide } from "../../api/client";

type IntegrationGuideModalProps = {
  integrationGuide: PlatformIntegrationGuide | null;
  integrationGuideBusy: boolean;
  integrationGuideError: string;
  integrationGuidePlatformName: string;
  renderHighlightedSnippet: (snippet: string | undefined) => Array<string | React.ReactNode> | null;
  onCopy: (value: string) => void;
  onClose: () => void;
};

type GuideMode = NonNullable<PlatformIntegrationGuide["modes"]>[number];
type AccessStage = "quick" | "production";
type IdentityScenario = "authenticated_user" | "browser_guest" | "ephemeral";

const VISIBLE_IDENTITIES: IdentityScenario[] = ["authenticated_user", "browser_guest"];

function SnippetCard({
  title,
  summary,
  content,
  language,
  renderHighlightedSnippet,
  onCopy,
}: {
  title: string;
  summary?: string;
  content: string;
  language: string;
  renderHighlightedSnippet: (snippet: string | undefined) => Array<string | React.ReactNode> | null;
  onCopy: (value: string) => void;
}) {
  return (
    <div className="guide-section">
      <div className="guide-section__head">
        <div>
          <h5>{title}</h5>
          {summary ? <span className="guide-section__label">{summary}</span> : null}
        </div>
        <button type="button" className="action-button small primary" onClick={() => onCopy(content)}>
          复制
        </button>
      </div>
      <pre className="guide-code-block" data-language={language}>
        <code>{renderHighlightedSnippet(content)}</code>
      </pre>
    </div>
  );
}

const STAGE_OPTIONS: Array<{
  id: AccessStage;
  title: string;
  badge: string;
  summary: string;
}> = [
  {
    id: "production",
    title: "生产接入",
    badge: "Production",
    summary: "宿主后端保管密钥并代理 bind，适合正式上线和后续能力扩展。",
  },
];

const IDENTITY_META: Record<IdentityScenario, { title: string; visuals: string[]; description: string }> = {
  authenticated_user: {
    title: "登录用户",
    visuals: ["有登录体系", "稳定用户 ID", "更完整权限控制"],
    description: "适合已有用户系统的平台，强调稳定历史和更完整的宿主能力扩展。",
  },
  browser_guest: {
    title: "匿名访客",
    visuals: ["无登录也可用", "浏览器级续接", "限制高权限"],
    description: "适合没有登录体系但有宿主后端的平台，同一浏览器可复用匿名访客会话。",
  },
  ephemeral: {
    title: "临时访客",
    visuals: ["无身份依赖", "临时会话", "只做验证"],
    description: "适合最快体验验证，不承诺跨会话连续性。",
  },
};

function getAvailableIdentities(modes: GuideMode[], stage: AccessStage): IdentityScenario[] {
  const existing = new Set(
    modes.filter((item) => item.access_stage === stage).map((item) => item.identity_scenario as IdentityScenario),
  );
  return VISIBLE_IDENTITIES.filter((item) => existing.has(item));
}

export function IntegrationGuideModal({
  integrationGuide,
  integrationGuideBusy,
  integrationGuideError,
  integrationGuidePlatformName,
  renderHighlightedSnippet,
  onCopy,
  onClose,
}: IntegrationGuideModalProps) {
  const modes = integrationGuide?.modes ?? [];
  const [selectedStage, setSelectedStage] = useState<AccessStage>("production");
  const [selectedIdentity, setSelectedIdentity] = useState<IdentityScenario>("authenticated_user");
  const [selectedTemplateId, setSelectedTemplateId] = useState("");

  const availableStages = useMemo(
    () => STAGE_OPTIONS.filter((item) => modes.some((mode) => mode.access_stage === item.id)),
    [modes],
  );

  useEffect(() => {
    setSelectedStage((prev) => {
      if (availableStages.some((item) => item.id === prev)) return prev;
      return availableStages[0]?.id ?? "quick";
    });
  }, [availableStages]);

  const availableIdentities = useMemo(
    () => getAvailableIdentities(modes, selectedStage),
    [modes, selectedStage],
  );

  useEffect(() => {
    setSelectedIdentity((prev) => {
      if (availableIdentities.includes(prev)) return prev;
      return availableIdentities[0] ?? "authenticated_user";
    });
  }, [availableIdentities]);

  const activeMode = useMemo(
    () =>
      modes.find(
        (item) =>
          item.access_stage === selectedStage &&
          item.identity_scenario === selectedIdentity,
      ) ?? null,
    [modes, selectedIdentity, selectedStage],
  );

  const frontendSnippet =
    activeMode?.snippets.find((item) => item.snippet_id.includes("frontend")) ?? activeMode?.snippets[0] ?? null;
  const envSnippet =
    (activeMode?.snippets ?? []).find((item) => item.snippet_id === "backend_env") ?? null;
  const backendCodeSnippets = (activeMode?.snippets ?? []).filter(
    (item) => !item.snippet_id.includes("frontend") && item.snippet_id !== "backend_env",
  );

  useEffect(() => {
    if (!backendCodeSnippets.length) {
      setSelectedTemplateId("");
      return;
    }
    setSelectedTemplateId((prev) => {
      if (prev && backendCodeSnippets.some((item) => item.snippet_id === prev)) return prev;
      return backendCodeSnippets[0]?.snippet_id ?? "";
    });
  }, [backendCodeSnippets]);

  const selectedBackendSnippet =
    backendCodeSnippets.find((item) => item.snippet_id === selectedTemplateId) ?? backendCodeSnippets[0] ?? null;

  const fallbackSnippets = integrationGuide
    ? [
        { snippet_id: "legacy-frontend", title: "前端嵌入代码", language: "html", summary: "兼容旧教程字段", content: integrationGuide.snippets.frontend },
        { snippet_id: "legacy-env", title: "后端环境变量示例", language: "dotenv", summary: "兼容旧教程字段", content: integrationGuide.snippets.backend_env },
        { snippet_id: "legacy-fastapi", title: "后端 Bind 示例（FastAPI）", language: "python", summary: "兼容旧教程字段", content: integrationGuide.snippets.backend_fastapi },
      ].filter((item) => item.content)
    : [];

  if (!(integrationGuide || integrationGuideBusy || integrationGuideError)) return null;

  return (
    <div className="guide-modal-backdrop" onClick={onClose}>
      <div className="guide-modal" onClick={(e) => e.stopPropagation()}>
        <div className="guide-modal__header">
          <div>
            <h4>接入方案</h4>
            <p>
              {integrationGuidePlatformName || integrationGuide?.display_name || "平台接入说明"}
              ：当前仅保留生产接入，支持登录用户和匿名访客两种正式场景，只展示当前组合对应的接入内容。
            </p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭接入教程">
            ×
          </button>
        </div>

        {integrationGuideBusy ? <div className="admin-panel__empty">接入教程加载中...</div> : null}
        {integrationGuideError ? <div className="admin-panel__error">{integrationGuideError}</div> : null}

        {integrationGuide ? (
          <div className="guide-modal__body">
            {availableStages.length ? (
              <section className="guide-stage-grid">
                {availableStages.map((stage) => (
                  <button
                    key={stage.id}
                    type="button"
                    className={`guide-stage-card${selectedStage === stage.id ? " is-active" : ""}`}
                    onClick={() => setSelectedStage(stage.id)}
                  >
                    <span className="guide-path-card__badge">{stage.badge}</span>
                    <strong>{stage.title}</strong>
                    <p>{stage.summary}</p>
                  </button>
                ))}
              </section>
            ) : null}

            {availableIdentities.length ? (
              <section className="guide-path-grid">
                {availableIdentities.map((identity) => {
                  const meta = IDENTITY_META[identity];
                  return (
                    <button
                      key={identity}
                      type="button"
                      className={`guide-path-card${selectedIdentity === identity ? " is-active" : ""}`}
                      onClick={() => setSelectedIdentity(identity)}
                    >
                      <div className="guide-path-card__top">
                        <strong>{meta.title}</strong>
                      </div>
                      <p>{meta.description}</p>
                      <div className="guide-path-card__visuals">
                        {meta.visuals.map((item) => (
                          <span key={item} className="guide-path-pill">{item}</span>
                        ))}
                      </div>
                    </button>
                  );
                })}
              </section>
            ) : null}

            <section className="guide-split-layout">
              <div className="guide-split-layout__code">
                {activeMode ? (
                  <section className={`guide-mode-card${selectedStage === "production" ? " guide-mode-card--recommended" : ""}`}>
                    <div className="guide-mode-card__header">
                      <div>
                        <div className="guide-mode-card__eyebrow">
                          {selectedStage === "production" ? "Production" : "Quick"}
                        </div>
                        <h5>{activeMode.title}</h5>
                        <p>{activeMode.summary}</p>
                      </div>
                    </div>

                    <div className="guide-kv-grid">
                      <div className="guide-note-item">
                        <strong>适用场景</strong>
                        <span>{activeMode.use_when || "适合当前接入组合。"}</span>
                      </div>
                      <div className="guide-note-item">
                        <strong>后端要求</strong>
                        <span>{activeMode.backend_requirement || "按当前模式要求接入。"}</span>
                      </div>
                      <div className="guide-note-item">
                        <strong>身份要求</strong>
                        <span>{activeMode.identity_requirement || "按当前模式决定身份来源。"}</span>
                      </div>
                    </div>

                    {activeMode.capabilities.length ? (
                      <div className="guide-chip-row">
                        {activeMode.capabilities.map((item) => (
                          <span key={item} className="guide-chip">{item}</span>
                        ))}
                      </div>
                    ) : null}

                    {activeMode.steps.length ? (
                      <div className="guide-list-block">
                        <h6>实施步骤</h6>
                        <ol className="guide-ordered-list">
                          {activeMode.steps.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ol>
                      </div>
                    ) : null}

                    {activeMode.warnings.length ? (
                      <div className="guide-warning-list">
                        {activeMode.warnings.map((item) => (
                          <div key={item} className="guide-warning-item">
                            <strong>注意</strong>
                            <span>{item}</span>
                          </div>
                        ))}
                      </div>
                    ) : null}

                    {frontendSnippet ? (
                      <SnippetCard
                        title={frontendSnippet.title}
                        summary={frontendSnippet.summary}
                        content={frontendSnippet.content}
                        language={frontendSnippet.language}
                        renderHighlightedSnippet={renderHighlightedSnippet}
                        onCopy={onCopy}
                      />
                    ) : null}

                    {selectedStage === "production" && envSnippet ? (
                      <SnippetCard
                        title={envSnippet.title}
                        summary={envSnippet.summary}
                        content={envSnippet.content}
                        language={envSnippet.language}
                        renderHighlightedSnippet={renderHighlightedSnippet}
                        onCopy={onCopy}
                      />
                    ) : null}

                    {selectedStage === "production" && backendCodeSnippets.length ? (
                      <div className="guide-section">
                        <div className="guide-section__head">
                          <div>
                            <h5>后端模板</h5>
                            <span className="guide-section__label">环境变量与后端实现已拆开展示，这里只保留后端代码模板。</span>
                          </div>
                        </div>
                        <div className="guide-template-switcher">
                          {backendCodeSnippets.map((snippet) => (
                            <button
                              key={snippet.snippet_id}
                              type="button"
                              className={`guide-template-switcher__item${snippet.snippet_id === selectedTemplateId ? " is-active" : ""}`}
                              onClick={() => setSelectedTemplateId(snippet.snippet_id)}
                            >
                              {snippet.title}
                            </button>
                          ))}
                        </div>
                        {selectedBackendSnippet ? (
                          <SnippetCard
                            title={selectedBackendSnippet.title}
                            summary={selectedBackendSnippet.summary}
                            content={selectedBackendSnippet.content}
                            language={selectedBackendSnippet.language}
                            renderHighlightedSnippet={renderHighlightedSnippet}
                            onCopy={onCopy}
                          />
                        ) : null}
                      </div>
                    ) : null}
                  </section>
                ) : null}

                {!integrationGuide.modes?.length && fallbackSnippets.length ? (
                  <section className="guide-section">
                    <h5>兼容模式</h5>
                    {fallbackSnippets.map((snippet) => (
                      <SnippetCard
                        key={snippet.snippet_id}
                        title={snippet.title}
                        summary={snippet.summary}
                        content={snippet.content}
                        language={snippet.language}
                        renderHighlightedSnippet={renderHighlightedSnippet}
                        onCopy={onCopy}
                      />
                    ))}
                  </section>
                ) : null}
              </div>

              <aside className="guide-side-panel">
                <div className="guide-side-panel__item">
                  <strong>官方前端加载器</strong>
                  <p>{integrationGuide.frontend_script_url}</p>
                </div>

                {integrationGuide.prerequisites.length ? (
                  <div className="guide-side-panel__item">
                    <strong>接入前准备</strong>
                    <div className="guide-note-list">
                      {integrationGuide.prerequisites.map((item) => (
                        <div key={item} className="guide-note-item">
                          <span>{item}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}

                {integrationGuide.placeholders.length ? (
                  <div className="guide-side-panel__item">
                    <strong>需要替换的占位符</strong>
                    <div className="guide-note-list">
                      {integrationGuide.placeholders.map((item) => (
                        <div key={item.key} className="guide-note-item">
                          <strong>{item.value}</strong>
                          <span>{item.description || item.label}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}

                {integrationGuide.notes.length ? (
                  <div className="guide-side-panel__item">
                    <strong>接入建议</strong>
                    <div className="guide-note-list">
                      {integrationGuide.notes.map((item) => (
                        <div key={item} className="guide-note-item">
                          <span>{item}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </aside>
            </section>
          </div>
        ) : null}
      </div>
    </div>
  );
}
