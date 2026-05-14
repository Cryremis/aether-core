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
type DeployScope = "same_origin" | "cross_origin";

const VISIBLE_IDENTITIES: IdentityScenario[] = ["authenticated_user", "browser_guest"];

function getSnippetDeployScope(snippetId: string): DeployScope | null {
  if (snippetId.endsWith("_same_origin")) return "same_origin";
  if (snippetId.endsWith("_cross_origin")) return "cross_origin";
  return null;
}

function CollapsibleSnippetCard({
  snippetId,
  title,
  summary,
  content,
  language,
  renderHighlightedSnippet,
  onCopy,
  defaultExpanded = false,
}: {
  snippetId: string;
  title: string;
  summary?: string;
  content: string;
  language: string;
  renderHighlightedSnippet: (snippet: string | undefined) => Array<string | React.ReactNode> | null;
  onCopy: (value: string) => void;
  defaultExpanded?: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  return (
    <div className="guide-snippet-card">
      <div className="guide-snippet-card__header">
        <div className="guide-snippet-card__meta">
          <strong>{title}</strong>
          {summary ? <span>{summary}</span> : null}
        </div>
        <div className="guide-snippet-card__actions">
          <button type="button" className="action-button small" onClick={() => onCopy(content)}>
            复制
          </button>
          <button
            type="button"
            className="action-button small primary"
            onClick={() => setExpanded((prev) => !prev)}
          >
            {expanded ? "收起" : "展开"}
          </button>
        </div>
      </div>
      <div className={`guide-code-shell${expanded ? " is-expanded" : ""}`}>
        <pre
          className={`guide-code-block${expanded ? " is-expanded" : ""}`}
          data-language={language}
          data-snippet-id={snippetId}
        >
          <code>{renderHighlightedSnippet(content)}</code>
        </pre>
        {!expanded ? <div className="guide-code-fade" aria-hidden="true" /> : null}
      </div>
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
    visuals: ["有登录体系", "稳定用户 ID", "完整权限控制"],
    description: "适合已有用户系统的平台。",
  },
  browser_guest: {
    title: "匿名访客",
    visuals: ["无登录可用", "浏览器级续接", "低风险优先"],
    description: "适合没有登录体系但有宿主后端的平台。",
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
  const [selectedDeployScope, setSelectedDeployScope] = useState<DeployScope>("same_origin");
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
        (item) => item.access_stage === selectedStage && item.identity_scenario === selectedIdentity,
      ) ?? null,
    [modes, selectedIdentity, selectedStage],
  );

  const frontendSnippets = useMemo(
    () => (activeMode?.snippets ?? []).filter((item) => item.snippet_id.includes("frontend")),
    [activeMode],
  );

  const frontendSnippet = useMemo(() => {
    if (!frontendSnippets.length) return null;
    const matched = frontendSnippets.find((item) => getSnippetDeployScope(item.snippet_id) === selectedDeployScope);
    return matched ?? frontendSnippets[0];
  }, [frontendSnippets, selectedDeployScope]);

  useEffect(() => {
    const supportsSelectedScope = frontendSnippets.some(
      (item) => getSnippetDeployScope(item.snippet_id) === selectedDeployScope,
    );
    if (supportsSelectedScope) return;
    const fallback = frontendSnippets.find((item) => getSnippetDeployScope(item.snippet_id) === "same_origin")
      ? "same_origin"
      : "cross_origin";
    setSelectedDeployScope(fallback);
  }, [frontendSnippets, selectedDeployScope]);

  const backendCodeSnippets = useMemo(
    () =>
      (activeMode?.snippets ?? []).filter(
        (item) =>
          !item.snippet_id.includes("frontend") &&
          item.snippet_id !== "backend_env" &&
          item.snippet_id !== "host_tools_reference",
      ),
    [activeMode],
  );

  const optionalSnippets = useMemo(
    () => (activeMode?.snippets ?? []).filter((item) => item.snippet_id === "host_tools_reference"),
    [activeMode],
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

  if (!(integrationGuide || integrationGuideBusy || integrationGuideError)) return null;

  return (
    <div className="guide-modal-backdrop" onClick={onClose}>
      <div className="guide-modal" onClick={(e) => e.stopPropagation()}>
        <div className="guide-modal__header">
          <div>
            <h4>接入教程</h4>
            <p>{integrationGuidePlatformName || integrationGuide?.display_name || "平台接入说明"}</p>
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
                <section className="guide-mode-card guide-mode-card--recommended">
                  <div className="guide-mode-card__header">
                    <div>
                      <div className="guide-mode-card__eyebrow">Production</div>
                      <h5>{activeMode?.title}</h5>
                      <p>{activeMode?.summary}</p>
                    </div>
                  </div>

                  <div className="guide-deploy-switcher">
                    <button
                      type="button"
                      className={`guide-template-switcher__item${selectedDeployScope === "same_origin" ? " is-active" : ""}`}
                      onClick={() => setSelectedDeployScope("same_origin")}
                    >
                      同域部署
                    </button>
                    <button
                      type="button"
                      className={`guide-template-switcher__item${selectedDeployScope === "cross_origin" ? " is-active" : ""}`}
                      onClick={() => setSelectedDeployScope("cross_origin")}
                    >
                      跨域部署
                    </button>
                  </div>

                  {frontendSnippet ? (
                    <CollapsibleSnippetCard
                      snippetId={frontendSnippet.snippet_id}
                      title={frontendSnippet.title}
                      summary={frontendSnippet.summary}
                      content={frontendSnippet.content}
                      language={frontendSnippet.language}
                      renderHighlightedSnippet={renderHighlightedSnippet}
                      onCopy={onCopy}
                    />
                  ) : null}

                  {backendCodeSnippets.length ? (
                    <div className="guide-section">
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
                        <CollapsibleSnippetCard
                          snippetId={selectedBackendSnippet.snippet_id}
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

                  {optionalSnippets.length ? (
                    <div className="guide-section">
                      {optionalSnippets.map((snippet) => (
                        <CollapsibleSnippetCard
                          key={snippet.snippet_id}
                          snippetId={snippet.snippet_id}
                          title={snippet.title}
                          summary={snippet.summary}
                          content={snippet.content}
                          language={snippet.language}
                          renderHighlightedSnippet={renderHighlightedSnippet}
                          onCopy={onCopy}
                        />
                      ))}
                    </div>
                  ) : null}
                </section>
              </div>

              <aside className="guide-side-panel">
                <div className="guide-side-panel__item">
                  <strong>官方前端加载器</strong>
                  <p>{integrationGuide.frontend_script_url}</p>
                </div>

                {integrationGuide.placeholders.length ? (
                  <div className="guide-side-panel__item">
                    <strong>需要替换</strong>
                    <div className="guide-note-list">
                      {integrationGuide.placeholders.map((item) => (
                        <div key={item.key} className="guide-note-item">
                          <strong>{item.value}</strong>
                          <span>{item.description || item.label}</span>
                        </div>
                      ))}
                    </div>
                    <div className="guide-callout">
                      <strong>用户解析示例</strong>
                      <p>FastAPI: <code>request.state.user</code></p>
                      <p>Express: <code>req.user</code></p>
                    </div>
                  </div>
                ) : null}

                {activeMode?.steps?.length ? (
                  <div className="guide-side-panel__item">
                    <strong>接入步骤</strong>
                    <div className="guide-note-list">
                      {activeMode.steps.map((item, index) => (
                        <div key={`${index}-${item}`} className="guide-note-item">
                          <span>{index + 1}. {item}</span>
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

                {activeMode?.warnings?.length ? (
                  <div className="guide-side-panel__item">
                    <strong>风险提醒</strong>
                    <div className="guide-note-list">
                      {activeMode.warnings.map((item) => (
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
