// frontend/src/components/AdminPanel.tsx
import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  createAdminWhitelist,
  createPlatform,
  deletePlatformBaselineFile,
  deletePlatformBaselineSkill,
  downloadPlatformBaselineFile,
  getPlatformBaseline,
  listAdminWhitelist,
  listPlatforms,
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
  section: "input" | "work";
  size: number;
  media_type: string;
};

type PlatformBaselineSkillItem = {
  name: string;
  description: string;
  allowed_tools: string[];
  tags: string[];
  relative_path: string;
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
  const [baselineSkills, setBaselineSkills] = useState<PlatformBaselineSkillItem[]>([]);
  const [error, setError] = useState("");
  const [baselineError, setBaselineError] = useState("");
  const [baselineSection, setBaselineSection] = useState<"input" | "work">("input");

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
        skills?: PlatformBaselineSkillItem[];
      };
      setBaselineFiles(data.files ?? []);
      setBaselineSkills(data.skills ?? []);
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
      setBaselineSkills([]);
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
      await uploadPlatformBaselineFile(activePlatformId, baselineSection, file);
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
      await loadPlatformBaseline(activePlatformId);
    } catch (deleteError) {
      setBaselineError(deleteError instanceof Error ? deleteError.message : "删除平台基线文件失败");
    }
  };

  const handleDeleteBaselineSkill = async (skillName: string) => {
    if (!activePlatformId) {
      return;
    }
    try {
      setBaselineError("");
      await deletePlatformBaselineSkill(activePlatformId, skillName);
      await loadPlatformBaseline(activePlatformId);
    } catch (deleteError) {
      setBaselineError(deleteError instanceof Error ? deleteError.message : "删除平台基线技能失败");
    }
  };

  const activePlatform = platforms.find((item) => item.platform_id === activePlatformId) ?? null;

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
            <h4>基线文件</h4>
            <select
              value={baselineSection}
              onChange={(event) => setBaselineSection(event.target.value as "input" | "work")}
            >
              <option value="input">input 参考资料区</option>
              <option value="work">work 工作区</option>
            </select>
            <label className="admin-panel__file-button">
              上传文件
              <input
                type="file"
                onChange={(event) => void handleBaselineFileUpload(event.target.files?.[0] ?? null)}
              />
            </label>
            {baselineFiles.length === 0 ? <div className="admin-panel__empty">当前没有基线文件。</div> : null}
            {baselineFiles.map((item) => (
              <article key={item.relative_path} className="admin-panel__card">
                <strong>{item.name}</strong>
                <p>{item.relative_path}</p>
                <p>{item.section} · {item.size} bytes</p>
                <div className="admin-panel__actions">
                  <button type="button" onClick={() => void handleDownloadBaselineFile(item)}>
                    下载
                  </button>
                  <button type="button" onClick={() => void handleDeleteBaselineFile(item.relative_path)}>
                    删除
                  </button>
                </div>
              </article>
            ))}
          </div>

          <div className="admin-panel__form">
            <h4>基线技能</h4>
            <label className="admin-panel__file-button">
              上传技能包
              <input
                type="file"
                accept=".zip,.md"
                onChange={(event) => void handleBaselineSkillUpload(event.target.files?.[0] ?? null)}
              />
            </label>
            {baselineSkills.length === 0 ? <div className="admin-panel__empty">当前没有基线技能。</div> : null}
            {baselineSkills.map((item) => (
              <article key={item.relative_path} className="admin-panel__card">
                <strong>{item.name}</strong>
                <p>{item.description}</p>
                <p>{item.relative_path}</p>
                <div className="admin-panel__actions">
                  <button type="button" onClick={() => void handleDeleteBaselineSkill(item.name)}>
                    删除
                  </button>
                </div>
              </article>
            ))}
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
