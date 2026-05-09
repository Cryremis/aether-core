import { createPortal } from "react-dom";
import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode, type RefObject } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { MemoizedMarkdown, renderAssistantSegments, formatElapsedMs } from "../../pages/workbench/markdown";
import type { ChatMessage } from "../../pages/workbench/types";
import { WorkbenchIcons as Icons } from "./WorkbenchIcons";

type ChatTimelineProps = {
  contentRef?: RefObject<HTMLDivElement | null>;
  loading: boolean;
  messages: ChatMessage[];
  onForkUserMessage?: (messageId: string) => void;
  onRerunFromMessage?: (messageId: string) => void;
  onEditUserMessage?: (messageId: string, content: string) => void;
  actionsDisabled?: boolean;
};

type SummaryResult = {
  primary: string;
  meta: Array<[string, string]>;
  raw: string;
};

type TerminalSpan = {
  text: string;
  style: CSSProperties;
  styleKey: string;
};

type TerminalLine = {
  spans: TerminalSpan[];
};

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

const RESULT_TERMINAL_THEME = {
  background: "#f8fffb",
  foreground: "#166534",
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

function extractTerminalTextFromOutput(outputText: string): string | null {
  const parsed = parseJsonSafely(outputText);
  if (!parsed) return null;
  const hasStdout = typeof parsed.stdout === "string";
  const hasStderr = typeof parsed.stderr === "string";
  if (!hasStdout && !hasStderr) return null;
  const stdout = hasStdout ? String(parsed.stdout ?? "") : "";
  const stderr = hasStderr ? String(parsed.stderr ?? "") : "";
  if (!stdout) return stderr;
  if (!stderr) return stdout;
  return stdout.endsWith("\n") ? `${stdout}${stderr}` : `${stdout}\n${stderr}`;
}

function countTerminalLines(value: string): number {
  if (!value) return 0;
  return value.split(/\r\n|\r|\n/).length;
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

function rgbNumberToHex(value: number) {
  return `#${value.toString(16).padStart(6, "0")}`;
}

function serializeStyle(style: CSSProperties) {
  return JSON.stringify(style);
}

function buildCellStyle(cell: {
  isFgRGB(): boolean;
  isFgPalette(): boolean;
  isFgDefault(): boolean;
  isBgRGB(): boolean;
  isBgPalette(): boolean;
  isBgDefault(): boolean;
  getFgColor(): number;
  getBgColor(): number;
  isBold(): number;
  isItalic(): number;
  isUnderline(): number;
  isStrikethrough(): number;
  isInverse(): number;
  isInvisible(): number;
  isDim(): number;
}): CSSProperties {
  const style: CSSProperties = {};

  let foreground = RESULT_TERMINAL_THEME.foreground;
  if (cell.isFgRGB()) {
    foreground = rgbNumberToHex(cell.getFgColor());
  } else if (cell.isFgPalette()) {
    foreground = ansi256ToHex(cell.getFgColor()) ?? foreground;
  } else if (!cell.isFgDefault()) {
    foreground = RESULT_TERMINAL_THEME.foreground;
  }

  let background = "transparent";
  if (cell.isBgRGB()) {
    background = rgbNumberToHex(cell.getBgColor());
  } else if (cell.isBgPalette()) {
    background = ansi256ToHex(cell.getBgColor()) ?? background;
  } else if (!cell.isBgDefault()) {
    background = RESULT_TERMINAL_THEME.background;
  }

  if (cell.isInverse()) {
    const swappedForeground = background === "transparent" ? RESULT_TERMINAL_THEME.background : background;
    background = foreground;
    foreground = swappedForeground;
  }

  style.color = foreground;
  if (background !== "transparent") {
    style.backgroundColor = background;
  }
  if (cell.isBold()) style.fontWeight = 700;
  if (cell.isItalic()) style.fontStyle = "italic";
  if (cell.isDim()) style.opacity = 0.72;
  const decorations: string[] = [];
  if (cell.isUnderline()) decorations.push("underline");
  if (cell.isStrikethrough()) decorations.push("line-through");
  if (decorations.length > 0) style.textDecoration = decorations.join(" ");
  if (cell.isInvisible()) style.visibility = "hidden";

  return style;
}

function extractTerminalLines(terminal: XTerm): TerminalLine[] {
  const lines: TerminalLine[] = [];
  const buffer = terminal.buffer.active;

  for (let lineIndex = 0; lineIndex <= buffer.baseY + buffer.cursorY; lineIndex += 1) {
    const bufferLine = buffer.getLine(lineIndex);
    if (!bufferLine) continue;

    let maxCellIndex = -1;
    const scanLimit = Math.min(bufferLine.length, terminal.cols);
    for (let cellIndex = 0; cellIndex < scanLimit; cellIndex += 1) {
      const cell = bufferLine.getCell(cellIndex);
      if (!cell) continue;
      if (cell.getChars() || cell.getCode() !== 0 || !cell.isAttributeDefault()) {
        maxCellIndex = cellIndex;
      }
    }

    if (maxCellIndex < 0) {
      lines.push({ spans: [] });
      continue;
    }

    const spans: TerminalSpan[] = [];
    for (let cellIndex = 0; cellIndex <= maxCellIndex; cellIndex += 1) {
      const cell = bufferLine.getCell(cellIndex);
      if (!cell || cell.getWidth() === 0) continue;
      const text = cell.getChars() || " ";
      const style = buildCellStyle(cell);
      const styleKey = serializeStyle(style);
      const previous = spans[spans.length - 1];
      if (previous && previous.styleKey === styleKey) {
        previous.text += text;
      } else {
        spans.push({ text, style, styleKey });
      }
    }

    lines.push({ spans });
  }

  return lines;
}

function ParsedTerminalRenderer({ text }: { text: string }) {
  const [lines, setLines] = useState<TerminalLine[]>([]);

  useEffect(() => {
    let cancelled = false;
    const terminal = new XTerm({
      cols: 4096,
      rows: 1,
      scrollback: 5000,
      allowTransparency: true,
      convertEol: true,
      disableStdin: true,
    });

    terminal.write(text || " ", () => {
      if (cancelled) {
        terminal.dispose();
        return;
      }
      setLines(extractTerminalLines(terminal));
      terminal.dispose();
    });

    return () => {
      cancelled = true;
      terminal.dispose();
    };
  }, [text]);

  if (lines.length === 0) {
    return <span className="tool-panel__empty tool-panel__empty--result">等待执行结果...</span>;
  }

  return (
    <div className="tool-plain-terminal" role="textbox" aria-readonly="true">
      {lines.map((line, lineIndex) => (
        <span key={`line-${lineIndex}`} className="tool-terminal-line">
          {line.spans.length === 0
            ? "\u00a0"
            : line.spans.map((span, spanIndex) => (
                <span key={`span-${lineIndex}-${spanIndex}`} style={span.style}>
                  {span.text}
                </span>
              ))}
        </span>
      ))}
    </div>
  );
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
            <div
              className="tool-json-popover__panel"
              style={{ top: position.top, right: position.right }}
              onClick={(event) => {
                event.stopPropagation();
              }}
              onMouseDown={(event) => {
                event.stopPropagation();
              }}
              onPointerDown={(event) => {
                event.stopPropagation();
              }}
            >
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
  const terminalText = useMemo(() => extractTerminalTextFromOutput(outputText), [outputText]);
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
          {terminalText !== null ? <ParsedTerminalRenderer text={terminalText} /> : <ParsedTerminalRenderer text={primary || " "} />}
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
  const runText = liveOutputText ?? "";

  useEffect(() => {
    // Tool cards follow runtime state by default: open while running, collapse once settled.
    setExpanded(status === "running");
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
            {status === "running" ? <ToolLivePanel text={runText} /> : null}
            {status !== "running" ? <ToolResultPanel outputText={outputText} liveOutputText={liveOutputText} status={status} /> : null}
          </div>
        </div>
      </div>
    </details>
  );
}

function UserMessageBubble({
  message,
  actionsDisabled,
  editingMessageId,
  onStartEdit,
  onForkUserMessage,
  onRerunFromMessage,
  onEditUserMessage,
}: {
  message: Extract<ChatMessage, { role: "user" }>;
  actionsDisabled: boolean;
  editingMessageId: string | null;
  onStartEdit: (messageId: string, content: string) => void;
  onForkUserMessage?: (messageId: string) => void;
  onRerunFromMessage?: (messageId: string) => void;
  onEditUserMessage?: (messageId: string, content: string) => void;
}) {
  const [localEditingContent, setLocalEditingContent] = useState(message.content);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (editingMessageId === message.id && textareaRef.current) {
      setLocalEditingContent(message.content);
      textareaRef.current.focus();
      const length = textareaRef.current.value.length;
      textareaRef.current.setSelectionRange(length, length);
    }
  }, [editingMessageId, message.id, message.content]);

  const handleSaveEdit = () => {
    const trimmed = localEditingContent.trim();
    if (!trimmed) return;
    onEditUserMessage?.(message.id, trimmed);
    onStartEdit("", "");
  };

  const handleEditKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      handleSaveEdit();
    }
    if (event.key === "Escape") {
      event.preventDefault();
      onStartEdit("", "");
    }
  };

  const isEditing = editingMessageId === message.id;

  return (
    <div className="message-row user msg-anim">
      <div className={`bubble user-bubble user-bubble--interactive${isEditing ? " user-bubble--editing" : ""}`}>
        <div className="user-bubble__actions" aria-label="message actions">
          <button
            type="button"
            className="user-bubble__action-btn"
            title="Fork 新会话"
            disabled={actionsDisabled || isEditing}
            onClick={() => onForkUserMessage?.(message.id)}
          >
            <Icons.Fork />
          </button>
          <button
            type="button"
            className="user-bubble__action-btn"
            title="从此重跑"
            disabled={actionsDisabled || isEditing}
            onClick={() => onRerunFromMessage?.(message.id)}
          >
            <Icons.Rerun />
          </button>
          <button
            type="button"
            className="user-bubble__action-btn"
            title="编辑并重跑"
            disabled={actionsDisabled || isEditing}
            onClick={() => onStartEdit(message.id, message.content)}
          >
            <Icons.Pencil />
          </button>
        </div>
        {isEditing ? (
          <div className="user-bubble__edit-panel">
            <textarea
              ref={textareaRef}
              className="user-bubble__edit-textarea"
              value={localEditingContent}
              onChange={(e) => setLocalEditingContent(e.target.value)}
              onKeyDown={handleEditKeyDown}
              rows={Math.min(8, Math.max(2, localEditingContent.split("\n").length + 1))}
            />
            <div className="user-bubble__edit-actions">
              <button
                type="button"
                className="user-bubble__edit-cancel user-bubble__edit-icon-btn"
                onClick={() => onStartEdit("", "")}
                title="取消"
              >
                <Icons.Close />
              </button>
              <button
                type="button"
                className="user-bubble__edit-save user-bubble__edit-icon-btn"
                disabled={!localEditingContent.trim()}
                onClick={handleSaveEdit}
                title="保存并重跑"
              >
                <Icons.Check />
              </button>
            </div>
          </div>
        ) : (
          <MemoizedMarkdown content={message.content} />
        )}
      </div>
    </div>
  );
}

export function ChatTimeline({
  contentRef,
  loading,
  messages,
  onForkUserMessage,
  onRerunFromMessage,
  onEditUserMessage,
  actionsDisabled = false,
}: ChatTimelineProps) {
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);

  const handleStartEdit = (messageId: string, content: string) => {
    setEditingMessageId(messageId || null);
  };

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
          <UserMessageBubble
            key={message.id}
            message={message}
            actionsDisabled={actionsDisabled}
            editingMessageId={editingMessageId}
            onStartEdit={handleStartEdit}
            onForkUserMessage={onForkUserMessage}
            onRerunFromMessage={onRerunFromMessage}
            onEditUserMessage={onEditUserMessage}
          />
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
