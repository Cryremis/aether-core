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
      <div className="llm-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="llm-dialog__header">
          <div>
            <h3>个人 LLM 配置</h3>
            <p>当前生效来源：{llmState.resolved_scope === "user" ? "个人覆盖" : llmState.resolved_scope === "platform" ? "平台默认" : "全局默认"}</p>
          </div>
          <button type="button" className="icon-button subtle" onClick={onClose}>×</button>
        </div>
        {llmError ? <div className="error-toast anim-shake">{llmError}</div> : null}
        <div className="llm-dialog__body">
          <label className="admin-panel__checkbox">
            <input type="checkbox" checked={llmState.enabled} onChange={(e) => onChange((current) => ({ ...current, enabled: e.target.checked }))} />
            <span>启用个人 LLM 覆盖</span>
          </label>
          <input className="composer-input llm-input" value={llmState.base_url} onChange={(e) => onChange((current) => ({ ...current, base_url: e.target.value }))} autoComplete="off" name="llm-base-url" placeholder="LiteLLM 或内网 OpenAI 兼容服务地址" />
          <input className="composer-input llm-input" value={llmState.model} onChange={(e) => onChange((current) => ({ ...current, model: e.target.value }))} autoComplete="off" name="llm-model-id" placeholder="模型 ID" />
          <input className="composer-input llm-input" type="password" value={llmState.api_key} onChange={(e) => onChange((current) => ({ ...current, api_key: e.target.value }))} autoComplete="new-password" name="llm-api-key" placeholder={llmState.has_api_key ? "已存在密钥，留空则保持不变" : "API Key"} />
          <details className="llm-advanced-panel" open={showAdvancedLlmFields} onToggle={(e) => onToggleAdvanced((e.currentTarget as HTMLDetailsElement).open)}>
            <summary>高级参数</summary>
            <p className="llm-advanced-panel__hint">仅在对接 LiteLLM、代理网关或内网兼容服务需要额外请求头、额外请求体时填写。留空即可。</p>
            <textarea className="composer-input llm-textarea" value={llmState.extra_headers_text} onChange={(e) => onChange((current) => ({ ...current, extra_headers_text: e.target.value }))} autoComplete="off" name="llm-extra-headers" placeholder='额外请求头 JSON，例如 {"x-tenant":"demo"}' />
            <textarea className="composer-input llm-textarea" value={llmState.extra_body_text} onChange={(e) => onChange((current) => ({ ...current, extra_body_text: e.target.value }))} autoComplete="off" name="llm-extra-body" placeholder='额外请求体 JSON，例如 {"reasoning":{"effort":"medium"}}' />
          </details>
        </div>
        <div className="llm-dialog__footer">
          <button type="button" className="action-button sidebar-footer__button sidebar-footer__button--ghost" onClick={onReset} disabled={llmBusy}>
            清除覆盖
          </button>
          <button type="button" className="action-button sidebar-footer__button" onClick={onSave} disabled={llmBusy || !llmState.base_url.trim() || !llmState.model.trim()}>
            {llmBusy ? "保存中..." : "保存个人 LLM"}
          </button>
        </div>
      </div>
    </div>
  );
}
