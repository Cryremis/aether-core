import type { FormEvent } from "react";

import type { AdminWhitelistRole } from "./types";

type AdminFormsProps = {
  providerKey: string;
  providerUserId: string;
  whitelistRole: AdminWhitelistRole;
  platformKey: string;
  displayName: string;
  description: string;
  onProviderKeyChange: (value: string) => void;
  onProviderUserIdChange: (value: string) => void;
  onWhitelistRoleChange: (value: AdminWhitelistRole) => void;
  onPlatformKeyChange: (value: string) => void;
  onDisplayNameChange: (value: string) => void;
  onDescriptionChange: (value: string) => void;
  onCreateWhitelist: (event: FormEvent) => void;
  onCreatePlatform: (event: FormEvent) => void;
};

export function AdminForms(props: AdminFormsProps) {
  return (
    <div className="admin-grid-forms">
      <form className="admin-panel__form" onSubmit={props.onCreateWhitelist}>
        <h4>管理员白名单</h4>
        <p className="admin-panel__hint">按 provider key + 用户标识录入，例如 `password`、`corp-sso`、`github`。</p>
        <input value={props.providerKey} onChange={(e) => props.onProviderKeyChange(e.target.value)} placeholder="provider key，例如 github" />
        <input value={props.providerUserId} onChange={(e) => props.onProviderUserIdChange(e.target.value)} placeholder="用户唯一标识、工号或账号" />
        <select value={props.whitelistRole} onChange={(e) => props.onWhitelistRoleChange(e.target.value as AdminWhitelistRole)}>
          <option value="platform_admin">平台管理员</option>
          <option value="system_admin">系统管理员</option>
          <option value="debug">Debug 用户</option>
        </select>
        <button type="submit" className="action-button w-full" disabled={!props.providerKey.trim() || !props.providerUserId.trim()}>添加白名单</button>
      </form>
      <form className="admin-panel__form" onSubmit={props.onCreatePlatform}>
        <h4>注册新接入平台</h4>
        <p className="admin-panel__hint">这里只注册外部接入平台。AetherCore 自身作为内置平台自动存在，无需手动创建。</p>
        <input value={props.platformKey} onChange={(e) => props.onPlatformKeyChange(e.target.value)} placeholder="platform_key，例如 atk-assistant" />
        <input value={props.displayName} onChange={(e) => props.onDisplayNameChange(e.target.value)} placeholder="平台显示名称" />
        <textarea value={props.description} onChange={(e) => props.onDescriptionChange(e.target.value)} placeholder="平台说明" />
        <button type="submit" className="action-button w-full" disabled={!props.platformKey.trim() || !props.displayName.trim()}>注册平台</button>
      </form>
    </div>
  );
}
