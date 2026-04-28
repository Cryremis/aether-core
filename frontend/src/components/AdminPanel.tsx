
// frontend/src/components/AdminPanel.tsx
import { FormEvent, useEffect, useMemo, useState, useRef } from "react";

import {
  createPlatformBaselineDirectory,
  createPlatform,
  deletePlatformLlmConfig,
  deletePlatformPromptConfig,
  deletePlatformBaselineFile,
  downloadPlatformBaselineFile,
  getPlatformBaseline,
  getPlatformBaselineFileContent,
  getPlatformIntegrationGuide,
  getPlatformLlmConfig,
  getPlatformPromptConfig,
  listPlatforms,
  movePlatformBaselinePath,
  PlatformIntegrationGuide,
  savePlatformBaselineTextFile,
  updatePlatformPromptConfig,
  updatePlatformLlmConfig,
  uploadPlatformBaselineFile,
  uploadPlatformBaselineSkill,
} from "../api/client";
import { AdminForms } from "./admin/AdminForms";
import { BaselineContextMenu } from "./admin/BaselineContextMenu";
import { BaselineManager } from "./admin/BaselineManager";
import { AdminIcons as Icons } from "./admin/AdminIcons";
import { IntegrationGuideModal } from "./admin/IntegrationGuideModal";
import { PlatformList } from "./admin/PlatformList";
import { PlatformLlmPanel } from "./admin/PlatformLlmPanel";
import { PlatformPromptPanel } from "./admin/PlatformPromptPanel";
import { SkillUploadModal } from "./admin/SkillUploadModal";
import type {
  LlmConfigFormState,
  PlatformBaselineEntryItem,
  PlatformBaselineFileItem,
  PlatformItem,
  PromptConfigFormState,
} from "./admin/types";

type AdminPanelProps = {
  role: string;
};

