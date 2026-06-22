import type { PlatformSandboxProxyFormState } from "./types";

type PlatformSandboxProxyPanelProps = {
  sandboxProxyForm: PlatformSandboxProxyFormState;
  sandboxProxyError: string;
  sandboxProxyBusy: boolean;
  onChange: (updater: (current: PlatformSandboxProxyFormState) => PlatformSandboxProxyFormState) => void;
  onSave: () => void;
  onReset: () => void;
};

export function PlatformSandboxProxyPanel({
  sandboxProxyForm,
  sandboxProxyError,
  sandboxProxyBusy,
  onChange,
  onSave,
  onReset,
}: PlatformSandboxProxyPanelProps) {
  return (
    <div className="epic-llm-panel">
      <div className="manager-header__info">
        <h4>Sandbox 代理</h4>
        <p>只影响该平台 Docker runtime 的命令执行与出网环境，不影响后端 LLM 请求。</p>
      </div>
      {sandboxProxyError ? <div className="admin-panel__error">{sandboxProxyError}</div> : null}

      <label className="admin-panel__checkbox">
        <input
          type="checkbox"
          checked={sandboxProxyForm.enabled}
          onChange={(e) => onChange((current) => ({ ...current, enabled: e.target.checked }))}
        />
        <span>启用平台专属 sandbox 代理</span>
      </label>

      <label className="admin-panel__checkbox">
        <input
          type="checkbox"
          checked={sandboxProxyForm.inherit_host_proxy}
          onChange={(e) => onChange((current) => ({ ...current, inherit_host_proxy: e.target.checked }))}
        />
        <span>未显式配置时允许回退到宿主默认代理</span>
      </label>

      <input
        value={sandboxProxyForm.http_proxy}
        onChange={(e) => onChange((current) => ({ ...current, http_proxy: e.target.value }))}
        autoComplete="off"
        name="platform-sandbox-http-proxy"
        placeholder="HTTP_PROXY，例如 http://proxy.example.com:7890"
      />
      <input
        value={sandboxProxyForm.https_proxy}
        onChange={(e) => onChange((current) => ({ ...current, https_proxy: e.target.value }))}
        autoComplete="off"
        name="platform-sandbox-https-proxy"
        placeholder="HTTPS_PROXY，例如 http://proxy.example.com:7890"
      />
      <input
        value={sandboxProxyForm.all_proxy}
        onChange={(e) => onChange((current) => ({ ...current, all_proxy: e.target.value }))}
        autoComplete="off"
        name="platform-sandbox-all-proxy"
        placeholder="ALL_PROXY，可选"
      />
      <textarea
        value={sandboxProxyForm.no_proxy}
        onChange={(e) => onChange((current) => ({ ...current, no_proxy: e.target.value }))}
        name="platform-sandbox-no-proxy"
        placeholder={"NO_PROXY，例如\nlocalhost,127.0.0.1,dashscope.aliyuncs.com"}
      />

      {sandboxProxyForm.recycledRuntimeCount !== null ? (
        <div className="platform-runtime-image__hint">
          <strong>最近一次回收：</strong>
          <span>{sandboxProxyForm.recycledRuntimeCount} 个 runtime</span>
        </div>
      ) : null}

      <div className="admin-panel__actions">
        <button type="button" className="action-button primary" onClick={onSave} disabled={sandboxProxyBusy}>
          {sandboxProxyBusy ? "保存中..." : "保存配置"}
        </button>
        <button type="button" className="action-button action-button--ghost danger-button" onClick={onReset} disabled={sandboxProxyBusy}>
          清除覆盖
        </button>
      </div>
    </div>
  );
}
