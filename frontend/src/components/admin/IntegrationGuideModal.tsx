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

type GuidePathCard = {
  id: "quick_frontend" | "production";
  title: string;
  badge: string;
  summary: string;
  visuals: string[];
  modeIds: string[];
};

type GuideMode = NonNullable<PlatformIntegrationGuide["modes"]>[number];

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

function buildPathCards(integrationGuide: PlatformIntegrationGuide | null): GuidePathCard[] {
  const modes = integrationGuide?.modes ?? [];
  const quickModeIds = modes
    .filter((item) => item.mode_id.includes("quick") || item.mode_id.includes("guest") || item.mode_id.includes("demo"))
    .map((item) => item.mode_id);
  const productionModeIds = modes
    .filter((item) => !quickModeIds.includes(item.mode_id))
    .map((item) => item.mode_id);

  return [
    {
      id: "quick_frontend",
      title: "纯前端快速接入验证",
      badge: "PoC",
      summary: "最快验证浮球、抽屉和对话工作台，适合先看体验，不急着补后端代理。",
      visuals: ["无宿主后端", "最快跑通", "仅用于验证"],
      modeIds: quickModeIds,
    },
    {
      id: "production",
      title: "前后端生产场景接入",
      badge: "Production",
      summary: "宿主后端保管密钥并代理 bind，适合真实用户、稳定会话和后续能力扩展。",
      visuals: ["后端代理", "安全上线", "可扩展 tools"],
      modeIds: productionModeIds,
    },
  ].filter((item) => item.modeIds.length > 0);
}

function getPreferredMode(modes: GuideMode[], guide: PlatformIntegrationGuide | null): GuideMode | null {
  if (!modes.length) return null;
  return (
    modes.find((item) => item.mode_id === guide?.recommended_mode_id)
    ?? modes.find((item) => item.recommended)
    ?? modes[0]
  );
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
  const pathCards = useMemo(() => buildPathCards(integrationGuide), [integrationGuide]);
  const [selectedPathId, setSelectedPathId] = useState<GuidePathCard["id"] | "">("");
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>("");

  useEffect(() => {
    setSelectedPathId(pathCards[0]?.id ?? "");
  }, [pathCards]);

  const selectedPath = useMemo(
    () => pathCards.find((item) => item.id === selectedPathId) ?? pathCards[0] ?? null,
    [pathCards, selectedPathId],
  );

  const pathModes = useMemo(() => {
    if (!integrationGuide || !selectedPath) return [];
    return integrationGuide.modes.filter((item) => selectedPath.modeIds.includes(item.mode_id));
  }, [integrationGuide, selectedPath]);

  const activeMode = useMemo(
    () => getPreferredMode(pathModes, integrationGuide),
    [pathModes, integrationGuide],
  );

  const frontendSnippet = activeMode?.snippets.find((item) => item.snippet_id.includes("frontend")) ?? activeMode?.snippets[0] ?? null;
  const backendSnippets = (activeMode?.snippets ?? []).filter((item) => !item.snippet_id.includes("frontend"));

  useEffect(() => {
    if (!backendSnippets.length) {
      setSelectedTemplateId("");
      return;
    }
    setSelectedTemplateId((prev) => {
      if (prev && backendSnippets.some((item) => item.snippet_id === prev)) return prev;
      return backendSnippets[0]?.snippet_id ?? "";
    });
  }, [backendSnippets]);

  const selectedBackendSnippet = backendSnippets.find((item) => item.snippet_id === selectedTemplateId) ?? backendSnippets[0] ?? null;

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
              ：只选一种路径，快速验证就走纯前端，正式上线就走前后端生产接入。
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
            {pathCards.length ? (
              <section className="guide-path-grid">
                {pathCards.map((card) => (
                  <button
                    key={card.id}
                    type="button"
                    className={`guide-path-card${card.id === selectedPath?.id ? " is-active" : ""}`}
                    onClick={() => setSelectedPathId(card.id)}
                  >
                    <div className="guide-path-card__top">
                      <span className="guide-path-card__badge">{card.badge}</span>
                      <strong>{card.title}</strong>
                    </div>
                    <p>{card.summary}</p>
                    <div className="guide-path-card__visuals">
                      {card.visuals.map((item) => (
                        <span key={item} className="guide-path-pill">{item}</span>
                      ))}
                    </div>
                  </button>
                ))}
              </section>
            ) : null}

            <section className="guide-split-layout">
              <div className="guide-split-layout__code">
                {activeMode ? (
                  <section className={`guide-mode-card${selectedPath?.id === "production" ? " guide-mode-card--recommended" : ""}`}>
                    <div className="guide-mode-card__header">
                      <div>
                        <div className="guide-mode-card__eyebrow">{selectedPath?.badge ?? "Guide"}</div>
                        <h5>{selectedPath?.title ?? activeMode.title}</h5>
                        <p>{activeMode.summary}</p>
                      </div>
                    </div>

                    <div className="guide-kv-grid">
                      <div className="guide-note-item">
                        <strong>适用场景</strong>
                        <span>{activeMode.use_when || "适合当前接入路径。"}</span>
                      </div>
                      <div className="guide-note-item">
                        <strong>后端要求</strong>
                        <span>{activeMode.backend_requirement || "按当前模式要求接入。"}</span>
                      </div>
                      <div className="guide-note-item">
                        <strong>身份要求</strong>
                        <span>{activeMode.identity_requirement || "按当前模式决定是否需要稳定用户身份。"}</span>
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

                    {selectedPath?.id === "production" && backendSnippets.length ? (
                      <div className="guide-section">
                        <div className="guide-section__head">
                          <div>
                            <h5>后端模板</h5>
                            <span className="guide-section__label">只展示你当前选中的后端模板，不再全部堆出来。</span>
                          </div>
                        </div>
                        <div className="guide-template-switcher">
                          {backendSnippets.map((snippet) => (
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
