import type { LlmConfigFormState } from "./types";

type PlatformLlmPanelProps = {
  platformLlmForm: LlmConfigFormState;
  platformLlmError: string;
  platformLlmBusy: boolean;
  showPlatformLlmAdvanced: boolean;
  onToggleAdvanced: (open: boolean) => void;
  onChange: (updater: (current: LlmConfigFormState) => LlmConfigFormState) => void;
  onSave: () => void;
  onReset: () => void;
};

export function PlatformLlmPanel({
  platformLlmForm,
  platformLlmError,
  platformLlmBusy,
  showPlatformLlmAdvanced,
  onToggleAdvanced,
  onChange,
  onSave,
  onReset,
}: PlatformLlmPanelProps) {
  return (
    <div className="epic-llm-panel">
      <div className="manager-header__info">
        <h4>平台默认 LLM</h4>
        <p>配置该平台的新会话默认使用的模型服务入口。</p>
      </div>
      {platformLlmError ? <div className="admin-panel__error">{platformLlmError}</div> : null}
      <label className="admin-panel__checkbox">
        <input type="checkbox" checked={platformLlmForm.enabled} onChange={(e) => onChange((current) => ({ ...current, enabled: e.target.checked }))} />
        <span>启用平台默认 LLM 覆盖</span>
      </label>
      <input value={platformLlmForm.base_url} onChange={(e) => onChange((current) => ({ ...current, base_url: e.target.value }))} autoComplete="off" name="platform-llm-base-url" placeholder="LiteLLM/OpenAI 服务地址" />
      <input value={platformLlmForm.model} onChange={(e) => onChange((current) => ({ ...current, model: e.target.value }))} autoComplete="off" name="platform-llm-model-id" placeholder="模型 ID (如 gpt-4o)" />
      <input type="password" value={platformLlmForm.api_key} onChange={(e) => onChange((current) => ({ ...current, api_key: e.target.value }))} autoComplete="new-password" name="platform-llm-api-key" placeholder={platformLlmForm.has_api_key ? "已存在密钥，留空则保持不变" : "API Key"} />
      <details className="llm-advanced-panel" open={showPlatformLlmAdvanced} onToggle={(e) => onToggleAdvanced((e.currentTarget as HTMLDetailsElement).open)}>
        <summary>高级调度参数</summary>
        <p className="llm-advanced-panel__hint">扩展请求头与结构体将直接透传至模型底层，留空即默认。</p>
        <textarea value={platformLlmForm.extra_headers_text} onChange={(e) => onChange((current) => ({ ...current, extra_headers_text: e.target.value }))} autoComplete="off" name="platform-llm-extra-headers" placeholder='额外请求头 JSON，例如 {"x-foo":"bar"}' />
        <textarea value={platformLlmForm.extra_body_text} onChange={(e) => onChange((current) => ({ ...current, extra_body_text: e.target.value }))} autoComplete="off" name="platform-llm-extra-body" placeholder='额外请求体 JSON，例如 {"reasoning":{"effort":"medium"}}' />
      </details>
      <details className="llm-advanced-panel">
        <summary>网络检索策略</summary>
        <label className="admin-panel__checkbox" style={{ marginTop: "8px", marginBottom: "8px" }}>
          <input type="checkbox" checked={platformLlmForm.network_enabled} onChange={(e) => onChange((current) => ({ ...current, network_enabled: e.target.checked }))} />
          <span>允许此平台会话开启联网搜索</span>
        </label>
        <label className="admin-panel__field">
          <span>允许访问域名 (白名单)</span>
          <textarea value={platformLlmForm.allowed_domains_text} onChange={(e) => onChange((current) => ({ ...current, allowed_domains_text: e.target.value }))} placeholder={"每行一个\nexample.com"} />
        </label>
        <label className="admin-panel__field">
          <span>禁止访问域名 (黑名单)</span>
          <textarea value={platformLlmForm.blocked_domains_text} onChange={(e) => onChange((current) => ({ ...current, blocked_domains_text: e.target.value }))} placeholder={"每行一个\ninternal.example.com"} />
        </label>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginTop: "4px" }}>
          <label className="admin-panel__field">
            <span>最大搜索结果</span>
            <input type="number" min={1} max={20} value={platformLlmForm.max_search_results} onChange={(e) => onChange((current) => ({ ...current, max_search_results: Number(e.target.value || 8) }))} />
          </label>
          <label className="admin-panel__field">
            <span>抓取超时 (秒)</span>
            <input type="number" min={1} max={120} value={platformLlmForm.fetch_timeout_seconds} onChange={(e) => onChange((current) => ({ ...current, fetch_timeout_seconds: Number(e.target.value || 30) }))} />
          </label>
        </div>
      </details>
      <div className="admin-panel__actions">
        <button type="button" className="action-button primary" onClick={onSave} disabled={platformLlmBusy || !platformLlmForm.base_url.trim() || !platformLlmForm.model.trim()}>
          {platformLlmBusy ? "保存中..." : "保存配置"}
        </button>
        <button type="button" className="action-button action-button--ghost danger-button" onClick={onReset} disabled={platformLlmBusy}>
          清除覆盖
        </button>
      </div>
    </div>
  );
}
