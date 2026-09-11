
// frontend/src/components/AdminPanel.tsx
import { FormEvent, useEffect, useMemo, useState } from "react";

import { createPlatform, getPlatformIntegrationGuide, listPlatforms, PlatformIntegrationGuide } from "../api/client";
import { AdminForms } from "./admin/AdminForms";
import { BaselineMoveModal } from "./admin/BaselineMoveModal";
import { BaselineContextMenu } from "./admin/BaselineContextMenu";
import { BaselineManager } from "./admin/BaselineManager";
import { IntegrationGuideModal } from "./admin/IntegrationGuideModal";
import { PlatformList } from "./admin/PlatformList";
import { PlatformLlmPanel } from "./admin/PlatformLlmPanel";
import { PlatformPromptPanel } from "./admin/PlatformPromptPanel";
import { PlatformSandboxProxyPanel } from "./admin/PlatformSandboxProxyPanel";
import { PlatformRuntimeImagePanel } from "./admin/PlatformRuntimeImagePanel";
import { SkillUploadModal } from "./admin/SkillUploadModal";
import { useAppPreferences } from "../i18n";
import {
  usePlatformBaseline, usePlatformLlmConfig, usePlatformPromptConfig,
  usePlatformRuntimeImage, usePlatformSandboxProxy,
} from "../pages/platform/hooks";
import type { PlatformItem } from "./admin/types";

type AdminPanelProps = {
  role: string;
};

type PlatformSettingsView = "image" | "proxy" | "prompt" | "llm" | "baseline";

function PlatformWorkbenchSkeleton() {
  return (
    <div className="platform-workbench-skeleton">
      <div className="platform-workbench-skeleton__panel">
        <span className="platform-workbench-skeleton__line platform-workbench-skeleton__line--lg" />
        <span className="platform-workbench-skeleton__line platform-workbench-skeleton__line--md" />
        <div className="platform-workbench-skeleton__tabs">
          <span className="platform-workbench-skeleton__pill" />
          <span className="platform-workbench-skeleton__pill" />
          <span className="platform-workbench-skeleton__pill" />
          <span className="platform-workbench-skeleton__pill" />
          <span className="platform-workbench-skeleton__pill" />
        </div>
        <div className="platform-workbench-skeleton__card">
          <span className="platform-workbench-skeleton__line platform-workbench-skeleton__line--sm" />
          <span className="platform-workbench-skeleton__line platform-workbench-skeleton__line--md" />
          <span className="platform-workbench-skeleton__line platform-workbench-skeleton__line--md" />
        </div>
      </div>
    </div>
  );
}

