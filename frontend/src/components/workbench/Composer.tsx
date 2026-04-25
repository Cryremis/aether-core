import { useEffect, useRef, useState } from "react";

import type { QueuedMessage } from "../../pages/workbench/types";
import { WorkbenchIcons as Icons } from "./WorkbenchIcons";

type ComposerProps = {
  busy: boolean;
  disabled: boolean;
  allowNetwork: boolean;
  queuedMessages: QueuedMessage[];
  onAllowNetworkChange: (value: boolean) => void;
  onSend: (text: string) => void;
  onStop: () => void;
  onRemoveQueued: (id: string) => void;
  onUpload: (file: File) => void;
};

function QueuedMessagesDock({ messages, onRemove }: { messages: QueuedMessage[]; onRemove: (id: string) => void }) {
  if (messages.length === 0) return null;
  return (
    <div className="queued-messages-dock">
      <span className="queued-messages-dock__count">{messages.length} 条消息已排队</span>
      {messages.map((msg) => (
        <div key={msg.id} className="queued-message-item">
          <span className="queued-message-item__content">{msg.content}</span>
          <button type="button" className="queued-message-item__remove" onClick={() => onRemove(msg.id)}>
            <Icons.Close />
          </button>
        </div>
      ))}
    </div>
  );
}

export function Composer({
  busy,
  disabled,
  allowNetwork,
  queuedMessages,
  onAllowNetworkChange,
  onSend,
  onStop,
  onRemoveQueued,
  onUpload,
}: ComposerProps) {
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!textareaRef.current) return;
    textareaRef.current.style.height = "auto";
    textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
  }, [input]);

  const canSend = input.trim().length > 0 && !disabled;

  const handleSend = () => {
    if (!canSend) return;
    onSend(input.trim());
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  return (
    <div className="composer-area">
      <QueuedMessagesDock messages={queuedMessages} onRemove={onRemoveQueued} />
      <div className="composer-box">
        <textarea
          ref={textareaRef}
          className="composer-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder={busy ? "输入消息将在工具执行后发送..." : "发送指令，与系统深度交互..."}
          rows={1}
        />
        <div className="composer-actions">
          <div className="composer-actions__left">
            <label className="icon-button attach-btn" title="上传文件">
              <input
                type="file"
                onChange={(e) => {
                  if (e.target.files?.[0]) onUpload(e.target.files[0]);
                  e.currentTarget.value = "";
                }}
              />
              <Icons.Attach />
            </label>
            <button
              type="button"
              className={`network-toggle ${allowNetwork ? "active" : ""}`}
              onClick={() => onAllowNetworkChange(!allowNetwork)}
              aria-pressed={allowNetwork}
              title={allowNetwork ? "当前会话已开启联网搜索" : "当前会话未开启联网搜索"}
            >
              <Icons.Globe />
              <span>联网搜索</span>
            </button>
          </div>
          <div className="composer-actions__right">
            <button className={`icon-button send-btn ${canSend ? "active" : ""}`} disabled={!canSend} onClick={handleSend} title="发送">
              <Icons.Send />
            </button>
            {busy ? (
              <button className="icon-button stop-btn" onClick={onStop} title="停止">
                <Icons.Stop />
              </button>
            ) : null}
          </div>
        </div>
      </div>
      <div className="composer-footer">AetherCore System · Advanced Mode</div>
    </div>
  );
}
