import { createPortal } from "react-dom";
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode, type RefObject } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { AnsiUp } from "ansi_up";
import "@xterm/xterm/css/xterm.css";
import { MemoizedMarkdown, renderAssistantSegments, formatElapsedMs } from "../../pages/workbench/markdown";
import type { ChatMessage } from "../../pages/workbench/types";
import { WorkbenchIcons as Icons } from "./WorkbenchIcons";

type ChatTimelineProps = {
  contentRef?: RefObject<HTMLDivElement | null>;
  loading: boolean;
  messages: ChatMessage[];
};

type SummaryResult = {
  primary: string;
  meta: Array<[string, string]>;
  raw: string;
};

function LiveElapsedBadge({ startTime }: { startTime: number }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setElapsed(Date.now() - startTime);
    }, 100);
    return () => window.clearInterval(timer);
  }, [startTime]);

  return <div className="elapsed-badge">{formatElapsedMs(elapsed)}</div>;
}

function RuntimeNotice({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="runtime-notice" aria-live="polite">
      <div className="runtime-notice__header">
        <div className="runtime-notice__line" />
        <div className="runtime-notice__title">{title}</div>
        <div className="runtime-notice__line" />
      </div>
      {detail ? <div className="runtime-notice__detail">{detail}</div> : null}
    </div>
  );
}

function parseJsonSafely(value: string): Record<string, unknown> | null {
  if (!value.trim()) return null;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function formatDisplayValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const formatted = value.map(formatDisplayValue).filter(Boolean);
    return formatted.join(", ");
  }
  if (isPlainObject(value)) {
    return Object.entries(value)
      .map(([key, entryValue]) => `${key}: ${formatDisplayValue(entryValue)}`)
      .filter(Boolean)
      .join(", ");
  }
  return String(value);
}

