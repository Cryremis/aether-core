// frontend/src/components/AdminPanel.tsx
import { FormEvent, useEffect, useMemo, useState } from "react";

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
  uploadPlatformBaselineSkill,
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

export function AdminPanel({ role }: AdminPanelProps) {
  const [platforms, setPlatforms] = useState<PlatformItem[]>([]);
  const [whitelist, setWhitelist] = useState<WhitelistItem[]>([]);
  const [activePlatformId, setActivePlatformId] = useState<number | null>(null);
  const [baselineFiles, setBaselineFiles] = useState<PlatformBaselineFileItem[]>([]);
  const [baselineEntries, setBaselineEntries] = useState<PlatformBaselineEntryItem[]>([]);
  const [error, setError] = useState("");
  const [baselineError, setBaselineError] = useState("");
  const [currentBaselineDirectory, setCurrentBaselineDirectory] = useState("work");
  const [selectedBaselinePath, setSelectedBaselinePath] = useState("");
  const [selectedBaselineContent, setSelectedBaselineContent] = useState("");
  const [selectedBaselineMediaType, setSelectedBaselineMediaType] = useState("");
  const [selectedBaselineTruncated, setSelectedBaselineTruncated] = useState(false);
  const [baselineDirty, setBaselineDirty] = useState(false);

  const [providerUserId, setProviderUserId] = useState("");
  const [whitelistRole, setWhitelistRole] = useState("platform_admin");

  const [platformKey, setPlatformKey] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [hostType, setHostType] = useState("embedded");
  const [description, setDescription] = useState("");

  const existingPlatformKeys = useMemo(
    () => new Set(platforms.map((item) => item.platform_key)),
    [platforms],
  );

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
      const data = (result.data ?? {}) as {
        files?: PlatformBaselineFileItem[];
        entries?: PlatformBaselineEntryItem[];
      };
      setBaselineFiles(data.files ?? []);
      setBaselineEntries(data.entries ?? []);
      setActivePlatformId(platformId);
    } catch (loadError) {
      setBaselineError(loadError instanceof Error ? loadError.message : "加载平台基线环境失败");
    }
  };

  useEffect(() => {
    void loadData();
  }, [role]);

  useEffect(() => {
    if (!platforms.length) {
      setActivePlatformId(null);
      setBaselineFiles([]);
      setBaselineEntries([]);
      return;
    }

    const preferred =
      platforms.find((item) => item.platform_key === "standalone") ??
      platforms[0];
    const targetId = activePlatformId && platforms.some((item) => item.platform_id === activePlatformId)
      ? activePlatformId
      : preferred.platform_id;
    void loadPlatformBaseline(targetId);
  }, [platforms]);

  useEffect(() => {
    if (!selectedBaselinePath) {
      return;
    }
    if (!baselineEntries.some((item) => item.relative_path === selectedBaselinePath && item.kind === "file")) {
      setSelectedBaselinePath("");
      setSelectedBaselineContent("");
      setSelectedBaselineMediaType("");
      setSelectedBaselineTruncated(false);
      setBaselineDirty(false);
    }
  }, [baselineEntries, selectedBaselinePath]);

  const handleCreateWhitelist = async (event: FormEvent) => {
    event.preventDefault();
    try {
      setError("");
      await createAdminWhitelist({
        provider: "w3",
        provider_user_id: providerUserId.trim(),
        role: whitelistRole,
      });
      setProviderUserId("");
      await loadData();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "新增白名单失败");
    }
  };

  const handleCreatePlatform = async (event: FormEvent) => {
    event.preventDefault();
    const normalizedPlatformKey = platformKey.trim().toLowerCase();
    if (existingPlatformKeys.has(normalizedPlatformKey)) {
      setError(`platform_key "${normalizedPlatformKey}" 已存在，请更换`);
      return;
    }

    try {
      setError("");
      await createPlatform({
        platform_key: normalizedPlatformKey,
        display_name: displayName.trim(),
        host_type: hostType as "embedded" | "standalone",
        description: description.trim(),
      });
      setPlatformKey("");
      setDisplayName("");
      setHostType("embedded");
      setDescription("");
      await loadData();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "平台注册失败");
    }
  };

  const handleBaselineFileUpload = async (file?: File | null) => {
    if (!file || !activePlatformId) {
      return;
    }
    try {
      setBaselineError("");
      await uploadPlatformBaselineFile(activePlatformId, currentBaselineDirectory, file);
      await loadPlatformBaseline(activePlatformId);
    } catch (submitError) {
      setBaselineError(submitError instanceof Error ? submitError.message : "上传平台基线文件失败");
    }
  };

  const handleBaselineSkillUpload = async (file?: File | null) => {
    if (!file || !activePlatformId) {
      return;
    }
    try {
      setBaselineError("");
      await uploadPlatformBaselineSkill(activePlatformId, file);
      await loadPlatformBaseline(activePlatformId);
    } catch (submitError) {
      setBaselineError(submitError instanceof Error ? submitError.message : "上传平台基线技能失败");
    }
  };

  const handleDownloadBaselineFile = async (file: PlatformBaselineFileItem) => {
    if (!activePlatformId) {
      return;
    }
    try {
      setBaselineError("");
      const blob = await downloadPlatformBaselineFile(activePlatformId, file.relative_path);
      const url = window.URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = file.name;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.URL.revokeObjectURL(url);
    } catch (downloadError) {
      setBaselineError(downloadError instanceof Error ? downloadError.message : "下载平台基线文件失败");
    }
  };

  const handleDeleteBaselineFile = async (relativePath: string) => {
    if (!activePlatformId) {
      return;
    }
    try {
      setBaselineError("");
      await deletePlatformBaselineFile(activePlatformId, relativePath);
      if (selectedBaselinePath === relativePath || selectedBaselinePath.startsWith(`${relativePath}/`)) {
        setSelectedBaselinePath("");
        setSelectedBaselineContent("");
        setSelectedBaselineMediaType("");
        setSelectedBaselineTruncated(false);
        setBaselineDirty(false);
      }
      await loadPlatformBaseline(activePlatformId);
    } catch (deleteError) {
      setBaselineError(deleteError instanceof Error ? deleteError.message : "删除平台基线文件失败");
    }
  };

  const handleSelectBaselineEntry = async (item: PlatformBaselineEntryItem) => {
    if (!activePlatformId) {
      return;
    }
    if (item.kind === "directory") {
      setCurrentBaselineDirectory(item.relative_path);
      setSelectedBaselinePath("");
      setSelectedBaselineContent("");
      setSelectedBaselineMediaType("");
      setSelectedBaselineTruncated(false);
      setBaselineDirty(false);
      return;
    }
    try {
      setBaselineError("");
      const result = await getPlatformBaselineFileContent(activePlatformId, item.relative_path);
      const data = (result.data ?? {}) as {
        content?: string;
        media_type?: string;
        truncated?: boolean;
      };
      setSelectedBaselinePath(item.relative_path);
      setCurrentBaselineDirectory(item.relative_path.split("/").slice(0, -1).join("/") || item.section);
      setSelectedBaselineContent(data.content ?? "");
      setSelectedBaselineMediaType(data.media_type ?? item.media_type);
      setSelectedBaselineTruncated(Boolean(data.truncated));
      setBaselineDirty(false);
    } catch (loadError) {
      setBaselineError(loadError instanceof Error ? loadError.message : "加载平台基线文件内容失败");
    }
  };

  const handleSaveBaselineText = async () => {
    if (!activePlatformId || !selectedBaselinePath) {
      return;
    }
    try {
      setBaselineError("");
      await savePlatformBaselineTextFile(activePlatformId, selectedBaselinePath, selectedBaselineContent);
      setBaselineDirty(false);
      await loadPlatformBaseline(activePlatformId);
    } catch (saveError) {
      setBaselineError(saveError instanceof Error ? saveError.message : "保存平台基线文件失败");
    }
  };

  const handleCreateBaselineFile = async () => {
    if (!activePlatformId) {
      return;
    }
    const filename = window.prompt(
      `输入新文件名或相对路径，当前目录：${currentBaselineDirectory}`,
      `${currentBaselineDirectory}/new-file.txt`,
    );
    if (!filename?.trim()) {
      return;
    }
    try {
      setBaselineError("");
      await savePlatformBaselineTextFile(activePlatformId, filename.trim(), "");
      await loadPlatformBaseline(activePlatformId);
      setSelectedBaselinePath(filename.trim());
      setSelectedBaselineContent("");
      setSelectedBaselineMediaType("text/plain");
      setSelectedBaselineTruncated(false);
      setBaselineDirty(false);
    } catch (createError) {
      setBaselineError(createError instanceof Error ? createError.message : "创建平台基线文件失败");
    }
  };

  const handleCreateBaselineDirectory = async () => {
    if (!activePlatformId) {
      return;
    }
    const directoryPath = window.prompt(
      `输入新目录路径，当前目录：${currentBaselineDirectory}`,
      `${currentBaselineDirectory}/new-folder`,
    );
    if (!directoryPath?.trim()) {
      return;
    }
    try {
      setBaselineError("");
      await createPlatformBaselineDirectory(activePlatformId, directoryPath.trim());
      await loadPlatformBaseline(activePlatformId);
    } catch (createError) {
      setBaselineError(createError instanceof Error ? createError.message : "创建平台基线目录失败");
    }
  };

  const handleRenameBaselinePath = async (sourcePath: string) => {
    if (!activePlatformId) {
      return;
    }
    const targetPath = window.prompt("输入新的完整路径", sourcePath);
    if (!targetPath?.trim() || targetPath.trim() === sourcePath) {
      return;
    }
    try {
      setBaselineError("");
      await movePlatformBaselinePath(activePlatformId, sourcePath, targetPath.trim());
      if (selectedBaselinePath === sourcePath) {
        setSelectedBaselinePath(targetPath.trim());
      }
      await loadPlatformBaseline(activePlatformId);
    } catch (moveError) {
      setBaselineError(moveError instanceof Error ? moveError.message : "重命名平台基线路径失败");
    }
  };

  const activePlatform = platforms.find((item) => item.platform_id === activePlatformId) ?? null;
  const sortedBaselineEntries = useMemo(() => {
    return [...baselineEntries].sort((left, right) => {
      if (["input", "skills", "work", "output", "logs"].includes(left.relative_path)) {
        return -1;
      }
      if (["input", "skills", "work", "output", "logs"].includes(right.relative_path)) {
        return 1;
      }
      if (left.kind !== right.kind) {
        return left.kind === "directory" ? -1 : 1;
      }
      return left.relative_path.localeCompare(right.relative_path);
    });
  }, [baselineEntries]);

  const currentDirectoryChildren = useMemo(() => {
    const prefix = currentBaselineDirectory ? `${currentBaselineDirectory}/` : "";
    return baselineEntries
      .filter((item) => {
        if (item.relative_path === currentBaselineDirectory) {
          return false;
        }
        if (!item.relative_path.startsWith(prefix)) {
          return false;
        }
        const rest = item.relative_path.slice(prefix.length);
        return rest.length > 0 && !rest.includes("/");
      })
      .sort((left, right) => {
        if (left.kind !== right.kind) {
          return left.kind === "directory" ? -1 : 1;
        }
        return left.name.localeCompare(right.name);
      });
  }, [baselineEntries, currentBaselineDirectory]);

  return (
    <section className="admin-panel">
      <div className="admin-panel__header">
        <h3>管理配置</h3>
      </div>

      {error ? <div className="admin-panel__error">{error}</div> : null}

      {role === "system_admin" ? (
        <form className="admin-panel__form" onSubmit={handleCreateWhitelist}>
          <h4>管理员白名单</h4>
          <p className="admin-panel__hint">
            填写 W3 账号或工号即可。登录时会按 POC 兼容 `uuid`、`uid`、`employeeNumber` 自动匹配。
          </p>
          <input
            value={providerUserId}
            onChange={(event) => setProviderUserId(event.target.value)}
            placeholder="W3账号或工号"
          />
          <select value={whitelistRole} onChange={(event) => setWhitelistRole(event.target.value)}>
            <option value="platform_admin">平台管理员</option>
            <option value="system_admin">系统管理员</option>
            <option value="debug">Debug 用户</option>
          </select>
          <button type="submit" disabled={!providerUserId.trim()}>
            添加白名单
          </button>
        </form>
      ) : null}

      {role === "system_admin" ? (
        <form className="admin-panel__form" onSubmit={handleCreatePlatform}>
          <h4>平台注册</h4>
          <p className="admin-panel__hint">
            AetherCore 自身已内置一个默认平台，可用于直接对话、debug 调试和平台能力自验证。这里新增的是外部接入平台。
          </p>
          <input
            value={platformKey}
            onChange={(event) => setPlatformKey(event.target.value)}
            placeholder="platform_key，例如 atk-assistant"
          />
          <input
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder="平台显示名称"
          />
          <select value={hostType} onChange={(event) => setHostType(event.target.value)}>
            <option value="embedded">嵌入式平台</option>
            <option value="standalone">独立平台</option>
          </select>
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="平台说明"
          />
          <button type="submit" disabled={!platformKey.trim() || !displayName.trim()}>
            注册平台
          </button>
        </form>
      ) : null}

      <div className="admin-panel__list">
        <h4>平台列表</h4>
        {platforms.length === 0 ? <div className="admin-panel__empty">当前没有可管理的平台。</div> : null}
        {platforms.map((item) => (
          <article key={item.platform_id} className="admin-panel__card">
            <strong>{item.display_name}</strong>
            <p>{item.platform_key}</p>
            <p>{item.host_type}</p>
            <p>{item.description || "未填写平台说明"}</p>
            <p>初始管理员：{item.owner_name}</p>
            <code>{item.host_secret}</code>
            <button type="button" onClick={() => void loadPlatformBaseline(item.platform_id)}>
              {activePlatformId === item.platform_id ? "当前正在编辑基线环境" : "编辑平台基线环境"}
            </button>
          </article>
        ))}
      </div>

      {activePlatform ? (
        <div className="admin-panel__list">
          <h4>平台基线环境</h4>
          <p className="admin-panel__hint">
            当前平台：{activePlatform.display_name}（{activePlatform.platform_key}）。新会话会从这里派生出初始文件、工作目录和预置技能。
          </p>
          {baselineError ? <div className="admin-panel__error">{baselineError}</div> : null}

          <div className="admin-panel__form">
            <h4>平台基线文件管理器</h4>
            <div className="admin-panel__actions">
              <label className="admin-panel__file-button">
                上传文件
                <input
                  type="file"
                  onChange={(event) => void handleBaselineFileUpload(event.target.files?.[0] ?? null)}
                />
              </label>
              <button type="button" onClick={() => void handleCreateBaselineFile()}>
                新建文件
              </button>
              <button type="button" onClick={() => void handleCreateBaselineDirectory()}>
                新建目录
              </button>
            </div>
            <p className="admin-panel__hint">当前目录：{currentBaselineDirectory}</p>

            <div className="admin-panel__file-manager">
              <div className="admin-panel__tree">
                {sortedBaselineEntries.length === 0 ? (
                  <div className="admin-panel__empty">当前没有基线文件或目录。</div>
                ) : null}
                {sortedBaselineEntries.map((item) => {
                  const depth = Math.max(item.relative_path.split("/").length - 1, 0);
                  const fileItem = baselineFiles.find((file) => file.relative_path === item.relative_path);
                  return (
                    <div
                      key={item.relative_path}
                      className={`admin-panel__tree-item ${selectedBaselinePath === item.relative_path || currentBaselineDirectory === item.relative_path ? "is-active" : ""}`}
                      style={{ paddingLeft: `${12 + depth * 16}px` }}
                    >
                      <button
                        type="button"
                        className="admin-panel__tree-main"
                        onClick={() => void handleSelectBaselineEntry(item)}
                      >
                        <span>{item.kind === "directory" ? "[DIR]" : "[FILE]"}</span>
                        <strong>{item.name}</strong>
                        <em>{item.relative_path}</em>
                      </button>
                      <div className="admin-panel__tree-actions">
                        {fileItem ? (
                          <button type="button" onClick={() => void handleDownloadBaselineFile(fileItem)}>
                            下载
                          </button>
                        ) : null}
                        {!["input", "skills", "work", "output", "logs"].includes(item.relative_path) ? (
                          <>
                            <button type="button" onClick={() => void handleRenameBaselinePath(item.relative_path)}>
                              重命名
                            </button>
                            <button type="button" onClick={() => void handleDeleteBaselineFile(item.relative_path)}>
                              删除
                            </button>
                          </>
                        ) : null}
                      </div>
                    </div>
                  );
                })}
              </div>

              <div className="admin-panel__editor">
                <div className="admin-panel__editor-header">
                  <div>
                    <strong>当前目录内容</strong>
                    <p>{currentBaselineDirectory}</p>
                  </div>
                </div>
                <div className="admin-panel__directory-list">
                  {currentDirectoryChildren.length === 0 ? (
                    <div className="admin-panel__empty">当前目录为空。</div>
                  ) : null}
                  {currentDirectoryChildren.map((item) => {
                    const fileItem = baselineFiles.find((file) => file.relative_path === item.relative_path);
                    return (
                      <div key={item.relative_path} className="admin-panel__directory-item">
                        <button
                          type="button"
                          className="admin-panel__directory-main"
                          onClick={() => void handleSelectBaselineEntry(item)}
                        >
                          <span>{item.kind === "directory" ? "[DIR]" : "[FILE]"}</span>
                          <strong>{item.name}</strong>
                          <em>{item.kind === "file" ? `${item.size} bytes` : "目录"}</em>
                        </button>
                        <div className="admin-panel__tree-actions">
                          {fileItem ? (
                            <button type="button" onClick={() => void handleDownloadBaselineFile(fileItem)}>
                              下载
                            </button>
                          ) : null}
                          <button type="button" onClick={() => void handleRenameBaselinePath(item.relative_path)}>
                            重命名
                          </button>
                          <button type="button" onClick={() => void handleDeleteBaselineFile(item.relative_path)}>
                            删除
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>

                <div className="admin-panel__editor-header">
                  <div>
                    <strong>{selectedBaselinePath || "未选择文件"}</strong>
                    <p>
                      {selectedBaselineMediaType || "请选择左侧文本文件进行查看或编辑"}
                      {selectedBaselineTruncated ? " · 已按大小限制截断显示" : ""}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleSaveBaselineText()}
                    disabled={!selectedBaselinePath || !baselineDirty}
                  >
                    保存
                  </button>
                </div>
                <textarea
                  className="admin-panel__editor-textarea"
                  value={selectedBaselineContent}
                  onChange={(event) => {
                    setSelectedBaselineContent(event.target.value);
                    setBaselineDirty(true);
                  }}
                  placeholder="选择一个文本文件后，可在这里直接编辑其内容。"
                  disabled={!selectedBaselinePath}
                />
              </div>
            </div>
          </div>

        </div>
      ) : null}

      {role === "system_admin" ? (
        <div className="admin-panel__list">
          <h4>白名单</h4>
          {whitelist.length === 0 ? <div className="admin-panel__empty">当前没有白名单记录。</div> : null}
          {whitelist.map((item) => (
            <article key={item.whitelist_id} className="admin-panel__card">
              <strong>{item.full_name}</strong>
              <p>{item.role}</p>
              <code>{item.provider}:{item.provider_user_id}</code>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}