export function AdminPanel({ role }: AdminPanelProps) {
  const { t } = useAppPreferences();
  const [platforms, setPlatforms] = useState<PlatformItem[]>([]);
  const [activePlatformId, setActivePlatformId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [platformKey, setPlatformKey] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [settingsView, setSettingsView] = useState<PlatformSettingsView>("image");

  // 接入教程弹窗
  const [integrationGuide, setIntegrationGuide] = useState<PlatformIntegrationGuide | null>(null);
  const [integrationGuideError, setIntegrationGuideError] = useState("");
  const [integrationGuideBusy, setIntegrationGuideBusy] = useState(false);
  const [integrationGuidePlatformName, setIntegrationGuidePlatformName] = useState("");

  const existingPlatformKeys = useMemo(() => new Set(platforms.map((item) => item.platform_key)), [platforms]);

  const loadData = async () => {
    setError("");
    try {
      const platformResult = await listPlatforms();
      setPlatforms((platformResult.data ?? []) as PlatformItem[]);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "加载管理数据失败");
    }
  };

  useEffect(() => { void loadData(); }, [role]);

  // 平台设置数据：按当前激活的设置视图懒加载，切换平台自动重置
  const llm = usePlatformLlmConfig(activePlatformId, settingsView === "llm");
  const prompt = usePlatformPromptConfig(activePlatformId, settingsView === "prompt");
  const image = usePlatformRuntimeImage(activePlatformId, settingsView === "image", loadData);
  const proxy = usePlatformSandboxProxy(activePlatformId, settingsView === "proxy", loadData);
  const baseline = usePlatformBaseline(activePlatformId);

  useEffect(() => {
    if (!platforms.length) {
      setActivePlatformId(null);
      return;
    }
    const preferred = platforms.find((item) => item.platform_key === "standalone") ?? platforms[0];
    const targetId = activePlatformId && platforms.some((item) => item.platform_id === activePlatformId) ? activePlatformId : preferred.platform_id;
    setActivePlatformId(targetId);
  }, [platforms]);

  const handleCreatePlatform = async (e: FormEvent) => {
    e.preventDefault();
    const normalizedPlatformKey = platformKey.trim().toLowerCase();
    if (existingPlatformKeys.has(normalizedPlatformKey)) { setError(`platform_key "${normalizedPlatformKey}" 已存在，请更换`); return; }
    if (normalizedPlatformKey === "standalone") { setError('platform_key "standalone" 为系统内置保留平台'); return; }
    try { setError(""); await createPlatform({ platform_key: normalizedPlatformKey, display_name: displayName.trim(), description: description.trim() }); setPlatformKey(""); setDisplayName(""); setDescription(""); await loadData(); } catch (err) { setError(err instanceof Error ? err.message : "平台注册失败"); }
  };

  const handleOpenIntegrationGuide = async (platform: PlatformItem) => {
    try {
      setIntegrationGuideBusy(true);
      setIntegrationGuideError("");
      setIntegrationGuidePlatformName(platform.display_name);
      const result = await getPlatformIntegrationGuide(platform.platform_id);
      setIntegrationGuide((result.data ?? null) as PlatformIntegrationGuide | null);
    } catch (err) {
      setIntegrationGuideError(err instanceof Error ? err.message : "加载接入教程失败");
      setIntegrationGuide(null);
    } finally {
      setIntegrationGuideBusy(false);
    }
  };

  const closeIntegrationGuide = () => {
    setIntegrationGuide(null);
    setIntegrationGuideError("");
    setIntegrationGuideBusy(false);
    setIntegrationGuidePlatformName("");
  };

  const copyText = async (value: string) => {
    await navigator.clipboard.writeText(value);
  };

  const renderHighlightedSnippet = (snippet: string | undefined) => {
    if (!snippet) return null;
    const parts = snippet.split(/(\{\{[A-Z0-9_]+\}\})/g);
    return parts.map((part, index) =>
      /^\{\{[A-Z0-9_]+\}\}$/.test(part) ? (
        <span key={`placeholder-${index}`} className="guide-placeholder">
          {part}
        </span>
      ) : part,
    );
  };

  // 面包屑解析
  const breadcrumbs = useMemo(() => {
    if (!baseline.currentDirectory) return [];
    const parts = baseline.currentDirectory.split("/");
    return parts.map((part, index) => ({
      name: part,
      path: parts.slice(0, index + 1).join("/"),
    }));
  }, [baseline.currentDirectory]);

  // 当前目录内容 (仅限一层)
  const currentDirectoryChildren = useMemo(() => {
    const prefix = baseline.currentDirectory ? `${baseline.currentDirectory}/` : "";
    return baseline.entries.filter((item) => {
      if (!baseline.currentDirectory) {
        return !item.relative_path.includes("/");
      }
      if (item.relative_path === baseline.currentDirectory) return false;
      if (!item.relative_path.startsWith(prefix)) return false;
      const rest = item.relative_path.slice(prefix.length);
      return !rest.includes("/");
    }).sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === "directory" ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  }, [baseline.entries, baseline.currentDirectory]);

  const activePlatform = platforms.find((item) => item.platform_id === activePlatformId) ?? null;

  const settingsTabs: Array<{ key: PlatformSettingsView; title: string; description: string }> = [
    { key: "image", title: t("admin.tab.image"), description: t("admin.tab.imageDesc") },
    { key: "proxy", title: t("admin.tab.proxy"), description: t("admin.tab.proxyDesc") },
    { key: "prompt", title: t("admin.tab.prompt"), description: t("admin.tab.promptDesc") },
    { key: "llm", title: t("admin.tab.llm"), description: t("admin.tab.llmDesc") },
    { key: "baseline", title: t("admin.tab.baseline"), description: t("admin.tab.baselineDesc") },
  ];

  const settingsLoaded: Record<PlatformSettingsView, boolean> = {
    image: image.loaded, proxy: proxy.loaded, prompt: prompt.loaded, llm: llm.loaded, baseline: true,
  };
  const isSettingsPanelReady = settingsLoaded[settingsView];
  const shouldShowWorkbenchSkeleton = Boolean(activePlatformId) && !activePlatform;

  return (
    <section className="admin-panel">
      {error ? <div className="admin-panel__error epic-error">{error}</div> : null}

      {/* 平台注册表单（仅系统管理员） */}
      {role === "system_admin" ? (
        <div className="epic-glass admin-panel__create-card stagger-3">
          <AdminForms
            platformKey={platformKey}
            displayName={displayName}
            description={description}
            onPlatformKeyChange={setPlatformKey}
            onDisplayNameChange={setDisplayName}
            onDescriptionChange={setDescription}
            onCreatePlatform={handleCreatePlatform}
          />
        </div>
      ) : null}

      <div className="epic-glass stagger-4">
        <PlatformList
          platforms={platforms}
          activePlatformId={activePlatformId}
          onSelect={setActivePlatformId}
          onOpenGuide={(platform) => void handleOpenIntegrationGuide(platform)}
        />
      </div>

      {/* ================= 平台工作台 ================= */}
      {shouldShowWorkbenchSkeleton ? <PlatformWorkbenchSkeleton /> : null}

      {activePlatform ? (
        <div className="epic-glass epic-bento-card epic-bento-card--workbench stagger-4">
          <div className="platform-settings-workbench">
            <div className="platform-settings-workbench__header">
              <div className="manager-header__info">
                <h4>{t("admin.platformWorkbench.title")}</h4>
                <p>{t("admin.platformWorkbench.copy")}</p>
              </div>
            </div>

            <div className="platform-settings-tabs" role="tablist" aria-label={t("admin.platformWorkbench.tabsLabel")}>
              {settingsTabs.map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  role="tab"
                  aria-selected={settingsView === tab.key}
                  className={`platform-settings-tab${settingsView === tab.key ? " is-active" : ""}`}
                  onClick={() => setSettingsView(tab.key)}
                >
                  <span>{tab.title}</span>
                  <small>{tab.description}</small>
                </button>
              ))}
            </div>

            <div className={`platform-settings-workbench__panel${settingsView === "baseline" ? " platform-settings-workbench__panel--baseline" : ""}`}>
              {!isSettingsPanelReady ? <div className="platform-settings-loading">{t("common.loading")}</div> : null}

              {settingsView === "image" && isSettingsPanelReady ? (
                <PlatformRuntimeImagePanel
                  platformName={activePlatform.display_name}
                  runtimeImageForm={image.form}
                  runtimeImageError={image.error}
                  runtimeImageBusy={image.busy}
                  onChange={image.setForm}
                  onSave={() => void image.save()}
                  onReset={() => void image.reset()}
                  onUpload={(file) => void image.upload(file)}
                />
              ) : null}

              {settingsView === "proxy" && isSettingsPanelReady ? (
                <PlatformSandboxProxyPanel
                  sandboxProxyForm={proxy.form}
                  sandboxProxyError={proxy.error}
                  sandboxProxyBusy={proxy.busy}
                  onChange={proxy.setForm}
                  onSave={() => void proxy.save()}
                  onReset={() => void proxy.reset()}
                />
              ) : null}

              {settingsView === "prompt" && isSettingsPanelReady ? (
                <PlatformPromptPanel
                  promptForm={prompt.form}
                  promptError={prompt.error}
                  promptBusy={prompt.busy}
                  onChange={prompt.setForm}
                  onSave={() => void prompt.save()}
                  onReset={() => void prompt.reset()}
                />
              ) : null}

              {settingsView === "llm" && isSettingsPanelReady ? (
                <PlatformLlmPanel
                  platformLlmForm={llm.form}
                  platformLlmError={llm.error}
                  platformLlmBusy={llm.busy}
                  showPlatformLlmAdvanced={llm.showAdvanced}
                  onToggleAdvanced={llm.setShowAdvanced}
                  onChange={llm.setForm}
                  onSave={() => void llm.save()}
                  onReset={() => void llm.reset()}
                />
              ) : null}

              {settingsView === "baseline" ? (
                <BaselineManager
                  showMcp
                  activePlatform={activePlatform}
                  baselineError={baseline.error}
                  fileManagerRef={baseline.fileManagerRef}
                  breadcrumbs={breadcrumbs}
                  currentDirectoryChildren={currentDirectoryChildren}
                  currentBaselineDirectory={baseline.currentDirectory}
                  selectedBaselinePath={baseline.selectedPath}
                  selectedBaselineContent={baseline.selectedContent}
                  selectedBaselineMediaType={baseline.selectedMediaType}
                  selectedBaselineTruncated={baseline.selectedTruncated}
                  baselineDirty={baseline.dirty}
                  onGoHome={() => { baseline.setCurrentDirectory(""); baseline.setSelectedPath(""); }}
                  onGoBreadcrumb={(path) => { baseline.setCurrentDirectory(path); baseline.setSelectedPath(""); }}
                  onCreateDirectory={() => void baseline.handleCreateDirectory()}
                  onCreateFile={() => void baseline.handleCreateFile()}
                  onUploadFile={(file) => void baseline.handleUploadFile(file)}
                  onUploadFolder={(files) => void baseline.handleUploadFolder(files)}
                  onOpenSkillUpload={() => { baseline.setSkillUploadError(""); baseline.setSkillUploadVisible(true); }}
                  onSelectFile={(item) => void baseline.handleSelectFile(item)}
                  onDoubleClickItem={baseline.handleDoubleClickItem}
                  onContextMenu={baseline.handleContextMenu}
                  onContentChange={baseline.handleContentChange}
                  onSaveText={() => void baseline.handleSaveText()}
                  onClosePreview={() => { baseline.setSelectedPath(""); }}
                />
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      <BaselineContextMenu
        contextMenu={baseline.contextMenu}
        onOpenDirectory={() => { baseline.handleDoubleClickItem(baseline.contextMenu.item!); }}
        onEditFile={() => { void baseline.handleSelectFile(baseline.contextMenu.item!); }}
        onDownloadFile={() => { void baseline.handleDownload(baseline.contextMenu.item!.relative_path, baseline.contextMenu.item!.name); }}
        onMove={() => { baseline.openMoveModal(baseline.contextMenu.item!.relative_path, "move"); }}
        onRename={() => { baseline.openMoveModal(baseline.contextMenu.item!.relative_path, "rename"); }}
        onDelete={() => { void baseline.handleDelete(baseline.contextMenu.item!.relative_path); }}
      />
      <IntegrationGuideModal
        integrationGuide={integrationGuide}
        integrationGuideBusy={integrationGuideBusy}
        integrationGuideError={integrationGuideError}
        integrationGuidePlatformName={integrationGuidePlatformName}
        renderHighlightedSnippet={renderHighlightedSnippet}
        onCopy={(value) => void copyText(value)}
        onClose={closeIntegrationGuide}
      />
      <SkillUploadModal
        visible={baseline.skillUploadVisible}
        busy={baseline.skillUploadBusy}
        error={baseline.skillUploadError}
        platformName={activePlatform?.display_name ?? ""}
        onClose={() => baseline.setSkillUploadVisible(false)}
        onUpload={(file) => baseline.handleSkillUpload(file)}
        onUploadFolder={(files) => baseline.handleSkillFolderUpload(files)}
      />
      <BaselineMoveModal
        visible={Boolean(baseline.moveState.sourcePath)}
        busy={baseline.moveBusy}
        error={baseline.moveError}
        mode={baseline.moveState.mode}
        sourcePath={baseline.moveState.sourcePath}
        targetPath={baseline.moveState.targetPath}
        onTargetPathChange={(value) => baseline.setMoveState({ ...baseline.moveState, targetPath: value })}
        onClose={baseline.closeMoveModal}
        onSubmit={() => void baseline.handleMove()}
      />
    </section>
  );
}
