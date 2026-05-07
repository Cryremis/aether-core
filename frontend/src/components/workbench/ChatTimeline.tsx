import { createPortal } from "react-dom";
import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode, type RefObject } from "react";
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

type TerminalStyleState = {
  fg: string | null;
  bg: string | null;
  bold: boolean;
  dim: boolean;
  italic: boolean;
  underline: boolean;
  strike: boolean;
  inverse: boolean;
  hidden: boolean;
};

type TerminalSpan = { text: string; style: CSSProperties };
type TerminalLine = { spans: TerminalSpan[] };

const ANSI_BASIC_COLORS = [
  "#6b7280",
  "#cd3131",
  "#0dbc79",
  "#e5e510",
  "#2472c8",
  "#bc3fbc",
  "#11a8cd",
  "#e5e5e5",
  "#9ca3af",
  "#f14c4c",
  "#23d18b",
  "#f5f543",
  "#3b8eea",
  "#d670d6",
  "#29b8db",
  "#ffffff",
];

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

function toHexChannel(value: number) {
  return value.toString(16).padStart(2, "0");
}

function ansi256ToHex(index: number) {
  if (index < 0) return null;
  if (index < 16) return ANSI_BASIC_COLORS[index] ?? null;
  if (index <= 231) {
    const normalized = index - 16;
    const r = Math.floor(normalized / 36);
    const g = Math.floor((normalized % 36) / 6);
    const b = normalized % 6;
    const channels = [r, g, b].map((part) => (part === 0 ? 0 : 55 + part * 40));
    return `#${channels.map(toHexChannel).join("")}`;
  }
  if (index <= 255) {
    const c = 8 + (index - 232) * 10;
    return `#${toHexChannel(c)}${toHexChannel(c)}${toHexChannel(c)}`;
  }
  return null;
}

function cloneAnsiState(state: TerminalStyleState): TerminalStyleState {
  return { ...state };
}

function defaultAnsiState(): TerminalStyleState {
  return {
    fg: null,
    bg: null,
    bold: false,
    dim: false,
    italic: false,
    underline: false,
    strike: false,
    inverse: false,
    hidden: false,
  };
}

function ansiStateToStyle(state: TerminalStyleState): CSSProperties {
  const style: CSSProperties = {};
  const fg = state.inverse ? state.bg : state.fg;
  const bg = state.inverse ? state.fg : state.bg;

  if (fg) style.color = fg;
  if (bg) style.backgroundColor = bg;
  if (state.bold) style.fontWeight = 700;
  if (state.dim) style.opacity = 0.72;
  if (state.italic) style.fontStyle = "italic";
  const decorations: string[] = [];
  if (state.underline) decorations.push("underline");
  if (state.strike) decorations.push("line-through");
  if (decorations.length > 0) style.textDecoration = decorations.join(" ");
  if (state.hidden) style.opacity = 0.38;
  return style;
}

function applyAnsiCodes(state: TerminalStyleState, codes: number[]) {
  if (codes.length === 0) {
    Object.assign(state, defaultAnsiState());
    return;
  }

  for (let index = 0; index < codes.length; index += 1) {
    const code = codes[index];
    if (code === 0) {
      Object.assign(state, defaultAnsiState());
      continue;
    }
    if (code === 1) {
      state.bold = true;
      continue;
    }
    if (code === 2) {
      state.dim = true;
      continue;
    }
    if (code === 3) {
      state.italic = true;
      continue;
    }
    if (code === 4) {
      state.underline = true;
      continue;
    }
    if (code === 7) {
      state.inverse = true;
      continue;
    }
    if (code === 8) {
      state.hidden = true;
      continue;
    }
    if (code === 9) {
      state.strike = true;
      continue;
    }
    if (code === 22) {
      state.bold = false;
      state.dim = false;
      continue;
    }
    if (code === 23) {
      state.italic = false;
      continue;
    }
    if (code === 24) {
      state.underline = false;
      continue;
    }
    if (code === 27) {
      state.inverse = false;
      continue;
    }
    if (code === 28) {
      state.hidden = false;
      continue;
    }
    if (code === 29) {
      state.strike = false;
      continue;
    }
    if (code === 39) {
      state.fg = null;
      continue;
    }
    if (code === 49) {
      state.bg = null;
      continue;
    }
    if (code >= 30 && code <= 37) {
      state.fg = ANSI_BASIC_COLORS[code - 30] ?? null;
      continue;
    }
    if (code >= 90 && code <= 97) {
      state.fg = ANSI_BASIC_COLORS[code - 82] ?? null;
      continue;
    }
    if (code >= 40 && code <= 47) {
      state.bg = ANSI_BASIC_COLORS[code - 40] ?? null;
      continue;
    }
    if (code >= 100 && code <= 107) {
      state.bg = ANSI_BASIC_COLORS[code - 92] ?? null;
      continue;
    }
    if (code === 38 || code === 48) {
      const isForeground = code === 38;
      const mode = codes[index + 1];
      if (mode === 5 && typeof codes[index + 2] === "number") {
        const color = ansi256ToHex(codes[index + 2]);
        if (isForeground) {
          state.fg = color;
        } else {
          state.bg = color;
        }
        index += 2;
        continue;
      }
      if (mode === 2 && typeof codes[index + 2] === "number" && typeof codes[index + 3] === "number" && typeof codes[index + 4] === "number") {
        const rgb = `#${toHexChannel(codes[index + 2])}${toHexChannel(codes[index + 3])}${toHexChannel(codes[index + 4])}`;
        if (isForeground) {
          state.fg = rgb;
        } else {
          state.bg = rgb;
        }
        index += 4;
      }
    }
  }
}

