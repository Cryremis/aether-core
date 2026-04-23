// frontend/src/components/AdminPanel.tsx
import { FormEvent, useEffect, useMemo, useState, useRef } from "react";

import {
  createPlatformBaselineDirectory,
  createAdminWhitelist,
  createPlatform,
  deletePlatformLlmConfig,
  deletePlatformBaselineFile,
  downloadPlatformBaselineFile,
  getPlatformBaseline,
  getPlatformBaselineFileContent,
  getPlatformIntegrationGuide,
  getPlatformLlmConfig,
  listAdminWhitelist,
  listPlatforms,
  movePlatformBaselinePath,
  PlatformIntegrationGuide,
  savePlatformBaselineTextFile,
  updatePlatformLlmConfig,
  uploadPlatformBaselineFile,
} from "../api/client";

type AdminPanelProps = {
  role: string;
};

type PlatformItem = {
  platform_id: number;
  platform_key: string;
  display_name: string;
  host_type: string;
  description: string;
  owner_name: string;
  host_secret: string;
};

type PlatformBaselineFileItem = {
  name: string;
  relative_path: string;
  section: "input" | "skills" | "work" | "output" | "logs";
  size: number;
  media_type: string;
};

type PlatformBaselineEntryItem = {
  name: string;
  relative_path: string;
  section: "input" | "skills" | "work" | "output" | "logs";
  kind: "file" | "directory";
  size: number;
  media_type: string;
};

type WhitelistItem = {
  whitelist_id: number;
  provider: string;
  provider_user_id: string;
  full_name: string;
  role: string;
};

type AdminWhitelistRole = "system_admin" | "platform_admin" | "debug";
type LlmConfigFormState = {
  enabled: boolean;
  base_url: string;
  model: string;
  api_key: string;
  extra_headers_text: string;
  extra_body_text: string;
  has_api_key: boolean;
  network_enabled: boolean;
  allowed_domains_text: string;
  blocked_domains_text: string;
  max_search_results: number;
  fetch_timeout_seconds: number;
};

// ================== SVG 图标集合 ==================
const Icons = {
  Folder: () => <svg viewBox="0 0 24 24" width="32" height="32" fill="#60a5fa" stroke="none"><path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>,
  File: () => <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="#94a3b8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path><polyline points="13 2 13 9 20 9"></polyline></svg>,
  ChevronRight: () => <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>,
  MoreVertical: () => <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="1"></circle><circle cx="12" cy="5" r="1"></circle><circle cx="12" cy="19" r="1"></circle></svg>,
  Upload: () => <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>,
  FolderPlus: () => <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path><line x1="12" y1="11" x2="12" y2="17"></line><line x1="9" y1="14" x2="15" y2="14"></line></svg>,
  FilePlus: () => <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="12" y1="18" x2="12" y2="12"></line><line x1="9" y1="15" x2="15" y2="15"></line></svg>,
  Download: () => <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>,
  Edit2: () => <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"></path></svg>,
  Trash2: () => <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>,
};

