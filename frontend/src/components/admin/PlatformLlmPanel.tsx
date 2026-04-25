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
    <div className="admin-panel__form admin-panel__form--llm">
      <h4>平台默认 LLM</h4>
      <p className="admin-panel__hint">这里配置该平台的新会话默认使用的 LiteLLM / OpenAI 兼容入口。终端用户如配置了个人 LLM，会优先覆盖这里。</p>
      {platformLlmError ? <div className="admin-panel__error">{platformLlmError}</div> : null}
      <label className="admin-panel__checkbox">
        <input type="checkbox" checked={platformLlmForm.enabled} onChange={(e) => onChange((current) => ({ ...current, enabled: e.target.checked }))} />
        <span>启用平台默认 LLM</span>
      </label>
      <input value={platformLlmForm.base_url} onChange={(e) => onChange((current) => ({ ...current, base_url: e.target.value }))} autoComplete="off" name="platform-llm-base-url" placeholder="LiteLLM 或内网 OpenAI 兼容服务地址，例如 http://litellm.internal:4000/v1" />
      <input value={platformLlmForm.model} onChange={(e) => onChange((current) => ({ ...current, model: e.target.value }))} autoComplete="off" name="platform-llm-model-id" placeholder="模型 ID，例如 glm-4.5 / minimax-m1 / gpt-4o-mini" />
      <input type="password" value={platformLlmForm.api_key} onChange={(e) => onChange((current) => ({ ...current, api_key: e.target.value }))} autoComplete="new-password" name="platform-llm-api-key" placeholder={platformLlmForm.has_api_key ? "已存在密钥，留空则保持不变" : "API Key"} />
      <details className="llm-advanced-panel" open={showPlatformLlmAdvanced} onToggle={(e) => onToggleAdvanced((e.currentTarget as HTMLDetailsElement).open)}>
        <summary>高级参数</summary>
        <p className="admin-panel__hint">仅在代理网关、租户透传或内网兼容服务需要补充额外 headers/body 时填写。留空即可。</p>
        <textarea value={platformLlmForm.extra_headers_text} onChange={(e) => onChange((current) => ({ ...current, extra_headers_text: e.target.value }))} autoComplete="off" name="platform-llm-extra-headers" placeholder='额外请求头 JSON，例如 {"x-foo":"bar"}' />
        <textarea value={platformLlmForm.extra_body_text} onChange={(e) => onChange((current) => ({ ...current, extra_body_text: e.target.value }))} autoComplete="off" name="platform-llm-extra-body" placeholder='额外请求体 JSON，例如 {"reasoning":{"effort":"medium"}}' />
      </details>
      <details className="llm-advanced-panel">
        <summary>联网策略</summary>
        <label className="admin-panel__checkbox">
          <input type="checkbox" checked={platformLlmForm.network_enabled} onChange={(e) => onChange((current) => ({ ...current, network_enabled: e.target.checked }))} />
          <span>启用联网工具</span>
        </label>
        <p className="admin-panel__hint">系统只使用模型原生联网搜索能力；这里保留的是平台治理策略，不再要求额外配置搜索服务。</p>
        <label className="admin-panel__field">
          <span>允许访问域名</span>
          <textarea value={platformLlmForm.allowed_domains_text} onChange={(e) => onChange((current) => ({ ...current, allowed_domains_text: e.target.value }))} placeholder={"每行一个\nexample.com"} />
        </label>
        <label className="admin-panel__field">
          <span>禁止访问域名</span>
          <textarea value={platformLlmForm.blocked_domains_text} onChange={(e) => onChange((current) => ({ ...current, blocked_domains_text: e.target.value }))} placeholder={"每行一个\ninternal.example.com"} />
        </label>
        <label className="admin-panel__field">
          <span>最大搜索结果数</span>
          <input type="number" min={1} max={20} value={platformLlmForm.max_search_results} onChange={(e) => onChange((current) => ({ ...current, max_search_results: Number(e.target.value || 8) }))} />
        </label>
        <label className="admin-panel__field">
          <span>网页抓取超时（秒）</span>
          <input type="number" min={1} max={120} value={platformLlmForm.fetch_timeout_seconds} onChange={(e) => onChange((current) => ({ ...current, fetch_timeout_seconds: Number(e.target.value || 30) }))} />
        </label>
      </details>
      <div className="admin-panel__actions">
        <button type="button" className="action-button" onClick={onSave} disabled={platformLlmBusy || !platformLlmForm.base_url.trim() || !platformLlmForm.model.trim()}>
          {platformLlmBusy ? "保存中..." : "保存平台 LLM"}
        </button>
        <button type="button" className="action-button action-button--ghost" onClick={onReset} disabled={platformLlmBusy}>
          清除覆盖
        </button>
      </div>
    </div>
  );
}
