import type { LlmDialogState } from "../../pages/workbench/types";

type LlmConfigDialogProps = {
  open: boolean;
  llmBusy: boolean;
  llmError: string;
  llmState: LlmDialogState;
  showAdvancedLlmFields: boolean;
  onClose: () => void;
  onReset: () => void;
  onSave: () => void;
  onToggleAdvanced: (open: boolean) => void;
  onChange: (updater: (current: LlmDialogState) => LlmDialogState) => void;
};

export function LlmConfigDialog({
  open,
  llmBusy,
  llmError,
  llmState,
  showAdvancedLlmFields,
  onClose,
  onReset,
  onSave,
  onToggleAdvanced,
  onChange,
}: LlmConfigDialogProps) {
  if (!open) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="llm-dialog settings-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="settings-dialog__header">
          <div>
            <h3>模型配置</h3>
            <p style={{ fontSize: "12px", color: "var(--text-tertiary)", marginTop: "2px" }}>
              {llmState.resolved_scope === "user" ? "个人覆盖" : llmState.resolved_scope === "platform" ? "平台默认" : "全局默认"}
            </p>
          </div>
          <button type="button" className="icon-button subtle" onClick={onClose}>×</button>
        </div>
        {llmError ? <div className="error-toast anim-shake" style={{ margin: "0 24px", marginTop: "12px" }}>{llmError}</div> : null}
        <div className="llm-dialog__body" style={{ padding: "16px 24px" }}>
          <div className="settings-row" style={{ padding: "0", border: "none" }}>
            <span className="settings-row__label">启用个人 LLM 覆盖</span>
            <button
              type="button"
              className={`toggle-switch ${llmState.enabled ? "on" : ""}`}
              onClick={() => onChange((current) => ({ ...current, enabled: !current.enabled }))}
              aria-pressed={llmState.enabled}
            >
              <span className="toggle-switch__thumb" />
            </button>
          </div>
          <input className="settings-input" value={llmState.base_url} onChange={(e) => onChange((current) => ({ ...current, base_url: e.target.value }))} autoComplete="off" name="llm-base-url" placeholder="LiteLLM 或内网 OpenAI 兼容服务地址" />
          <input className="settings-input" value={llmState.model} onChange={(e) => onChange((current) => ({ ...current, model: e.target.value }))} autoComplete="off" name="llm-model-id" placeholder="模型 ID" />
          <input className="settings-input" type="password" value={llmState.api_key} onChange={(e) => onChange((current) => ({ ...current, api_key: e.target.value }))} autoComplete="new-password" name="llm-api-key" placeholder={llmState.has_api_key ? "已存在密钥，留空则保持不变" : "API Key"} />
          <details className="llm-advanced-panel">
            <summary>采样参数</summary>
            <p className="llm-advanced-panel__hint">调整温度与重复惩罚。留空表示继承上层（平台→全局）默认值。</p>
            <div className="llm-sampling-grid">
              <label className="admin-panel__field"><span>Temperature</span><input className="settings-input" type="number" step="0.1" min="0" max="2" value={llmState.sampling_temperature} onChange={(e) => onChange((current) => ({ ...current, sampling_temperature: e.target.value }))} placeholder="继承" /></label>
              <label className="admin-panel__field"><span>Frequency Penalty</span><input className="settings-input" type="number" step="0.1" min="-2" max="2" value={llmState.sampling_frequency_penalty} onChange={(e) => onChange((current) => ({ ...current, sampling_frequency_penalty: e.target.value }))} placeholder="继承" /></label>
              <label className="admin-panel__field"><span>Presence Penalty</span><input className="settings-input" type="number" step="0.1" min="-2" max="2" value={llmState.sampling_presence_penalty} onChange={(e) => onChange((current) => ({ ...current, sampling_presence_penalty: e.target.value }))} placeholder="继承" /></label>
              <label className="admin-panel__field"><span>Top-P</span><input className="settings-input" type="number" step="0.05" min="0" max="1" value={llmState.sampling_top_p} onChange={(e) => onChange((current) => ({ ...current, sampling_top_p: e.target.value }))} placeholder="继承" /></label>
              <label className="admin-panel__field"><span>Repetition Penalty</span><input className="settings-input" type="number" step="0.05" min="1" max="2" value={llmState.sampling_repetition_penalty} onChange={(e) => onChange((current) => ({ ...current, sampling_repetition_penalty: e.target.value }))} placeholder="继承" /></label>
            </div>
          </details>
          <details className="llm-advanced-panel">
            <summary>高级参数</summary>
            <p className="llm-advanced-panel__hint">仅在对接 LiteLLM、代理网关或内网兼容服务需要额外请求头、额外请求体时填写。留空即可。</p>
            <textarea className="composer-input llm-textarea settings-input" value={llmState.extra_headers_text} onChange={(e) => onChange((current) => ({ ...current, extra_headers_text: e.target.value }))} autoComplete="off" name="llm-extra-headers" placeholder='额外请求头 JSON，例如 {"x-tenant":"demo"}' />
            <textarea className="composer-input llm-textarea settings-input" value={llmState.extra_body_text} onChange={(e) => onChange((current) => ({ ...current, extra_body_text: e.target.value }))} autoComplete="off" name="llm-extra-body" placeholder='额外请求体 JSON，例如 {"reasoning":{"effort":"medium"}}' />
          </details>
        </div>
        <div className="settings-dialog__footer">
          <button type="button" className="danger-ghost-btn" onClick={onReset} disabled={llmBusy}>
            清除覆盖
          </button>
          <button type="button" className="action-button" style={{ borderRadius: "10px", padding: "8px 20px", fontSize: "13px", fontWeight: 500 }} onClick={onSave} disabled={llmBusy || !llmState.base_url.trim() || !llmState.model.trim()}>
            {llmBusy ? "保存中..." : "保存配置"}
          </button>
        </div>
      </div>
    </div>
  );
}
