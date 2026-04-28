import { useState } from "react";
import { createPortal } from "react-dom";
import type { PromptConfigFormState } from "./types";

const VAR_GROUPS = [
  {
    title: "平台信息",
    vars: [
      { name: "platform.display_name", desc: "平台的显示名称" },
      { name: "platform.platform_key", desc: "平台的唯一标识" },
    ],
  },
  {
    title: "接入方上下文",
    vars: [
      { name: "host.name", desc: "接入方名称" },
      { name: "host.user.id", desc: "用户ID（接入方传入）" },
      { name: "host.user.name", desc: "用户名（接入方传入）" },
      { name: "host.page.pathname", desc: "页面路径（接入方传入）" },
      { name: "host.page.url", desc: "页面URL（接入方传入）" },
      { name: "host.extras.*", desc: "自定义字段（接入方传入）" },
    ],
  },
  {
    title: "会话信息",
    vars: [
      { name: "conversation.id", desc: "会话ID" },
      { name: "conversation.session_id", desc: "Session ID" },
    ],
  },
  {
    title: "工作区路径",
    vars: [
      { name: "workspace.input_dir", desc: "输入目录" },
      { name: "workspace.skills_dir", desc: "技能目录" },
      { name: "workspace.work_dir", desc: "工作目录" },
      { name: "workspace.output_dir", desc: "输出目录" },
    ],
  },
];

type PlatformPromptPanelProps = {
  promptForm: PromptConfigFormState;
  promptError: string;
  promptBusy: boolean;
  onChange: (updater: (current: PromptConfigFormState) => PromptConfigFormState) => void;
  onSave: () => void;
  onReset: () => void;
};

export function PlatformPromptPanel({
  promptForm,
  promptError,
  promptBusy,
  onChange,
  onSave,
  onReset,
}: PlatformPromptPanelProps) {
  const [showVarModal, setShowVarModal] = useState(false);
  const canRenderPortal = typeof document !== "undefined";

  return (
    <div className="admin-panel__form admin-panel__form--llm">
      <h4>平台系统提示词</h4>
      <p className="admin-panel__hint">
        配置该平台默认附带的系统提示词，可用变量：
        <code className="var-code">
          {"{{platform.display_name}}"}
          <sup className="var-tip" title="平台的显示名称">?</sup>
        </code>
        <code className="var-code">
          {"{{host.user.id}}"}
          <sup className="var-tip" title="接入方传入的用户ID">?</sup>
        </code>
        <code className="var-code">
          {"{{host.page.pathname}}"}
          <sup className="var-tip" title="接入方传入的页面路径">?</sup>
        </code>
        <code className="var-code">
          {"{{workspace.work_dir}}"}
          <sup className="var-tip" title="工作目录路径">?</sup>
        </code>
        等。<button type="button" className="var-all-btn" onClick={() => setShowVarModal(true)}>查看全部</button>
      </p>
      {promptError ? <div className="admin-panel__error">{promptError}</div> : null}
      <label className="admin-panel__checkbox">
        <input type="checkbox" checked={promptForm.enabled} onChange={(e) => onChange((current) => ({ ...current, enabled: e.target.checked }))} />
        <span>启用平台系统提示词</span>
      </label>
      <label className="admin-panel__field">
        <span>提示词内容</span>
        <textarea
          value={promptForm.system_prompt}
          onChange={(e) => onChange((current) => ({ ...current, system_prompt: e.target.value }))}
          name="platform-system-prompt"
          rows={12}
          placeholder={"例如：你是 {{platform.display_name}} 的企业智能助手。"}
        />
      </label>
      <div className="admin-panel__actions">
        <button type="button" className="action-button" onClick={onSave} disabled={promptBusy}>
          {promptBusy ? "保存中..." : "保存平台提示词"}
        </button>
        <button type="button" className="action-button action-button--ghost" onClick={onReset} disabled={promptBusy}>
          清除覆盖
        </button>
      </div>

      {showVarModal && canRenderPortal && createPortal(
        <div className="var-modal-backdrop" onClick={() => setShowVarModal(false)}>
          <div className="var-modal" onClick={(e) => e.stopPropagation()}>
            <div className="var-modal__header">
              <h5>可用变量列表</h5>
              <button type="button" className="var-modal__close" onClick={() => setShowVarModal(false)}>×</button>
            </div>
            <div className="var-modal__body">
              {VAR_GROUPS.map((group) => (
                <div className="var-group" key={group.title}>
                  <h6>{group.title}</h6>
                  <div className="var-group__list">
                    {group.vars.map((v) => (
                      <div className="var-item-full" key={v.name}>
                        <code>{"{{" + v.name + "}}"}</code>
                        <span>{v.desc}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