export function AdminPanel({ role }: AdminPanelProps) {
  const [platforms, setPlatforms] = useState<PlatformItem[]>([]);
  const [whitelist, setWhitelist] = useState<WhitelistItem[]>([]);
  const[activePlatformId, setActivePlatformId] = useState<number | null>(null);
  const [baselineEntries, setBaselineEntries] = useState<PlatformBaselineEntryItem[]>([]);
  const [error, setError] = useState("");
  const[baselineError, setBaselineError] = useState("");
  
  // File Manager State
  const[currentBaselineDirectory, setCurrentBaselineDirectory] = useState(""); // "" 代表根目录 (显示 input/skills/work 等)
  const[selectedBaselinePath, setSelectedBaselinePath] = useState("");
  const [selectedBaselineContent, setSelectedBaselineContent] = useState("");
  const [selectedBaselineMediaType, setSelectedBaselineMediaType] = useState("");
  const[selectedBaselineTruncated, setSelectedBaselineTruncated] = useState(false);
  const [baselineDirty, setBaselineDirty] = useState(false);

  // Context Menu State
  const [contextMenu, setContextMenu] = useState<{ visible: boolean; x: number; y: number; item: PlatformBaselineEntryItem | null }>({ visible: false, x: 0, y: 0, item: null });

  // Form States
  const [providerKey, setProviderKey] = useState("password");
  const [providerUserId, setProviderUserId] = useState("");
  const [whitelistRole, setWhitelistRole] = useState<AdminWhitelistRole>("platform_admin");
  const[platformKey, setPlatformKey] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
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
  const [platformLlmError, setPlatformLlmError] = useState("");
  const [platformLlmBusy, setPlatformLlmBusy] = useState(false);
  const [showPlatformLlmAdvanced, setShowPlatformLlmAdvanced] = useState(false);
  const [integrationGuide, setIntegrationGuide] = useState<PlatformIntegrationGuide | null>(null);
  const [integrationGuideError, setIntegrationGuideError] = useState("");
  const [integrationGuideBusy, setIntegrationGuideBusy] = useState(false);
  const [integrationGuidePlatformName, setIntegrationGuidePlatformName] = useState("");

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
      const [platformResult, whitelistResult] = await Promise.all([
        listPlatforms(),
        role === "system_admin" ? listAdminWhitelist() : Promise.resolve({ data: [] }),
      ]);
      setPlatforms((platformResult.data ?? []) as PlatformItem[]);
      setWhitelist((whitelistResult.data ?? []) as WhitelistItem[]);
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
          allowed_domains_text: (data?.network?.allowed_domains ?? []).join("\n"),
          blocked_domains_text: (data?.network?.blocked_domains ?? []).join("\n"),
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
  }, [activePlatformId]);

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
  }, [baselineEntries, selectedBaselinePath]);

  // Form Handlers
  const handleCreateWhitelist = async (e: FormEvent) => { /* 略，原逻辑保持 */
    e.preventDefault();
    try {
      setError("");
      await createAdminWhitelist({
        provider: providerKey.trim(),
        provider_user_id: providerUserId.trim(),
        role: whitelistRole,
      });
      setProviderUserId("");
      await loadData();
    } catch (err) { setError(err instanceof Error ? err.message : "新增白名单失败"); }
  };

  const handleCreatePlatform = async (e: FormEvent) => { /* 略，原逻辑保持 */
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
  const describePlatformType = (item: PlatformItem) => item.platform_key === "standalone" || item.host_type === "standalone" ? "内置平台" : "接入平台";

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
        allowed_domains_text: (data?.network?.allowed_domains ?? []).join("\n"),
        blocked_domains_text: (data?.network?.blocked_domains ?? []).join("\n"),
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
        : [
            <span key={`placeholder-${index}`} className="guide-placeholder">
              {"{{YOUR_PLATFORM_BASE_URL}}"}
            </span>,
            part,
          ],
    ) : null;

  const integrationGuideDetails = [
    {
      title: "推荐接入顺序",
      body: "按页面顺序复制即可：先复制前端代码，再复制 .env 示例，最后复制后端 Bind 示例。默认代码已经填好平台信息，只需要检查你们平台自己的用户对象和公网地址。",
    },
    {
      title: "前端这段代码负责什么",
      body: "它负责浮球、抽屉、iframe、会话 key 持久化这些通用逻辑。大多数平台只需要确认 /static/aethercore-embed.js 是否能访问，以及 getUserId 能拿到当前登录用户 ID。",
    },
    {
      title: ".env 怎么用",
      body: ".env 示例里已经填好 AetherCore 地址、platform_key 和 platform_secret。你只需要把 PLATFORM_PUBLIC_BASE_URL 换成你们平台自己的公网根地址。如果你们框架不自动加载 .env，也可以把同样的值直接配成环境变量。",
    },
    {
      title: "后端 Bind 示例怎么用",
      body: "后端示例是完整 FastAPI 写法，不依赖你们项目里的 settings 对象。复制后通常只需要按你们平台的登录体系调整 request.state.user 这一行，其它逻辑就是调用 AetherCore 并返回 token 和 session_id。",
    },
    {
      title: "需要你自己替换的内容",
      body: "{{YOUR_PLATFORM_BASE_URL}} 需要换成你们平台对外可访问的根地址。高亮只用于提示你这里要检查，复制按钮复制出去的仍然是原始代码。",
    },
    {
      title: "如果你只想先快速接起来",
      body: "可以先按最小场景接入，不注入任何 tools、skills、apis，只验证浮球和对话工作台是否正常。安全加固和宿主能力注入可以后续再补。",
    },
    {
      title: "关于安全",
      body: "更推荐把 host_secret 放在服务端，由服务端代理 bind，这样更稳。如果你们当前阶段更在意快速验证，也可以先按自己的方式做 PoC，只要清楚这样会带来额外风险即可。",
    },
  ];

  // 面包屑解析
  const breadcrumbs = useMemo(() => {
    if (!currentBaselineDirectory) return[];
    const parts = currentBaselineDirectory.split("/");
    return parts.map((part, index) => ({
      name: part,
      path: parts.slice(0, index + 1).join("/")
    }));
  }, [currentBaselineDirectory]);

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
      <div className="admin-panel__header">
        <h3>管理配置</h3>
      </div>

      {error ? <div className="admin-panel__error">{error}</div> : null}

      {/* 白名单与平台注册表单（仅超级管理员）保持原样 */}
      {role === "system_admin" ? (
        <div className="admin-grid-forms">
          <form className="admin-panel__form" onSubmit={handleCreateWhitelist}>
            <h4>管理员白名单</h4>
            <p className="admin-panel__hint">按 provider key + 用户标识录入，例如 `password`、`corp-sso`、`github`。</p>
            <input value={providerKey} onChange={(e) => setProviderKey(e.target.value)} placeholder="provider key，例如 github" />
            <input value={providerUserId} onChange={(e) => setProviderUserId(e.target.value)} placeholder="用户唯一标识、工号或账号" />
            <select value={whitelistRole} onChange={(e) => setWhitelistRole(e.target.value as AdminWhitelistRole)}>
              <option value="platform_admin">平台管理员</option>
              <option value="system_admin">系统管理员</option>
              <option value="debug">Debug 用户</option>
            </select>
            <button type="submit" className="action-button w-full" disabled={!providerKey.trim() || !providerUserId.trim()}>添加白名单</button>
          </form>
          <form className="admin-panel__form" onSubmit={handleCreatePlatform}>
            <h4>注册新接入平台</h4>
            <p className="admin-panel__hint">这里只注册外部接入平台。AetherCore 自身作为内置平台自动存在，无需手动创建。</p>
            <input value={platformKey} onChange={(e) => setPlatformKey(e.target.value)} placeholder="platform_key，例如 atk-assistant" />
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="平台显示名称" />
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="平台说明" />
            <button type="submit" className="action-button w-full" disabled={!platformKey.trim() || !displayName.trim()}>注册平台</button>
          </form>
        </div>
      ) : null}

      <div className="admin-panel__list">
        <h4>管理的平台</h4>
        <div className="platform-grid">
          {platforms.length === 0 ? <div className="admin-panel__empty">当前没有可管理的平台。</div> : null}
          {platforms.map((item) => (
            <article key={item.platform_id} className={`admin-panel__card ${activePlatformId === item.platform_id ? "is-active" : ""}`} onClick={() => void loadPlatformBaseline(item.platform_id)}>
              <div className="platform-card__head">
                <strong>{item.display_name}</strong>
                <button
                  type="button"
                  className="action-button small action-button--ghost"
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleOpenIntegrationGuide(item);
                  }}
                >
                  接入教程
                </button>
              </div>
              <p>{item.platform_key}</p>
              <p className="desc">{item.description || "未填写平台说明"}</p>
              <div className="secret-code"><code>{item.host_secret}</code></div>
            </article>
          ))}
        </div>
      </div>

      {/* ================= 现代化资源管理器 ================= */}
      {activePlatform ? (
        <div className="admin-panel__list baseline-manager-wrapper">
          <div className="admin-panel__form admin-panel__form--llm">
            <h4>平台默认 LLM</h4>
            <p className="admin-panel__hint">这里配置该平台的新会话默认使用的 LiteLLM / OpenAI 兼容入口。终端用户如配置了个人 LLM，会优先覆盖这里。</p>
            {platformLlmError ? <div className="admin-panel__error">{platformLlmError}</div> : null}
            <label className="admin-panel__checkbox">
              <input
                type="checkbox"
                checked={platformLlmForm.enabled}
                onChange={(e) => setPlatformLlmForm((current) => ({ ...current, enabled: e.target.checked }))}
              />
              <span>启用平台默认 LLM</span>
            </label>
            <input
              value={platformLlmForm.base_url}
              onChange={(e) => setPlatformLlmForm((current) => ({ ...current, base_url: e.target.value }))}
              autoComplete="off"
              name="platform-llm-base-url"
              placeholder="LiteLLM 或内网 OpenAI 兼容服务地址，例如 http://litellm.internal:4000/v1"
            />
            <input
              value={platformLlmForm.model}
              onChange={(e) => setPlatformLlmForm((current) => ({ ...current, model: e.target.value }))}
              autoComplete="off"
              name="platform-llm-model-id"
              placeholder="模型 ID，例如 glm-4.5 / minimax-m1 / gpt-4o-mini"
            />
            <input
              type="password"
              value={platformLlmForm.api_key}
              onChange={(e) => setPlatformLlmForm((current) => ({ ...current, api_key: e.target.value }))}
              autoComplete="new-password"
              name="platform-llm-api-key"
              placeholder={platformLlmForm.has_api_key ? "已存在密钥，留空则保持不变" : "API Key"}
            />
            <details className="llm-advanced-panel" open={showPlatformLlmAdvanced} onToggle={(e) => setShowPlatformLlmAdvanced((e.currentTarget as HTMLDetailsElement).open)}>
              <summary>高级参数</summary>
              <p className="admin-panel__hint">仅在代理网关、租户透传或内网兼容服务需要补充额外 headers/body 时填写。留空即可。</p>
              <textarea
                value={platformLlmForm.extra_headers_text}
                onChange={(e) => setPlatformLlmForm((current) => ({ ...current, extra_headers_text: e.target.value }))}
                autoComplete="off"
                name="platform-llm-extra-headers"
                placeholder='额外请求头 JSON，例如 {"x-foo":"bar"}'
              />
              <textarea
                value={platformLlmForm.extra_body_text}
                onChange={(e) => setPlatformLlmForm((current) => ({ ...current, extra_body_text: e.target.value }))}
                autoComplete="off"
                name="platform-llm-extra-body"
                placeholder='额外请求体 JSON，例如 {"reasoning":{"effort":"medium"}}'
              />
            </details>
            <details className="llm-advanced-panel">
              <summary>联网策略</summary>
              <label className="admin-panel__checkbox">
                <input type="checkbox" checked={platformLlmForm.network_enabled} onChange={(e) => setPlatformLlmForm((current) => ({ ...current, network_enabled: e.target.checked }))} />
                <span>启用联网工具</span>
              </label>
              <p className="admin-panel__hint">系统只使用模型原生联网搜索能力；这里保留的是平台治理策略，不再要求额外配置搜索服务。</p>
              <label className="admin-panel__field">
                <span>允许访问域名</span>
                <textarea value={platformLlmForm.allowed_domains_text} onChange={(e) => setPlatformLlmForm((current) => ({ ...current, allowed_domains_text: e.target.value }))} placeholder={"每行一个\nexample.com"} />
              </label>
              <label className="admin-panel__field">
                <span>禁止访问域名</span>
                <textarea value={platformLlmForm.blocked_domains_text} onChange={(e) => setPlatformLlmForm((current) => ({ ...current, blocked_domains_text: e.target.value }))} placeholder={"每行一个\ninternal.example.com"} />
              </label>
              <label className="admin-panel__field">
                <span>最大搜索结果数</span>
                <input type="number" min={1} max={20} value={platformLlmForm.max_search_results} onChange={(e) => setPlatformLlmForm((current) => ({ ...current, max_search_results: Number(e.target.value || 8) }))} />
              </label>
              <label className="admin-panel__field">
                <span>网页抓取超时（秒）</span>
                <input type="number" min={1} max={120} value={platformLlmForm.fetch_timeout_seconds} onChange={(e) => setPlatformLlmForm((current) => ({ ...current, fetch_timeout_seconds: Number(e.target.value || 30) }))} />
              </label>
            </details>
            <div className="admin-panel__actions">
              <button type="button" className="action-button" onClick={() => void handleSavePlatformLlm()} disabled={platformLlmBusy || !platformLlmForm.base_url.trim() || !platformLlmForm.model.trim()}>
                {platformLlmBusy ? "保存中..." : "保存平台 LLM"}
              </button>
              <button type="button" className="action-button action-button--ghost" onClick={() => void handleResetPlatformLlm()} disabled={platformLlmBusy}>
                清除覆盖
              </button>
            </div>
          </div>

          <div className="manager-header">
            <div className="manager-header__info">
              <h4>基线资源管理器</h4>
              <p>当前平台：{activePlatform.display_name}。预置文件将在新会话创建时注入到沙箱。</p>
            </div>
            {baselineError ? <div className="baseline-error-toast">{baselineError}</div> : null}
          </div>

          <div className="file-manager-container" ref={fileManagerRef}>
            {/* 上部：工具栏与导航 */}
            <div className="fm-toolbar">
              <div className="fm-breadcrumbs">
                <button className="crumb-btn home-crumb" onClick={() => { setCurrentBaselineDirectory(""); setSelectedBaselinePath(""); }}>
                  {activePlatform.platform_key}
                </button>
                {breadcrumbs.map((crumb) => (
                  <div key={crumb.path} className="crumb-item">
                    <Icons.ChevronRight />
                    <button className="crumb-btn" onClick={() => { setCurrentBaselineDirectory(crumb.path); setSelectedBaselinePath(""); }}>
                      {crumb.name}
                    </button>
                  </div>
                ))}
              </div>
              
              <div className="fm-actions">
                <button className="fm-btn outline" onClick={() => void handleCreateBaselineDirectory()} title="新建文件夹">
                  <Icons.FolderPlus /> <span>新建目录</span>
                </button>
                <button className="fm-btn outline" onClick={() => void handleCreateBaselineFile()} title="新建文本文件">
                  <Icons.FilePlus /> <span>新建文件</span>
                </button>
                <label className="fm-btn primary" title="上传文件到当前目录">
                  <Icons.Upload /> <span>上传</span>
                  <input type="file" onChange={(e) => { void handleBaselineFileUpload(e.target.files?.[0]); e.currentTarget.value=""; }} />
                </label>
              </div>
            </div>

            {/* 下部：分栏视图 (左：网格/列表，右：编辑器侧边栏) */}
            <div className="fm-split-view">
              
              {/* Explorer 区域 */}
              <div className="fm-explorer" onContextMenu={(e) => { e.preventDefault(); /* Prevent default right click on empty area */ }}>
                {currentDirectoryChildren.length === 0 ? (
                  <div className="fm-empty-state">当前目录为空</div>
                ) : (
                  <div className="fm-grid">
                    {currentDirectoryChildren.map((item) => (
                      <div 
                        key={item.relative_path} 
                        className={`fm-item ${selectedBaselinePath === item.relative_path ? "is-selected" : ""}`}
                        onClick={() => item.kind === "file" && handleSelectFile(item)}
                        onDoubleClick={() => handleDoubleClickItem(item)}
                        onContextMenu={(e) => handleContextMenu(e, item)}
                      >
                        <div className="fm-item__icon">
                          {item.kind === "directory" ? <Icons.Folder /> : <Icons.File />}
                        </div>
                        <span className="fm-item__name" title={item.name}>{item.name}</span>
                        {item.kind === "file" && <span className="fm-item__meta">{(item.size / 1024).toFixed(1)} KB</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Editor 侧栏区域 */}
              <div className={`fm-editor-drawer ${selectedBaselinePath ? "is-open" : ""}`}>
                {selectedBaselinePath ? (
                  <>
                    <div className="fm-editor__header">
                      <div className="fm-editor__title">
                        <strong>{selectedBaselinePath.split('/').pop()}</strong>
                        <span>{selectedBaselineMediaType} {selectedBaselineTruncated ? " (已截断)" : ""}</span>
                      </div>
                      <div className="fm-editor__actions">
                         <button className="fm-btn primary small" onClick={() => void handleSaveBaselineText()} disabled={!baselineDirty}>
                           保存修改
                         </button>
                         <button className="fm-btn outline small icon-only" onClick={() => { setSelectedBaselinePath(""); setSelectedBaselineContent(""); }} title="关闭预览">
                           &times;
                         </button>
                      </div>
                    </div>
                    <textarea
                      className="fm-editor__textarea"
                      value={selectedBaselineContent}
                      onChange={(e) => { setSelectedBaselineContent(e.target.value); setBaselineDirty(true); }}
                      spellCheck={false}
                    />
                  </>
                ) : (
                  <div className="fm-editor__placeholder">
                    <Icons.File />
                    <p>选择一个文件进行预览或编辑</p>
                  </div>
                )}
              </div>

            </div>
          </div>

        </div>
      ) : null}

      {/* ================= 浮动上下文菜单 ================= */}
      {contextMenu.visible && contextMenu.item && (
        <div 
          className="fm-context-menu" 
          style={{ top: contextMenu.y, left: contextMenu.x }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="context-menu__header">
            {contextMenu.item.name}
          </div>
          {contextMenu.item.kind === "directory" ? (
             <button className="context-menu__item" onClick={() => { handleDoubleClickItem(contextMenu.item!); setContextMenu({ ...contextMenu, visible: false}); }}>
               <Icons.Folder /> 打开目录
             </button>
          ) : (
            <>
              <button className="context-menu__item" onClick={() => { handleSelectFile(contextMenu.item!); setContextMenu({ ...contextMenu, visible: false}); }}>
                <Icons.Edit2 /> 预览 / 编辑
              </button>
              <button className="context-menu__item" onClick={() => { handleDownloadBaselineFile(contextMenu.item!.relative_path, contextMenu.item!.name); setContextMenu({ ...contextMenu, visible: false}); }}>
                <Icons.Download /> 下载文件
              </button>
            </>
          )}
          <div className="context-menu__divider" />
          <button className="context-menu__item" onClick={() => { handleRenameBaselinePath(contextMenu.item!.relative_path); setContextMenu({ ...contextMenu, visible: false}); }}>
            <Icons.Edit2 /> 重命名
          </button>
          <button className="context-menu__item danger" onClick={() => { handleDeleteBaselineFile(contextMenu.item!.relative_path); setContextMenu({ ...contextMenu, visible: false}); }}>
            <Icons.Trash2 /> 删除
          </button>
        </div>
      )}

      {role === "system_admin" ? (
        <div className="admin-panel__list">
          <h4>白名单记录</h4>
          {whitelist.length === 0 ? <div className="admin-panel__empty">当前没有白名单记录。</div> : null}
          <div className="whitelist-grid">
            {whitelist.map((item) => (
              <article key={item.whitelist_id} className="admin-panel__card">
                <div className="flex-row">
                   <strong>{item.full_name}</strong>
                   <span className="badge">{item.role}</span>
                </div>
                <code>{item.provider} : {item.provider_user_id}</code>
              </article>
            ))}
          </div>
        </div>
      ) : null}

      {(integrationGuide || integrationGuideBusy || integrationGuideError) ? (
        <div className="guide-modal-backdrop" onClick={closeIntegrationGuide}>
          <div className="guide-modal" onClick={(e) => e.stopPropagation()}>
            <div className="guide-modal__header">
              <div>
                <h4>接入教程</h4>
                <p>{integrationGuidePlatformName || integrationGuide?.display_name || "平台接入说明"}：按顺序复制代码块，按提示替换高亮内容即可。</p>
              </div>
              <button type="button" className="icon-button" onClick={closeIntegrationGuide} aria-label="关闭接入教程">
                ×
              </button>
            </div>

            {integrationGuideBusy ? <div className="admin-panel__empty">接入教程加载中...</div> : null}
            {integrationGuideError ? <div className="admin-panel__error">{integrationGuideError}</div> : null}

            {integrationGuide ? (
              <div className="guide-modal__body">
                <section className="guide-section">
                  <div className="guide-split-layout">
                    <div className="guide-split-layout__code">
                      <div className="guide-section">
                        <h5>前端复制代码</h5>
                        <div className="guide-section__head">
                          <span className="guide-section__label">复制后放到全局布局页</span>
                          <button type="button" className="action-button small" onClick={() => void copyText(integrationGuide.snippets.frontend)}>
                            复制
                          </button>
                        </div>
                        <pre className="guide-code-block"><code>{renderHighlightedSnippet(integrationGuide.snippets.frontend)}</code></pre>
                      </div>

                      <div className="guide-section">
                        <h5>后端 .env 示例</h5>
                        <div className="guide-section__head">
                          <span className="guide-section__label">推荐放到你们后端服务的环境变量或 .env 文件</span>
                          <button type="button" className="action-button small" onClick={() => void copyText(integrationGuide.snippets.backend_env)}>
                            复制
                          </button>
                        </div>
                        <pre className="guide-code-block guide-code-block--env"><code>{renderHighlightedSnippet(integrationGuide.snippets.backend_env)}</code></pre>
                      </div>

                      <div className="guide-section">
                        <h5>后端 Bind 示例</h5>
                        <div className="guide-section__head">
                          <span className="guide-section__label">完整 FastAPI 示例，复制后按你们用户体系改 user 获取逻辑</span>
                          <button type="button" className="action-button small" onClick={() => void copyText(integrationGuide.snippets.backend_fastapi)}>
                            复制
                          </button>
                        </div>
                        <pre className="guide-code-block"><code>{renderHighlightedSnippet(integrationGuide.snippets.backend_fastapi)}</code></pre>
                      </div>
                    </div>

                    <aside className="guide-side-panel">
                      <h6>接入说明</h6>
                      <div className="guide-note-list">
                        {integrationGuideDetails.map((item) => (
                          <div key={item.title} className="guide-note-item">
                            <strong>{item.title}</strong>
                            <p>{item.body}</p>
                          </div>
                        ))}
                      </div>
                    </aside>
                  </div>
                </section>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}