export function AdminPanel({ role }: AdminPanelProps) {
  const [platforms, setPlatforms] = useState<PlatformItem[]>([]);
  const[activePlatformId, setActivePlatformId] = useState<number | null>(null);
  const [baselineEntries, setBaselineEntries] = useState<PlatformBaselineEntryItem[]>([]);
  const[error, setError] = useState("");
  const[baselineError, setBaselineError] = useState("");
  
  // File Manager State
  const[currentBaselineDirectory, setCurrentBaselineDirectory] = useState(""); // "" 代表根目录 (显示 input/skills/work 等)
  const [selectedBaselinePath, setSelectedBaselinePath] = useState("");
  const [selectedBaselineContent, setSelectedBaselineContent] = useState("");
  const[selectedBaselineMediaType, setSelectedBaselineMediaType] = useState("");
  const[selectedBaselineTruncated, setSelectedBaselineTruncated] = useState(false);
  const [baselineDirty, setBaselineDirty] = useState(false);
  const[showSkillUploadModal, setShowSkillUploadModal] = useState(false);
  const[skillUploadBusy, setSkillUploadBusy] = useState(false);
  const [skillUploadError, setSkillUploadError] = useState("");

  // Context Menu State
  const [contextMenu, setContextMenu] = useState<{ visible: boolean; x: number; y: number; item: PlatformBaselineEntryItem | null }>({ visible: false, x: 0, y: 0, item: null });

  // Form States
  const [platformKey, setPlatformKey] = useState("");
  const [displayName, setDisplayName] = useState("");
  const[description, setDescription] = useState("");
  const [platformLlmForm, setPlatformLlmForm] = useState<LlmConfigFormState>({
    enabled: true,
    base_url: "",
    model: "",
    api_key: "",
    extra_headers_text: "",
    extra_body_text: "",
    has_api_key: false,
    network_enabled: true,
    allowed_domains_text: "",
    blocked_domains_text: "",
    max_search_results: 8,
    fetch_timeout_seconds: 30,
  });
  const[platformLlmError, setPlatformLlmError] = useState("");
  const [platformLlmBusy, setPlatformLlmBusy] = useState(false);
  const [showPlatformLlmAdvanced, setShowPlatformLlmAdvanced] = useState(false);
  const [promptForm, setPromptForm] = useState<PromptConfigFormState>({
    enabled: true,
    system_prompt: "",
  });
  const [promptError, setPromptError] = useState("");
  const [promptBusy, setPromptBusy] = useState(false);
  const [integrationGuide, setIntegrationGuide] = useState<PlatformIntegrationGuide | null>(null);
  const [integrationGuideError, setIntegrationGuideError] = useState("");
  const[integrationGuideBusy, setIntegrationGuideBusy] = useState(false);
  const[integrationGuidePlatformName, setIntegrationGuidePlatformName] = useState("");

  const fileManagerRef = useRef<HTMLDivElement>(null);

  const existingPlatformKeys = useMemo(() => new Set(platforms.map((item) => item.platform_key)), [platforms]);

  // 关闭右键菜单
  useEffect(() => {
    const handleClick = () => setContextMenu((prev) => ({ ...prev, visible: false }));
    document.addEventListener("click", handleClick);
    return () => document.removeEventListener("click", handleClick);
  },[]);

  const loadData = async () => {
    setError("");
    try {
      const platformResult = await listPlatforms();
      setPlatforms((platformResult.data ?? []) as PlatformItem[]);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "加载管理数据失败");
    }
  };

  const loadPlatformBaseline = async (platformId: number) => {
    setBaselineError("");
    try {
      const result = await getPlatformBaseline(platformId);
      const data = (result.data ?? {}) as { files?: PlatformBaselineFileItem[]; entries?: PlatformBaselineEntryItem[]; };
      setBaselineEntries(data.entries ??[]);
      setActivePlatformId(platformId);
    } catch (loadError) {
      setBaselineError(loadError instanceof Error ? loadError.message : "加载平台基线环境失败");
    }
  };

  useEffect(() => { void loadData(); }, [role]);

  useEffect(() => {
    if (!activePlatformId) {
      setPlatformLlmError("");
      setPlatformLlmForm({
        enabled: true,
        base_url: "",
        model: "",
        api_key: "",
        extra_headers_text: "",
        extra_body_text: "",
        has_api_key: false,
        network_enabled: true,
        allowed_domains_text: "",
        blocked_domains_text: "",
        max_search_results: 8,
        fetch_timeout_seconds: 30,
      });
      setShowPlatformLlmAdvanced(false);
      setPromptError("");
      setPromptForm({
        enabled: true,
        system_prompt: "",
      });
      return;
    }
    void (async () => {
      try {
        setPlatformLlmError("");
        const result = await getPlatformLlmConfig(activePlatformId);
        const data = (result.data ?? null) as {
          enabled: boolean;
          base_url: string;
          model: string;
          has_api_key: boolean;
          extra_headers?: Record<string, string>;
          extra_body?: Record<string, unknown>;
          network?: {
            enabled?: boolean;
            allowed_domains?: string[];
            blocked_domains?: string[];
            max_search_results?: number;
            fetch_timeout_seconds?: number;
          };
        } | null;
        setPlatformLlmForm({
          enabled: data?.enabled ?? true,
          base_url: data?.base_url ?? "",
          model: data?.model ?? "",
          api_key: "",
          extra_headers_text: data?.extra_headers && Object.keys(data.extra_headers).length > 0 ? JSON.stringify(data.extra_headers, null, 2) : "",
          extra_body_text: data?.extra_body && Object.keys(data.extra_body).length > 0 ? JSON.stringify(data.extra_body, null, 2) : "",
          has_api_key: Boolean(data?.has_api_key),
          network_enabled: data?.network?.enabled ?? true,
          allowed_domains_text: (data?.network?.allowed_domains ??[]).join("\n"),
          blocked_domains_text: (data?.network?.blocked_domains ??[]).join("\n"),
          max_search_results: data?.network?.max_search_results ?? 8,
          fetch_timeout_seconds: data?.network?.fetch_timeout_seconds ?? 30,
        });
        setShowPlatformLlmAdvanced(
          Boolean(
            (data?.extra_headers && Object.keys(data.extra_headers).length > 0) ||
            (data?.extra_body && Object.keys(data.extra_body).length > 0),
          ),
        );
      } catch (err) {
        setPlatformLlmError(err instanceof Error ? err.message : "加载平台 LLM 配置失败");
      }
    })();
    void (async () => {
      try {
        setPromptError("");
        const result = await getPlatformPromptConfig(activePlatformId);
        const data = (result.data ?? null) as {
          enabled: boolean;
          system_prompt: string;
        } | null;
        setPromptForm({
          enabled: data?.enabled ?? true,
          system_prompt: data?.system_prompt ?? "",
        });
      } catch (err) {
        setPromptError(err instanceof Error ? err.message : "加载平台提示词配置失败");
      }
    })();
  },[activePlatformId]);

  useEffect(() => {
    if (!platforms.length) {
      setActivePlatformId(null);
      setBaselineEntries([]);
      return;
    }
    const preferred = platforms.find((item) => item.platform_key === "standalone") ?? platforms[0];
    const targetId = activePlatformId && platforms.some((item) => item.platform_id === activePlatformId) ? activePlatformId : preferred.platform_id;
    void loadPlatformBaseline(targetId);
  }, [platforms]);

  useEffect(() => {
    if (!selectedBaselinePath) return;
    if (!baselineEntries.some((item) => item.relative_path === selectedBaselinePath && item.kind === "file")) {
      setSelectedBaselinePath("");
      setSelectedBaselineContent("");
      setSelectedBaselineMediaType("");
      setSelectedBaselineTruncated(false);
      setBaselineDirty(false);
    }
  },[baselineEntries, selectedBaselinePath]);

  const handleCreatePlatform = async (e: FormEvent) => { 
    e.preventDefault();
    const normalizedPlatformKey = platformKey.trim().toLowerCase();
    if (existingPlatformKeys.has(normalizedPlatformKey)) { setError(`platform_key "${normalizedPlatformKey}" 已存在，请更换`); return; }
    if (normalizedPlatformKey === "standalone") { setError('platform_key "standalone" 为系统内置保留平台'); return; }
    try { setError(""); await createPlatform({ platform_key: normalizedPlatformKey, display_name: displayName.trim(), description: description.trim() }); setPlatformKey(""); setDisplayName(""); setDescription(""); await loadData(); } catch (err) { setError(err instanceof Error ? err.message : "平台注册失败"); }
  };

  // ---------------- 文件操作 ----------------

  const getTargetUploadDir = () => currentBaselineDirectory || "work";

  const handleBaselineFileUpload = async (file?: File | null) => {
    if (!file || !activePlatformId) return;
    try {
      setBaselineError("");
      await uploadPlatformBaselineFile(activePlatformId, getTargetUploadDir(), file);
      await loadPlatformBaseline(activePlatformId);
    } catch (submitError) { setBaselineError(submitError instanceof Error ? submitError.message : "上传平台基线文件失败"); }
  };

  const handleBaselineSkillUpload = async (file: File) => {
    if (!activePlatformId) return;
    try {
      setSkillUploadBusy(true);
      setSkillUploadError("");
      await uploadPlatformBaselineSkill(activePlatformId, file);
      await loadPlatformBaseline(activePlatformId);
    } catch (submitError) {
      setSkillUploadError(submitError instanceof Error ? submitError.message : "上传技能失败");
    } finally {
      setSkillUploadBusy(false);
    }
  };

  const handleDownloadBaselineFile = async (fileRelativePath: string, fileName: string) => {
    if (!activePlatformId) return;
    try {
      setBaselineError("");
      const blob = await downloadPlatformBaselineFile(activePlatformId, fileRelativePath);
      const url = window.URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = fileName;
      document.body.appendChild(anchor); anchor.click(); anchor.remove(); window.URL.revokeObjectURL(url);
    } catch (err) { setBaselineError(err instanceof Error ? err.message : "下载失败"); }
  };

  const handleDeleteBaselineFile = async (relativePath: string) => {
    if (!activePlatformId) return;
    if (["input", "skills", "work", "output", "logs"].includes(relativePath)) {
      setBaselineError("根目录不允许删除。"); return;
    }
    if (!window.confirm(`确定要删除 ${relativePath} 吗？此操作不可恢复。`)) return;
    try {
      setBaselineError("");
      await deletePlatformBaselineFile(activePlatformId, relativePath);
      if (selectedBaselinePath === relativePath || selectedBaselinePath.startsWith(`${relativePath}/`)) {
        setSelectedBaselinePath(""); setSelectedBaselineContent(""); setBaselineDirty(false);
      }
      await loadPlatformBaseline(activePlatformId);
    } catch (err) { setBaselineError(err instanceof Error ? err.message : "删除失败"); }
  };

  const handleRenameBaselinePath = async (sourcePath: string) => {
    if (!activePlatformId) return;
    if (["input", "skills", "work", "output", "logs"].includes(sourcePath)) {
      setBaselineError("根目录不允许重命名。"); return;
    }
    const targetPath = window.prompt("输入新的完整路径", sourcePath);
    if (!targetPath?.trim() || targetPath.trim() === sourcePath) return;
    try {
      setBaselineError("");
      await movePlatformBaselinePath(activePlatformId, sourcePath, targetPath.trim());
      if (selectedBaselinePath === sourcePath) setSelectedBaselinePath(targetPath.trim());
      await loadPlatformBaseline(activePlatformId);
    } catch (err) { setBaselineError(err instanceof Error ? err.message : "重命名失败"); }
  };

  const handleSaveBaselineText = async () => {
    if (!activePlatformId || !selectedBaselinePath) return;
    try {
      setBaselineError("");
      await savePlatformBaselineTextFile(activePlatformId, selectedBaselinePath, selectedBaselineContent);
      setBaselineDirty(false);
      await loadPlatformBaseline(activePlatformId);
    } catch (err) { setBaselineError(err instanceof Error ? err.message : "保存失败"); }
  };

  const handleCreateBaselineFile = async () => {
    if (!activePlatformId) return;
    const targetDir = getTargetUploadDir();
    const filename = window.prompt(`输入新文件名 (当前目录：${targetDir})`, `new-file.txt`);
    if (!filename?.trim()) return;
    const fullPath = targetDir ? `${targetDir}/${filename.trim()}` : filename.trim();
    try {
      setBaselineError("");
      await savePlatformBaselineTextFile(activePlatformId, fullPath, "");
      await loadPlatformBaseline(activePlatformId);
      handleSelectFile({ name: filename.trim(), relative_path: fullPath, section: "work", kind: "file", size: 0, media_type: "text/plain" });
    } catch (err) { setBaselineError(err instanceof Error ? err.message : "创建文件失败"); }
  };

  const handleCreateBaselineDirectory = async () => {
    if (!activePlatformId) return;
    const targetDir = getTargetUploadDir();
    const directoryName = window.prompt(`输入新目录名 (当前目录：${targetDir})`, `new-folder`);
    if (!directoryName?.trim()) return;
    const fullPath = targetDir ? `${targetDir}/${directoryName.trim()}` : directoryName.trim();
    try {
      setBaselineError("");
      await createPlatformBaselineDirectory(activePlatformId, fullPath);
      await loadPlatformBaseline(activePlatformId);
    } catch (err) { setBaselineError(err instanceof Error ? err.message : "创建目录失败"); }
  };

  // ---------------- 视图驱动逻辑 ----------------

  const activePlatform = platforms.find((item) => item.platform_id === activePlatformId) ?? null;

  const parseJsonObject = (raw: string, label: string) => {
    const trimmed = raw.trim();
    if (!trimmed) return {};
    try {
      const parsed = JSON.parse(trimmed);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error(`${label}必须是 JSON 对象`);
      }
      return parsed as Record<string, unknown>;
    } catch (err) {
      throw new Error(err instanceof Error ? err.message : `${label}解析失败`);
    }
  };

  const parseLineList = (raw: string) =>
    raw
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);

  const handleSavePlatformLlm = async () => {
    if (!activePlatformId) return;
    try {
      setPlatformLlmBusy(true);
      setPlatformLlmError("");
      await updatePlatformLlmConfig(activePlatformId, {
        enabled: platformLlmForm.enabled,
        base_url: platformLlmForm.base_url.trim(),
        model: platformLlmForm.model.trim(),
        api_key: platformLlmForm.api_key.trim() || undefined,
        extra_headers: parseJsonObject(platformLlmForm.extra_headers_text, "扩展请求头") as Record<string, string>,
        extra_body: parseJsonObject(platformLlmForm.extra_body_text, "扩展请求体"),
        network: {
          enabled: platformLlmForm.network_enabled,
          allowed_domains: parseLineList(platformLlmForm.allowed_domains_text),
          blocked_domains: parseLineList(platformLlmForm.blocked_domains_text),
          max_search_results: platformLlmForm.max_search_results,
          fetch_timeout_seconds: platformLlmForm.fetch_timeout_seconds,
        },
      });
      const latest = await getPlatformLlmConfig(activePlatformId);
      const data = (latest.data ?? null) as {
        enabled: boolean;
        base_url: string;
        model: string;
        has_api_key: boolean;
        extra_headers?: Record<string, string>;
        extra_body?: Record<string, unknown>;
        network?: {
          enabled?: boolean;
          allowed_domains?: string[];
          blocked_domains?: string[];
          max_search_results?: number;
          fetch_timeout_seconds?: number;
        };
      } | null;
      setPlatformLlmForm({
        enabled: data?.enabled ?? true,
        base_url: data?.base_url ?? "",
        model: data?.model ?? "",
        api_key: "",
        extra_headers_text: data?.extra_headers && Object.keys(data.extra_headers).length > 0 ? JSON.stringify(data.extra_headers, null, 2) : "",
        extra_body_text: data?.extra_body && Object.keys(data.extra_body).length > 0 ? JSON.stringify(data.extra_body, null, 2) : "",
        has_api_key: Boolean(data?.has_api_key),
        network_enabled: data?.network?.enabled ?? true,
        allowed_domains_text: (data?.network?.allowed_domains ??[]).join("\n"),
        blocked_domains_text: (data?.network?.blocked_domains ??[]).join("\n"),
        max_search_results: data?.network?.max_search_results ?? 8,
        fetch_timeout_seconds: data?.network?.fetch_timeout_seconds ?? 30,
      });
      setShowPlatformLlmAdvanced(
        Boolean(
          (data?.extra_headers && Object.keys(data.extra_headers).length > 0) ||
          (data?.extra_body && Object.keys(data.extra_body).length > 0),
        ),
      );
    } catch (err) {
      setPlatformLlmError(err instanceof Error ? err.message : "保存平台 LLM 配置失败");
    } finally {
      setPlatformLlmBusy(false);
    }
  };

  const handleResetPlatformLlm = async () => {
    if (!activePlatformId) return;
    if (!window.confirm("确定删除该平台的专属 LLM 配置并回退到全局默认吗？")) return;
    try {
      setPlatformLlmBusy(true);
      setPlatformLlmError("");
      await deletePlatformLlmConfig(activePlatformId);
      setPlatformLlmForm({
        enabled: true,
        base_url: "",
        model: "",
        api_key: "",
        extra_headers_text: "",
        extra_body_text: "",
        has_api_key: false,
        network_enabled: true,
        allowed_domains_text: "",
        blocked_domains_text: "",
        max_search_results: 8,
        fetch_timeout_seconds: 30,
      });
      setShowPlatformLlmAdvanced(false);
    } catch (err) {
      setPlatformLlmError(err instanceof Error ? err.message : "删除平台 LLM 配置失败");
    } finally {
      setPlatformLlmBusy(false);
    }
  };

  const handleSavePlatformPrompt = async () => {
    if (!activePlatformId) return;
    try {
      setPromptBusy(true);
      setPromptError("");
      await updatePlatformPromptConfig(activePlatformId, {
        enabled: promptForm.enabled,
        system_prompt: promptForm.system_prompt,
      });
      const latest = await getPlatformPromptConfig(activePlatformId);
      const data = (latest.data ?? null) as {
        enabled: boolean;
        system_prompt: string;
      } | null;
      setPromptForm({
        enabled: data?.enabled ?? true,
        system_prompt: data?.system_prompt ?? "",
      });
    } catch (err) {
      setPromptError(err instanceof Error ? err.message : "保存平台提示词配置失败");
    } finally {
      setPromptBusy(false);
    }
  };

  const handleResetPlatformPrompt = async () => {
    if (!activePlatformId) return;
    if (!window.confirm("确定删除该平台的专属系统提示词配置吗？")) return;
    try {
      setPromptBusy(true);
      setPromptError("");
      await deletePlatformPromptConfig(activePlatformId);
      setPromptForm({
        enabled: true,
        system_prompt: "",
      });
    } catch (err) {
      setPromptError(err instanceof Error ? err.message : "删除平台提示词配置失败");
    } finally {
      setPromptBusy(false);
    }
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

  const renderHighlightedSnippet = (snippet: string | undefined) =>
    snippet ? snippet.split("{{YOUR_PLATFORM_BASE_URL}}").flatMap((part, index) =>
      index === 0
        ? [part]
        :[
            <span key={`placeholder-${index}`} className="guide-placeholder">
              {"{{YOUR_PLATFORM_BASE_URL}}"}
            </span>,
            part,
          ],
    ) : null;

  // 面包屑解析
  const breadcrumbs = useMemo(() => {
    if (!currentBaselineDirectory) return[];
    const parts = currentBaselineDirectory.split("/");
    return parts.map((part, index) => ({
      name: part,
      path: parts.slice(0, index + 1).join("/")
    }));
  },[currentBaselineDirectory]);

  // 当前目录内容 (仅限一层)
  const currentDirectoryChildren = useMemo(() => {
    const prefix = currentBaselineDirectory ? `${currentBaselineDirectory}/` : "";
    return baselineEntries.filter((item) => {
      // 在根目录下时，只显示基础 5 个目录
      if (!currentBaselineDirectory) {
        return !item.relative_path.includes("/");
      }
      if (item.relative_path === currentBaselineDirectory) return false;
      if (!item.relative_path.startsWith(prefix)) return false;
      const rest = item.relative_path.slice(prefix.length);
      return !rest.includes("/");
    }).sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === "directory" ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  }, [baselineEntries, currentBaselineDirectory]);

  // ---------------- 交互事件 ----------------

  const handleDoubleClickItem = (item: PlatformBaselineEntryItem) => {
    if (item.kind === "directory") {
      setCurrentBaselineDirectory(item.relative_path);
      setSelectedBaselinePath("");
      setBaselineDirty(false);
    } else {
      void handleSelectFile(item);
    }
  };

  const handleSelectFile = async (item: PlatformBaselineEntryItem) => {
    if (!activePlatformId || item.kind === "directory") return;
    try {
      setBaselineError("");
      const result = await getPlatformBaselineFileContent(activePlatformId, item.relative_path);
      const data = (result.data ?? {}) as { content?: string; media_type?: string; truncated?: boolean; };
      setSelectedBaselinePath(item.relative_path);
      setSelectedBaselineContent(data.content ?? "");
      setSelectedBaselineMediaType(data.media_type ?? item.media_type);
      setSelectedBaselineTruncated(Boolean(data.truncated));
      setBaselineDirty(false);
    } catch (err) { setBaselineError(err instanceof Error ? err.message : "读取文件失败"); }
  };

  const handleContextMenu = (e: React.MouseEvent, item: PlatformBaselineEntryItem) => {
    e.preventDefault();
    e.stopPropagation();
    
    // Calculate position slightly offset to ensure cursor doesn't instantly trigger a sub-hover
    setContextMenu({
      visible: true,
      x: e.clientX,
      y: e.clientY,
      item,
    });
  };

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
          onSelect={(platformId) => void loadPlatformBaseline(platformId)}
          onOpenGuide={(platform) => void handleOpenIntegrationGuide(platform)}
        />
      </div>

      {/* ================= Bento Grid: LLM 与基线资源管理器 ================= */}
      {activePlatform ? (
        <div className="epic-bento-grid stagger-4">
          <div className="epic-glass epic-bento-card">
            <PlatformPromptPanel
              promptForm={promptForm}
              promptError={promptError}
              promptBusy={promptBusy}
              onChange={setPromptForm}
              onSave={() => void handleSavePlatformPrompt()}
              onReset={() => void handleResetPlatformPrompt()}
            />
          </div>

          <div className="epic-glass epic-bento-card">
            <PlatformLlmPanel
              platformLlmForm={platformLlmForm}
              platformLlmError={platformLlmError}
              platformLlmBusy={platformLlmBusy}
              showPlatformLlmAdvanced={showPlatformLlmAdvanced}
              onToggleAdvanced={setShowPlatformLlmAdvanced}
              onChange={setPlatformLlmForm}
              onSave={() => void handleSavePlatformLlm()}
              onReset={() => void handleResetPlatformLlm()}
            />
          </div>

          <div className="epic-glass epic-bento-card">
            <BaselineManager
              activePlatform={activePlatform}
              baselineError={baselineError}
              fileManagerRef={fileManagerRef}
              breadcrumbs={breadcrumbs}
              currentDirectoryChildren={currentDirectoryChildren}
              currentBaselineDirectory={currentBaselineDirectory}
              selectedBaselinePath={selectedBaselinePath}
              selectedBaselineContent={selectedBaselineContent}
              selectedBaselineMediaType={selectedBaselineMediaType}
              selectedBaselineTruncated={selectedBaselineTruncated}
              baselineDirty={baselineDirty}
              onGoHome={() => { setCurrentBaselineDirectory(""); setSelectedBaselinePath(""); }}
              onGoBreadcrumb={(path) => { setCurrentBaselineDirectory(path); setSelectedBaselinePath(""); }}
              onCreateDirectory={() => void handleCreateBaselineDirectory()}
              onCreateFile={() => void handleCreateBaselineFile()}
              onUploadFile={(file) => void handleBaselineFileUpload(file)}
              onOpenSkillUpload={() => {
                setSkillUploadError("");
                setShowSkillUploadModal(true);
              }}
              onSelectFile={(item) => void handleSelectFile(item)}
              onDoubleClickItem={handleDoubleClickItem}
              onContextMenu={handleContextMenu}
              onContentChange={(value) => { setSelectedBaselineContent(value); setBaselineDirty(true); }}
              onSaveText={() => void handleSaveBaselineText()}
              onClosePreview={() => { setSelectedBaselinePath(""); setSelectedBaselineContent(""); }}
            />
          </div>
        </div>
      ) : null}

      <BaselineContextMenu
        contextMenu={contextMenu}
        onOpenDirectory={() => { handleDoubleClickItem(contextMenu.item!); setContextMenu({ ...contextMenu, visible: false}); }}
        onEditFile={() => { void handleSelectFile(contextMenu.item!); setContextMenu({ ...contextMenu, visible: false}); }}
        onDownloadFile={() => { void handleDownloadBaselineFile(contextMenu.item!.relative_path, contextMenu.item!.name); setContextMenu({ ...contextMenu, visible: false}); }}
        onRename={() => { void handleRenameBaselinePath(contextMenu.item!.relative_path); setContextMenu({ ...contextMenu, visible: false}); }}
        onDelete={() => { void handleDeleteBaselineFile(contextMenu.item!.relative_path); setContextMenu({ ...contextMenu, visible: false}); }}
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
        visible={showSkillUploadModal}
        busy={skillUploadBusy}
        error={skillUploadError}
        platformName={activePlatform?.display_name ?? ""}
        onClose={() => setShowSkillUploadModal(false)}
        onUpload={(file) => handleBaselineSkillUpload(file)}
      />
    </section>
  );
}