function formatMetaLabel(key: string): string {
  const normalized = key.replace(/[_-]+/g, " ").trim().toLowerCase();
  const dictionary: Record<string, string> = {
    timeout_seconds: "超时",
    duration_ms: "耗时",
    exit_code: "退出码",
    workdir: "目录",
    cwd: "目录",
    url: "链接",
    path: "路径",
    file_path: "文件",
    query: "查询",
    max_results: "结果数",
  };
  return dictionary[key] ?? dictionary[normalized] ?? normalized.replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatMetaChip(key: string, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (key === "timeout_seconds" && typeof value === "number") return `${value}s`;
  if (key === "duration_ms" && typeof value === "number") return formatElapsedMs(value) ?? `${value}ms`;
  if (key === "exit_code" && typeof value === "number") return String(value);
  const formatted = formatDisplayValue(value);
  if (!formatted) return "";
  return compactText(formatted, 96);
}

function compactText(value: string, maxLength = 160) {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength - 1)}…`;
}

function prettyJson(value: unknown): string {
  if (value === undefined) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function summarizeEntries(parsed: Record<string, unknown>, preferredKeys: string[]): Array<[string, string]> {
  const ordered = new Map<string, string>();
  for (const key of preferredKeys) {
    const value = parsed[key];
    const display = formatMetaChip(key, value);
    if (!display) continue;
    ordered.set(key, compactText(display, 120));
  }
  for (const [key, value] of Object.entries(parsed)) {
    if (ordered.has(key)) continue;
    const display = formatMetaChip(key, value);
    if (!display) continue;
    if (display.length > 140) continue;
    ordered.set(key, display);
  }
  return [...ordered.entries()];
}

function summarizeToolArguments(argumentsText: string, title: string): SummaryResult {
  const parsed = parseJsonSafely(argumentsText);
  if (!parsed) {
    const primary = compactText(argumentsText || title, 180);
    return {
      primary,
      meta: [],
      raw: argumentsText,
    };
  }

  const preferredKeys = ["command", "query", "url", "path", "file_path", "skill_name", "name", "title"];
  const primaryKey = preferredKeys.find((key) => typeof parsed[key] === "string" && String(parsed[key]).trim().length > 0);
  const primaryValue = primaryKey ? compactText(String(parsed[primaryKey] ?? ""), 180) : compactText(title, 180);
  const hiddenMetaKeys = new Set([primaryKey, "shell", "executor"].filter(Boolean));
  const metaPreferredKeys = ["timeout_seconds", "workdir", "cwd", "url", "path", "file_path", "query", "max_results"].filter((key) => !hiddenMetaKeys.has(key));

  return {
    primary: primaryValue,
    meta: summarizeEntries(parsed, metaPreferredKeys).filter(([key]) => !hiddenMetaKeys.has(key)),
    raw: prettyJson(parsed),
  };
}

function summarizeToolOutput(outputText: string, liveOutputText?: string): SummaryResult {
  const parsed = parseJsonSafely(outputText);
  if (parsed) {
    const preferredKeys = ["stdout", "summary", "result", "content", "text", "message"];
    const primaryKey = preferredKeys.find((key) => typeof parsed[key] === "string" && String(parsed[key]).trim().length > 0);
    const primaryValue = primaryKey ? String(parsed[primaryKey] ?? "") : "";
    return {
      primary: primaryValue ? primaryValue : liveOutputText?.trim() ? liveOutputText.trim() : prettyJson(parsed),
      meta: summarizeEntries(parsed, ["exit_code", "duration_ms", "executor", "shell", "status", "stderr_bytes", "stdout_bytes", "log_path"]),
      raw: prettyJson(parsed),
    };
  }

  if (liveOutputText && liveOutputText.trim()) {
    return {
      primary: liveOutputText,
      meta: [],
      raw: outputText || liveOutputText,
    };
  }

  return {
    primary: outputText,
    meta: [],
    raw: outputText,
  };
}

function countTerminalLines(value: string): number {
  if (!value) return 0;
  return value.split(/\r\n|\r|\n/).length;
}

function XTermRenderer({ text, variant, active = true }: { text: string; variant: "terminal" | "result"; active?: boolean }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<XTerm | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const contentLengthRef = useRef(0);
  const stickToBottomRef = useRef(true);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const terminal = new XTerm({
      convertEol: false,
      cursorBlink: false,
      disableStdin: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace',
      fontSize: 12.5,
      lineHeight: 1.55,
      scrollback: 5000,
      theme:
        variant === "result"
          ? {
              background: "#f8fffb",
              foreground: "#166534",
              cursor: "#166534",
            }
          : {
              background: "#111827",
              foreground: "#dbeafe",
              cursor: "#93c5fd",
            },
    });

    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(host);
    fitAddon.fit();

    terminal.onScroll(() => {
      const buffer = terminal.buffer.active;
      stickToBottomRef.current = buffer.baseY <= buffer.viewportY + 1;
    });

    if (text) {
      terminal.write(text);
      contentLengthRef.current = text.length;
      if (active) terminal.scrollToBottom();
    } else {
      contentLengthRef.current = 0;
    }

    const observer = new ResizeObserver(() => {
      fitAddon.fit();
      if (active && stickToBottomRef.current) terminal.scrollToBottom();
    });
    observer.observe(host);

    terminalRef.current = terminal;
    fitAddonRef.current = fitAddon;

    return () => {
      observer.disconnect();
      terminal.dispose();
      terminalRef.current = null;
      fitAddonRef.current = null;
      contentLengthRef.current = 0;
    };
  }, [variant]);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal) return;

    const previousLength = contentLengthRef.current;
    if (text.length < previousLength) {
      terminal.clear();
      if (text) terminal.write(text);
    } else if (text.length > previousLength) {
      terminal.write(text.slice(previousLength));
    }
    contentLengthRef.current = text.length;
    if (active && stickToBottomRef.current) terminal.scrollToBottom();
  }, [text, active]);

  useEffect(() => {
    if (!active) return;
    const terminal = terminalRef.current;
    const fitAddon = fitAddonRef.current;
    if (!terminal || !fitAddon) return;
    fitAddon.fit();
    if (stickToBottomRef.current) terminal.scrollToBottom();
  }, [active]);

  return <div ref={hostRef} className={`tool-xterm tool-xterm--${variant}`} />;
}

function PlainOutputRenderer({ text }: { text: string }) {
  const html = useMemo(() => {
    const ansi = new AnsiUp();
    ansi.use_classes = false;
    return ansi.ansi_to_html(text || " ");
  }, [text]);

  return <pre className="tool-plain-output" dangerouslySetInnerHTML={{ __html: html }} />;
}

function IconPopover({ label, children, icon }: { label: string; children: ReactNode; icon: ReactNode }) {
  const [open, setOpen] = useState(false);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [position, setPosition] = useState({ top: 0, right: 0 });

  useEffect(() => {
    if (!open) return;
    const node = hostRef.current;
    if (node) {
      const rect = node.getBoundingClientRect();
      setPosition({
        top: rect.bottom + 8,
        right: window.innerWidth - rect.right,
      });
    }
    const handlePointerDown = (event: PointerEvent) => {
      if (hostRef.current && !hostRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleEscape);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  return (
    <div ref={hostRef} className={`tool-json-popover ${open ? "is-open" : ""}`}>
      <button
        type="button"
        className="tool-icon-button"
        aria-label={label}
        title={label}
        aria-expanded={open}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          setOpen((value) => !value);
        }}
      >
        {icon}
      </button>
      {open
        ? createPortal(
            <div className="tool-json-popover__panel" style={{ top: position.top, right: position.right }}>
              {children}
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}

function ToolLivePanel({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(true);
  const [height, setHeight] = useState(180);
  const lineCount = useMemo(() => countTerminalLines(text), [text]);

  const handleResizeStart = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const startY = event.clientY;
    const startHeight = height;
    event.currentTarget.setPointerCapture(event.pointerId);
    const onMove = (moveEvent: PointerEvent) => {
      const nextHeight = Math.max(128, Math.min(560, startHeight + (moveEvent.clientY - startY)));
      setHeight(nextHeight);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  };

  return (
    <div className="tool-panel tool-panel--terminal">
      <button
        type="button"
        className="tool-panel__head"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
      >
        <span className="tool-panel__head-left">
          <span className="tool-panel__title-icon"><Icons.Terminal /></span>
          <span className="tool-panel__title">RUN</span>
        </span>
        <span className="tool-panel__head-right">
          <span className="tool-status-chip">{lineCount}</span>
          <span className="tool-status-chip">{expanded ? <Icons.ChevronUp /> : <Icons.ChevronDown />}</span>
        </span>
      </button>
      {expanded ? (
        <div className="tool-panel__body" style={{ height }}>
          <div className="tool-panel__content tool-panel__content--terminal">
            {text
              ? <XTermRenderer text={text} variant="terminal" active={expanded} />
              : <span className="tool-panel__empty">等待工具输出...</span>}
          </div>
          <button
            type="button"
            className="tool-panel__resize-button"
            aria-label="调整日志高度"
            onPointerDown={handleResizeStart}
          >
            <Icons.Grip />
          </button>
        </div>
      ) : null}
    </div>
  );
}

function ToolResultPanel({ outputText, liveOutputText, status }: { outputText: string; liveOutputText?: string; status: "running" | "done" | "aborted" }) {
  const [height, setHeight] = useState(180);
  const result = useMemo(() => summarizeToolOutput(outputText, liveOutputText), [outputText, liveOutputText]);
  const primary = result.primary || (status === "running" ? "等待执行结果..." : "");
  const handleResizeStart = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const startY = event.clientY;
    const startHeight = height;
    event.currentTarget.setPointerCapture(event.pointerId);
    const onMove = (moveEvent: PointerEvent) => {
      const nextHeight = Math.max(128, Math.min(560, startHeight + (moveEvent.clientY - startY)));
      setHeight(nextHeight);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  };

  if (!outputText && !liveOutputText) {
    return null;
  }

  return (
    <div className="tool-panel tool-panel--result">
      <div className="tool-panel__head">
        <span className="tool-panel__head-left">
          <span className="tool-panel__title-icon"><Icons.Checklist /></span>
          <span className="tool-panel__title">OUT</span>
        </span>
        <span className="tool-panel__head-right">
          {result.meta.slice(0, 4).map(([key, value]) => (
            <span key={key} className="tool-status-chip tool-status-chip--meta">
              <span className="tool-status-chip__label">{formatMetaLabel(key)}</span>
              <span>{value}</span>
            </span>
          ))}
          <IconPopover label="查看完整输出" icon={<Icons.Braces />}>
            <pre className="code-block tool-json">{result.raw}</pre>
          </IconPopover>
        </span>
      </div>
      <div className="tool-panel__body" style={{ height }}>
        <div className="tool-panel__content tool-panel__content--result">
          <PlainOutputRenderer text={primary || " "} />
        </div>
        <button
          type="button"
          className="tool-panel__resize-button"
          aria-label="调整结果高度"
          onPointerDown={handleResizeStart}
        >
          <Icons.Grip />
        </button>
      </div>
    </div>
  );
}

function ToolCard({ title, argumentsText, outputText, liveOutputText, status }: {
  title: string;
  argumentsText: string;
  outputText: string;
  liveOutputText?: string;
  status: "running" | "done" | "aborted";
}) {
  const [expanded, setExpanded] = useState(status === "running");
  const inputSummary = useMemo(() => summarizeToolArguments(argumentsText, title), [argumentsText, title]);
  const outputSummary = useMemo(() => summarizeToolOutput(outputText, liveOutputText), [outputText, liveOutputText]);

  useEffect(() => {
    if (status === "running") {
      setExpanded(true);
    }
  }, [status]);

  return (
    <details className={`tool-card ${status}`} open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary className="tool-header">
        <div className="tool-header__summary">
          <div className="tool-header__line">
            <svg className="tool-arrow" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
            <span className="tool-title__summary">{inputSummary.primary || outputSummary.primary || title}</span>
          </div>
          {expanded ? (
            <div className="tool-summary-metrics">
              {inputSummary.meta.slice(0, 2).map(([key, value]) => (
                <span key={key} className="tool-metric">{value}</span>
              ))}
            </div>
          ) : null}
        </div>
        <div className="tool-status">
          {expanded ? (
            <IconPopover label="查看输入 JSON" icon={<Icons.Braces />}>
              <pre className="code-block tool-json">{inputSummary.raw}</pre>
            </IconPopover>
          ) : null}
          {status === "running" ? (
            <span className="status-run"><Icons.Loader /> 执行中</span>
          ) : status === "aborted" ? (
            <span className="status-done status-done--aborted"><Icons.Close /> 已停止</span>
          ) : (
            <span className="status-done"><Icons.Check /> 完成</span>
          )}
        </div>
      </summary>
      <div className="tool-body-wrapper">
        <div className="tool-body-inner">
          <div className="tool-body">
            {status === "running" || liveOutputText ? <ToolLivePanel text={liveOutputText ?? ""} /> : null}
            {status !== "running" ? <ToolResultPanel outputText={outputText} liveOutputText={liveOutputText} status={status} /> : null}
          </div>
        </div>
      </div>
    </details>
  );
}

export function ChatTimeline({ contentRef, loading, messages }: ChatTimelineProps) {
  return (
    <div ref={contentRef} className="chat-container">
      {loading ? (
        <div className="welcome-screen anim-enter">
          <div className="welcome-icon"><Icons.Sparkles /></div>
          <h2>AetherCore Workbench</h2>
          <p>正在加载会话内容...</p>
        </div>
      ) : null}

      {!loading && messages.length === 0 ? (
        <div className="welcome-screen anim-enter">
          <div className="welcome-icon"><Icons.Sparkles /></div>
          <h2>AetherCore Workbench</h2>
          <p>输入任务指令，或在左侧上传文件与技能定义。</p>
        </div>
      ) : null}

      {messages.map((message) =>
        message.role === "user" ? (
          <div key={message.id} className="message-row user msg-anim">
            <div className="bubble user-bubble">
              <MemoizedMarkdown content={message.content} />
            </div>
          </div>
        ) : message.role === "system_event" ? (
          <div key={message.id} className="timeline-system-event msg-anim" aria-live="polite">
            <div className="timeline-system-event__line" />
            <div className="timeline-system-event__label">{message.title}</div>
            <div className="timeline-system-event__line" />
          </div>
        ) : message.role === "elicitation_response" ? (
          <div key={message.id} className="message-row message-row--elicitation-response msg-anim">
            <div className="elicitation-response-bubble">
              <div className="elicitation-response-bubble__eyebrow">
                <span className="elicitation-response-bubble__dot" />
                <span>问题已回复</span>
              </div>
              <div className="elicitation-response-bubble__title">{message.title}</div>
              <div className="elicitation-response-bubble__summary">{message.summary}</div>
              <div className="elicitation-response-bubble__answers">
                {message.answers.map((answer) => (
                  <div key={answer.id} className="elicitation-response-bubble__answer">
                    <span>{answer.header}</span>
                    <strong>{answer.value}</strong>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div key={message.id} className="message-row assistant msg-anim">
            <div className="assistant-content">
              {renderAssistantSegments(message.blocks).map((segment) =>
                segment.kind === "tool" ? (
                  <ToolCard
                    key={segment.id}
                    title={segment.block.title}
                    argumentsText={segment.block.argumentsText}
                    outputText={segment.block.outputText}
                    liveOutputText={segment.block.liveOutputText}
                    status={segment.block.status}
                  />
                ) : (
                  <div key={segment.id} className="text-bubble">
                    {segment.blocks.map((block) =>
                      block.kind === "reasoning" ? (
                        <details key={block.id} className="reasoning-block" open>
                          <summary><Icons.Sparkles /> 思考过程</summary>
                          <div className="reasoning-content">
                            <MemoizedMarkdown content={block.content} />
                          </div>
                        </details>
                      ) : block.kind === "runtime_notice" ? (
                        <RuntimeNotice key={block.id} title={block.title} detail={block.detail} />
                      ) : (
                        <MemoizedMarkdown key={block.id} content={block.content} />
                      ),
                    )}
                  </div>
                ),
              )}
            </div>
            {message.streaming && message.startTime ? (
              <LiveElapsedBadge startTime={message.startTime} />
            ) : message.elapsedMs !== null && message.elapsedMs >= 0 ? (
              <div className="elapsed-badge">{formatElapsedMs(message.elapsedMs)}</div>
            ) : null}
          </div>
        ),
      )}
    </div>
  );
}
