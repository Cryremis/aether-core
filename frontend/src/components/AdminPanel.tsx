// frontend/src/components/AdminPanel.tsx
import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  createAdminWhitelist,
  createPlatform,
  listAdminWhitelist,
  listPlatforms,
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
  const [error, setError] = useState("");

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

  useEffect(() => {
    void loadData();
  }, [role]);

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
          </article>
        ))}
      </div>

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
