// frontend/src/components/AdminPanel.tsx
import { FormEvent, useEffect, useMemo, useState, useRef } from "react";

import {
  createPlatformBaselineDirectory,
  createAdminWhitelist,
  createPlatform,
  deletePlatformBaselineFile,
  downloadPlatformBaselineFile,
  getPlatformBaseline,
  getPlatformBaselineFileContent,
  listAdminWhitelist,
  listPlatforms,
  movePlatformBaselinePath,
  savePlatformBaselineTextFile,
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
  const [providerUserId, setProviderUserId] = useState("");
  const [whitelistRole, setWhitelistRole] = useState<AdminWhitelistRole>("platform_admin");
  const[platformKey, setPlatformKey] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");

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
    try { setError(""); await createAdminWhitelist({ provider: "w3", provider_user_id: providerUserId.trim(), role: whitelistRole }); setProviderUserId(""); await loadData(); } catch (err) { setError(err instanceof Error ? err.message : "新增白名单失败"); }
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
            <p className="admin-panel__hint">填写 W3 账号或工号。支持 `uuid`、`uid` 自动匹配。</p>
            <input value={providerUserId} onChange={(e) => setProviderUserId(e.target.value)} placeholder="W3账号或工号" />
              <select value={whitelistRole} onChange={(e) => setWhitelistRole(e.target.value as AdminWhitelistRole)}>
              <option value="platform_admin">平台管理员</option>
              <option value="system_admin">系统管理员</option>
              <option value="debug">Debug 用户</option>
            </select>
            <button type="submit" disabled={!providerUserId.trim()}>添加白名单</button>
          </form>
          <form className="admin-panel__form" onSubmit={handleCreatePlatform}>
            <h4>注册新接入平台</h4>
            <p className="admin-panel__hint">这里只注册外部接入平台。AetherCore 自身作为内置平台自动存在，无需手动创建。</p>
            <input value={platformKey} onChange={(e) => setPlatformKey(e.target.value)} placeholder="platform_key，例如 atk-assistant" />
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="平台显示名称" />
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="平台说明" />
            <button type="submit" disabled={!platformKey.trim() || !displayName.trim()}>注册平台</button>
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
                <span className="badge">{describePlatformType(item)}</span>
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
    </section>
  );
}