function pushTerminalSpan(line: TerminalLine, state: TerminalStyleState, text: string) {
  if (!text) return;
  const style = ansiStateToStyle(state);
  const previous = line.spans[line.spans.length - 1];
  if (previous && JSON.stringify(previous.style) === JSON.stringify(style)) {
    previous.text += text;
    return;
  }
  line.spans.push({ text, style });
}

function parseTerminalText(value: string): TerminalLine[] {
  if (!value) return [];

  const lines: TerminalLine[] = [];
  let currentLine: TerminalLine = { spans: [] };
  let buffer = "";
  let state = defaultAnsiState();

  const flushBuffer = () => {
    if (!buffer) return;
    pushTerminalSpan(currentLine, state, buffer);
    buffer = "";
  };

  const commitLine = () => {
    flushBuffer();
    lines.push(currentLine);
    currentLine = { spans: [] };
  };

  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    if (char === "\u001b" && value[index + 1] === "[") {
      flushBuffer();
      let cursor = index + 2;
      while (cursor < value.length && !/[A-Za-z]/.test(value[cursor])) {
        cursor += 1;
      }
      if (cursor >= value.length) {
        break;
      }
      const command = value[cursor];
      if (command === "m") {
        const params = value.slice(index + 2, cursor).split(";").filter(Boolean).map((part) => Number(part));
        applyAnsiCodes(state, params);
      }
      index = cursor;
      continue;
    }

    if (char === "\r") {
      if (value[index + 1] === "\n") {
        flushBuffer();
        if (currentLine.spans.length > 0) {
          lines.push(currentLine);
        }
        currentLine = { spans: [] };
        index += 1;
        continue;
      }
      flushBuffer();
      currentLine = { spans: [] };
      continue;
    }

    if (char === "\n") {
      flushBuffer();
      if (currentLine.spans.length > 0) {
        lines.push(currentLine);
      }
      currentLine = { spans: [] };
      continue;
    }

    if (char === "\b") {
      if (buffer.length > 0) {
        buffer = buffer.slice(0, -1);
      }
      continue;
    }

    buffer += char;
  }

  flushBuffer();
  lines.push(currentLine);
  if (lines.length > 1 && lines[lines.length - 1].spans.length === 0 && value.endsWith("\n")) {
    lines.pop();
  }
  return lines;
}

function TerminalRenderer({ text }: { text: string }) {
  const lines = useMemo(() => parseTerminalText(text), [text]);
  if (lines.length === 0) {
    return <span className="tool-panel__empty">等待工具输出...</span>;
  }

  return (
    <>
      {lines.map((line, lineIndex) => (
        <span key={`line-${lineIndex}`} className="tool-terminal-line">
          {line.spans.length === 0 ? "\u00a0" : line.spans.map((span, spanIndex) => (
            <span key={`span-${lineIndex}-${spanIndex}`} style={span.style}>
              {span.text}
            </span>
          ))}
        </span>
      ))}
    </>
  );
}

function useAutoStickToBottom(text: string, enabled = true) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);

  useEffect(() => {
    const node = hostRef.current;
    if (!node) return;
    const handleScroll = () => {
      const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
      stickToBottomRef.current = distance <= 24;
    };
    node.addEventListener("scroll", handleScroll);
    return () => node.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    const node = hostRef.current;
    if (!node || !enabled) return;
    if (!stickToBottomRef.current) return;
    node.scrollTop = node.scrollHeight;
  }, [text, enabled]);

  return hostRef;
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
  const lineCount = useMemo(() => parseTerminalText(text).length, [text]);
  const contentRef = useAutoStickToBottom(text, expanded);

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
          <div ref={contentRef} className="tool-panel__content tool-panel__content--terminal">
            <TerminalRenderer text={text} />
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
          <TerminalRenderer text={primary || " "} />
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
