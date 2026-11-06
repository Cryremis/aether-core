import { useEffect, useRef, useState } from "react";

import type { QueuedMessage } from "../../pages/workbench/types";
import { parseWorkbenchCommand, workbenchCommands, type CommandAvailability, type WorkbenchCommand } from "../../pages/workbench/commands";
import type { SubagentRunSummary } from "../../api/client";
import { SubagentPopover } from "./SubagentPopover";
import { WorkbenchIcons as Icons } from "./WorkbenchIcons";

type ComposerProps = {
  busy: boolean;
  canStop: boolean;
  stopping: boolean;
  disabled: boolean;
  allowNetwork: boolean;
  queuedMessages: QueuedMessage[];
  workboardVisible: boolean;
  workboardCount: number;
  workboardCompleted: number;
  subagents: SubagentRunSummary[];
  subagentPopoverOpen: boolean;
  activeChildSessionId?: string | null;
  readOnlySubagentView: boolean;
  reasoningEffort: string;
  reasoningEffortOptions: string[];
  onAllowNetworkChange: (value: boolean) => void;
  onReasoningEffortChange: (value: string) => void;
  onWorkboardToggle: () => void;
  onSubagentPopoverOpenChange: (open: boolean) => void;
  onOpenSubagent: (subagent: SubagentRunSummary) => void;
  onStopSubagent: (subagent: SubagentRunSummary) => void;
  onDestroySubagent: (subagent: SubagentRunSummary) => void;
  onExitSubagent: () => void;
  onSend: (text: string) => void | boolean | Promise<void | boolean>;
  onCommand?: (name: WorkbenchCommand) => void;
  commandAvailability?: CommandAvailability;
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
  canStop,
  stopping,
  disabled,
  allowNetwork,
  queuedMessages,
  workboardVisible,
  workboardCount,
  workboardCompleted,
  subagents,
  subagentPopoverOpen,
  activeChildSessionId,
  readOnlySubagentView,
  reasoningEffort,
  reasoningEffortOptions,
  onAllowNetworkChange,
  onReasoningEffortChange,
  onWorkboardToggle,
  onSubagentPopoverOpenChange,
  onOpenSubagent,
  onStopSubagent,
  onDestroySubagent,
  onExitSubagent,
  onSend,
  onCommand,
  commandAvailability,
  onStop,
  onRemoveQueued,
  onUpload,
}: ComposerProps) {
  const [input, setInput] = useState("");
  const [menuDismissed, setMenuDismissed] = useState(false);
  const [commandIndex, setCommandIndex] = useState(-1);
  const [commandError, setCommandError] = useState("");
  const [effortOpen, setEffortOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const effortRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!textareaRef.current) return;
    textareaRef.current.style.height = "auto";
    textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
  }, [input]);

  useEffect(() => {
    if (!effortOpen) return;
    const handler = (e: MouseEvent) => {
      if (effortRef.current && !effortRef.current.contains(e.target as Node)) setEffortOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [effortOpen]);

  const trimmed = input.trim();
  const typedCommand = onCommand ? parseWorkbenchCommand(trimmed) : null;
  const matches = onCommand && !menuDismissed && /^\/[a-z]*$/i.test(trimmed)
    ? workbenchCommands.filter(({ name }) => `/${name}`.startsWith(trimmed.toLowerCase()))
    : [];
  const canSend = !!trimmed && trimmed !== "/" && (!disabled || !!typedCommand);

  const updateInput = (text: string) => {
    setInput(text);
    setMenuDismissed(false);
    setCommandIndex(-1);
    setCommandError("");
  };

  const runCommand = (name: WorkbenchCommand, hasArguments = false) => {
    const unavailable = commandAvailability?.[name];
    const error = hasArguments ? `请单独输入 /${name}` : unavailable;
    if (error) {
      setCommandError(error);
      return;
    }
    updateInput(name === "help" ? "/" : "");
    if (name === "help") textareaRef.current?.focus();
    else onCommand?.(name);
  };

  const handleSend = () => {
    if (typedCommand) {
      runCommand(typedCommand.name, typedCommand.hasArguments);
      return;
    }
    if (!canSend) return;
    const submitted = trimmed;
    updateInput("");
    void Promise.resolve(onSend(submitted)).then((accepted) => {
      if (accepted === false) setInput((current) => current ? `${submitted}\n\n${current}` : submitted);
    });
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  const handleFiles = (files: FileList | File[]) => {
    Array.from(files).forEach((f) => onUpload(f));
  };

  const workboardAllDone = workboardCount > 0 && workboardCompleted === workboardCount;
  const workboardPercent = workboardCount > 0 ? Math.round((workboardCompleted / workboardCount) * 100) : 0;

  return (
    <div className="composer-area">
      {!readOnlySubagentView ? <QueuedMessagesDock messages={queuedMessages} onRemove={onRemoveQueued} /> : null}
      <div className="composer-box">
        {!readOnlySubagentView && matches.length > 0 && (
          <div className="composer-command-menu" role="group" aria-label="工作台指令">
            {matches.map(({ name, label }, index) => (
              <button
                key={name}
                type="button"
                aria-disabled={Boolean(commandAvailability?.[name])}
                className={index === commandIndex ? "is-selected" : undefined}
                onClick={() => runCommand(name)}
              >
                <code>/{name}</code><span>{label}{commandAvailability?.[name] ? ` · ${commandAvailability[name]}` : ""}</span>
              </button>
            ))}
          </div>
        )}
        {commandError && <div className="composer-command-error" role="status">{commandError}</div>}
        {readOnlySubagentView ? (
          <div className="composer-readonly">子代理会话为只读，请在主会话继续对话。</div>
        ) : (
          <textarea
            ref={textareaRef}
            className="composer-input"
            value={input}
            onChange={(e) => updateInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.nativeEvent.isComposing) return;
              if (matches.length > 0 && ["ArrowDown", "ArrowUp", "Escape"].includes(e.key)) {
                e.preventDefault();
                if (e.key === "Escape") {
                  setMenuDismissed(true);
                  setCommandIndex(-1);
                } else {
                  const direction = e.key === "ArrowDown" ? 1 : -1;
                  setCommandIndex((index) => {
                    if (index < 0) return direction > 0 ? 0 : matches.length - 1;
                    return (index + direction + matches.length) % matches.length;
                  });
                }
                return;
              }
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                const selected = matches[commandIndex];
                if (selected) runCommand(selected.name);
                else handleSend();
              }
            }}
            placeholder={busy ? "消息将排队，输入 / 可查看指令" : "输入消息，或输入 / 查看指令"}
            rows={1}
          />
        )}
        <div className="composer-actions">
          <div className="composer-actions__left">
            {readOnlySubagentView ? (
              <button type="button" className="icon-button subtle" onClick={onExitSubagent} title="返回主会话">
                <Icons.ChevronLeft />
              </button>
            ) : (
              <>
                <label className="icon-button attach-btn" title="上传文件">
                  <input
                    type="file"
                    multiple
                    onChange={(e) => {
                      if (e.target.files && e.target.files.length > 0) handleFiles(e.target.files);
                      e.currentTarget.value = "";
                    }}
                  />
                  <Icons.Attach />
                </label>
                <button
                  type="button"
                  className={`workboard-toggle ${workboardVisible ? "active" : ""} ${workboardAllDone ? "completed" : ""} ${workboardCount === 0 ? "empty" : ""}`}
                  onClick={onWorkboardToggle}
                  aria-pressed={workboardVisible}
                  title={workboardCount > 0 ? (workboardAllDone ? "任务已全部完成" : `任务清单 ${workboardCompleted}/${workboardCount}`) : "任务清单"}
                >
                  <Icons.Checklist />
                  {!workboardVisible && workboardCount > 0 && !workboardAllDone ? (
                    <>
                      <span className="workboard-toggle__label">{workboardCompleted}/{workboardCount}</span>
                      <div className="workboard-toggle__mini-bar">
                        <div className="workboard-toggle__mini-bar-fill" style={{ width: `${workboardPercent}%` }} />
                      </div>
                    </>
                  ) : null}
                </button>
              </>
            )}
            <SubagentPopover
              subagents={subagents}
              open={subagentPopoverOpen}
              activeChildSessionId={activeChildSessionId}
              onOpenChange={onSubagentPopoverOpenChange}
              onSelect={onOpenSubagent}
              onStop={onStopSubagent}
              onDestroy={onDestroySubagent}
            />
            {!readOnlySubagentView ? (
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
            ) : null}
          </div>
          <div className="composer-actions__right">
            {!readOnlySubagentView ? (
              <>
                <div className="reasoning-effort-dropdown" ref={effortRef}>
                  <button
                    type="button"
                    className={`effort-toggle ${effortOpen ? "active" : ""}`}
                    onClick={() => setEffortOpen((v) => !v)}
                    title="思考深度"
                  >
                    <span>{reasoningEffort || "auto"}</span>
                    <Icons.ChevronDown />
                  </button>
                  {effortOpen ? (
                    <div className="effort-popup">
                      <button
                        type="button"
                        className={`effort-option ${reasoningEffort === "" ? "selected" : ""}`}
                        onClick={() => { onReasoningEffortChange(""); setEffortOpen(false); }}
                      >
                        auto
                      </button>
                      {reasoningEffortOptions.map((opt) => (
                        <button
                          key={opt}
                          type="button"
                          className={`effort-option ${reasoningEffort === opt ? "selected" : ""}`}
                          onClick={() => { onReasoningEffortChange(opt); setEffortOpen(false); }}
                        >
                          {opt}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
                <button className={`icon-button send-btn ${canSend ? "active" : ""}`} disabled={!canSend} onClick={handleSend} title="发送">
                  <Icons.Send />
                </button>
              </>
            ) : null}
            {canStop || stopping ? (
              <button className="icon-button stop-btn" onClick={onStop} disabled={stopping} title={stopping ? "正在停止" : "停止"}>
                <Icons.Stop />
              </button>
            ) : null}
          </div>
        </div>
      </div>
      <div className="composer-footer">
        {readOnlySubagentView ? "子代理 · 只读历史与运行状态" : "AetherCore System · Advanced Mode"}
      </div>
    </div>
  );
}
