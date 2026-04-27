import type { FormEvent } from "react";

type AdminFormsProps = {
  platformKey: string;
  displayName: string;
  description: string;
  onPlatformKeyChange: (value: string) => void;
  onDisplayNameChange: (value: string) => void;
  onDescriptionChange: (value: string) => void;
  onCreatePlatform: (event: FormEvent) => void;
};

export function AdminForms(props: AdminFormsProps) {
  return (
    <div className="admin-grid-forms">
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
