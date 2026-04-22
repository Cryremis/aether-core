// frontend/src/pages/WorkbenchPage.tsx
import { marked } from "marked";
import hljs from "highlight.js";
import "highlight.js/styles/github-dark-dimmed.css";
import { useEffect, useRef, useState } from "react";

import {
  bootstrapAdminSession,
  deleteUserLlmConfig,
  getDownloadUrl,
  getUserLlmConfig,
  getSessionSummary,
  listFiles,
  listSkills,
  streamChat,
  updateUserLlmConfig,
  uploadFile,
  uploadSkill,
} from "../api/client";

type FileItem = {
  file_id: string;
  name: string;
  category: string;
  size: number;
};

type SkillItem = {
  name: string;
  description: string;
  source: string;
};

type SessionMessage = {
  role: string;
  content: string;
  blocks?: AssistantBlock[];
};

type AssistantBlock =
  | { id: string; kind: "reasoning"; content: string }
  | { id: string; kind: "content"; content: string; status: "streaming" | "done" }
  | {
      id: string;
      kind: "tool";
      title: string;
      meta: string;
      argumentsText: string;
      outputText: string;
      status: "running" | "done";
    };

type AssistantSegment =
  | { id: string; kind: "bubble"; blocks: Array<Extract<AssistantBlock, { kind: "reasoning" | "content" }>> }
  | { id: string; kind: "tool"; block: Extract<AssistantBlock, { kind: "tool" }> };

type ChatMessage =
  | { id: string; role: "user"; content: string }
  | { id: string; role: "assistant"; blocks: AssistantBlock[] };

type ContextStatus = {
  model: string;
  estimatedTokens: number;
  effectiveWindow: number;
  contextWindow: number;
  targetInputTokens: number;
  warningThreshold: number;
  blockingLimit: number;
  percentUsed: number;
  state: "idle" | "warning" | "compacted" | "recovered" | "blocked";
  detail: string;
};

type SidebarView = "sessions" | "files" | "skills";
type LlmDialogState = {
  enabled: boolean;
  base_url: string;
  model: string;
  api_key: string;
  extra_headers_text: string;
  extra_body_text: string;
  has_api_key: boolean;
  resolved_scope: "user" | "platform" | "global";
};

type WorkbenchPageProps = {
  conversations: Array<{
    conversation_id: string;
    session_id: string;
    title: string;
  }>;
  currentUser?: {
    full_name: string;
    role: string;
  } | null;
  isEmbedMode?: boolean;
  sessionId: string;
  isNewSession?: boolean;
  onAdminToggle?: () => void;
  onLogout?: () => void;
  onNewConversation?: () => void;
  onDeleteSession?: (sessionId: string) => void;
  onRenameSession?: (sessionId: string, currentTitle: string) => void;
  onSessionCreated?: (sessionId: string) => void;
  onSessionRefresh?: () => void;
  onSessionSelect?: (sessionId: string) => void;
};

const SIDEBAR_MIN_WIDTH = 240;
const SIDEBAR_MAX_WIDTH = 460;
const SIDEBAR_DEFAULT_WIDTH = 280;

const RESULT_MESSAGES: Record<string, string> = {
  error_empty_response: "模型未返回可用正文",
  error_max_turns: "执行达到轮次上限",
  error_runtime_limit: "执行达到运行时限",
};

marked.setOptions({ breaks: true, gfm: true });

marked.use({
  renderer: {
    code(codeArg: unknown, langArg?: string) {
      const text = typeof codeArg === "object" && codeArg && "text" in codeArg ? String((codeArg as { text: unknown }).text ?? "") : String(codeArg ?? "");
      const language = typeof codeArg === "object" && codeArg && "lang" in codeArg ? String((codeArg as { lang: unknown }).lang ?? "") : (langArg ?? "plaintext");
      const validLang = hljs.getLanguage(language) ? language : "plaintext";
      const highlighted = hljs.highlight(text, { language: validLang }).value;
      const encodedCode = encodeURIComponent(text);
      const lineCount = text.split("\n").length;
      const shouldCollapse = lineCount > 15;

      return `
        <details class="code-block-wrapper ${shouldCollapse ? "collapsible" : ""}" ${shouldCollapse ? "" : "open"}>
          <summary class="code-header">
            <div class="code-header-left">
              <svg class="code-toggle-icon" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
              <span class="code-lang">${validLang}</span>
            </div>
            <div class="code-header-right">
              ${shouldCollapse ? `<span class="code-expand-label">${lineCount} 行</span>` : ""}
              <button class="copy-button" data-code="${encodedCode}" type="button">
                <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                <span>复制</span>
              </button>
            </div>
          </summary>
          <div class="code-content-wrapper">
            <div class="code-content-inner">
              <pre><code class="hljs language-${validLang}">${highlighted}</code></pre>
            </div>
          </div>
        </details>
      `;
    },
  },
});

const Icons = {
  Menu: () => <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round"><line x1="3" y1="12" x2="21" y2="12"></line><line x1="3" y1="6" x2="21" y2="6"></line><line x1="3" y1="18" x2="21" y2="18"></line></svg>,
  SidebarClose: () => <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="9" y1="3" x2="9" y2="21"></line></svg>,
  Send: () => <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>,
  Attach: () => <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path></svg>,
  Globe: () => <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"></circle><path d="M3 12h18"></path><path d="M12 3a15 15 0 0 1 0 18"></path><path d="M12 3a15 15 0 0 0 0 18"></path></svg>,
  File: () => <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path><polyline points="13 2 13 9 20 9"></polyline></svg>,
  Download: () => <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>,
  Terminal: () => <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><polyline points="4 17 10 11 4 5"></polyline><line x1="12" y1="19" x2="20" y2="19"></line></svg>,
  Loader: () => <svg className="spin-anim" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="2" x2="12" y2="6"></line><line x1="12" y1="18" x2="12" y2="22"></line><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"></line><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"></line><line x1="2" y1="12" x2="6" y2="12"></line><line x1="18" y1="12" x2="22" y2="12"></line><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"></line><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"></line></svg>,
  Check: () => <svg viewBox="0 0 24 24" width="14" height="14" stroke="#10b981" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>,
  Sparkles: () => <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"></path></svg>,
};

type ComposerProps = {
  busy: boolean;
  disabled: boolean;
  allowNetwork: boolean;
  onAllowNetworkChange: (value: boolean) => void;
  onSend: (text: string) => void;
  onUpload: (file: File) => void;
};

function Composer({ busy, disabled, allowNetwork, onAllowNetworkChange, onSend, onUpload }: ComposerProps) {
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!textareaRef.current) return;
    textareaRef.current.style.height = "auto";
    textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
  }, [input]);

  const canSend = input.trim().length > 0 && !busy && !disabled;

  const handleSend = () => {
    if (!canSend) return;
    onSend(input.trim());
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  return (
    <div className="composer-area">
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
          placeholder="发送指令，与系统深度交互..."
          rows={1}
        />
        <div className="composer-actions">
          <div className="composer-actions__left">
            <label className="icon-button attach-btn" title="上传文件">
              <input type="file" onChange={(e) => { if (e.target.files?.[0]) onUpload(e.target.files[0]); e.currentTarget.value = ""; }} />
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
          <button className={`icon-button send-btn ${canSend ? "active" : ""}`} disabled={!canSend} onClick={handleSend} title="发送">
            <Icons.Send />
          </button>
        </div>
      </div>
      <div className="composer-footer">AetherCore System · Advanced Mode</div>
    </div>
  );
}

function createHistoryMessages(items: SessionMessage[]): ChatMessage[] {
  return items.map((item, index) =>
    item.role === "assistant"
      ? {
          id: `history-assistant-${index}`,
          role: "assistant",
          blocks: item.blocks?.length
            ? item.blocks
            : [{ id: `history-content-${index}`, kind: "content", content: item.content, status: "done" }],
        }
      : { id: `history-user-${index}`, role: "user", content: item.content },
  );
}

function formatTokenCount(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "0";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2).replace(/\.00$/, "")}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1).replace(/\.0$/, "")}k`;
  return String(Math.round(value));
}

export function WorkbenchPage({
  conversations,
  currentUser,
  isEmbedMode = false,
  sessionId,
  isNewSession = false,
  onAdminToggle,
  onLogout,
  onNewConversation,
  onDeleteSession,
  onRenameSession,
  onSessionCreated,
  onSessionRefresh,
  onSessionSelect,
}: WorkbenchPageProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [files, setFiles] = useState<FileItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [sidebarView, setSidebarView] = useState<SidebarView>("sessions");
  const [isSidebarOpen, setIsSidebarOpen] = useState(window.innerWidth > 1024);
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 1024);
  const [sidebarWidth, setSidebarWidth] = useState(SIDEBAR_DEFAULT_WIDTH);
  const [isResizingSidebar, setIsResizingSidebar] = useState(false);
  const [showLlmDialog, setShowLlmDialog] = useState(false);
  const [llmBusy, setLlmBusy] = useState(false);
  const [llmError, setLlmError] = useState("");
  const [llmState, setLlmState] = useState<LlmDialogState>({
    enabled: true,
    base_url: "",
    model: "",
    api_key: "",
    extra_headers_text: "",
    extra_body_text: "",
    has_api_key: false,
    resolved_scope: "global",
  });
  const [allowNetwork, setAllowNetwork] = useState(true);
  const [showAdvancedLlmFields, setShowAdvancedLlmFields] = useState(false);
  const [localSessionId, setLocalSessionId] = useState<string | null>(null);
  const [contextStatus, setContextStatus] = useState<ContextStatus | null>(null);
  const isStreamingRef = useRef(false);
  const newlyCreatedSessionRef = useRef<string | null>(null);
const shouldStickToBottomRef = useRef(true);
  const scrollFrameRef = useRef<number | null>(null);

  const historyRef = useRef<HTMLDivElement | null>(null);

  const updateStickToBottom = (node: HTMLDivElement) => {
    const distanceFromBottom = node.scrollHeight - node.scrollTop - node.clientHeight;
    shouldStickToBottomRef.current = distanceFromBottom <= 80;
  };

  const scrollHistoryToBottom = () => {
    const node = historyRef.current;
    if (!node) return;
    if (scrollFrameRef.current !== null) window.cancelAnimationFrame(scrollFrameRef.current);
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      node.scrollTop = node.scrollHeight;
      scrollFrameRef.current = null;
    });
  };

  const handleHistoryScroll = () => {
    const node = historyRef.current;
    if (!node) return;
    updateStickToBottom(node);
  };

  const handleSidebarResizeStart = (event: React.PointerEvent<HTMLDivElement>) => {
    if (isMobile) return;

    event.preventDefault();
    setIsResizingSidebar(true);

    const handlePointerMove = (moveEvent: PointerEvent) => {
      const nextWidth = Math.min(Math.max(moveEvent.clientX, SIDEBAR_MIN_WIDTH), SIDEBAR_MAX_WIDTH);
      setSidebarWidth(nextWidth);
    };

    const handlePointerUp = () => {
      setIsResizingSidebar(false);
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
  };

  useEffect(() => {
    const handleCopy = async (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      const btn = target.closest(".copy-button") as HTMLButtonElement | null;
      if (!btn) return;

      e.preventDefault();
      e.stopPropagation();

      const rawCode = decodeURIComponent(btn.getAttribute("data-code") || "");
      try {
        await navigator.clipboard.writeText(rawCode);
        const span = btn.querySelector("span");
        if (span) {
          const originalText = span.innerText;
          span.innerText = "已复制!";
          btn.classList.add("copied");
          window.setTimeout(() => {
            span.innerText = originalText;
            btn.classList.remove("copied");
          }, 2000);
        }
      } catch (err) {
        console.error("复制失败", err);
      }
    };

    document.addEventListener("click", handleCopy);
    return () => document.removeEventListener("click", handleCopy);
  }, []);

  useEffect(() => {
    const handleResize = () => {
      const mobile = window.innerWidth <= 1024;
      setIsMobile(mobile);
      if (mobile && isSidebarOpen) setIsSidebarOpen(false);
      if (!mobile && !isSidebarOpen) setIsSidebarOpen(true);
    };
window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [isSidebarOpen]);

  useEffect(() => {
    const node = historyRef.current;
    if (!node) return;
    if (!shouldStickToBottomRef.current) return;
    scrollHistoryToBottom();
  }, [messages, loading]);

  useEffect(() => {
    const node = historyRef.current;
    if (!node) return;

    const handleDetailsToggle = (event: Event) => {
      const details = event.target;
      if (!(details instanceof HTMLDetailsElement)) return;
      if (!details.classList.contains("tool-card") && !details.classList.contains("code-block-wrapper") && !details.classList.contains("reasoning-block")) {
        return;
      }

      details.classList.remove("is-opening", "is-closing");
      void details.offsetHeight;
      details.classList.add(details.open ? "is-opening" : "is-closing");

      window.setTimeout(() => {
        details.classList.remove("is-opening", "is-closing");
      }, 240);
    };

    node.addEventListener("toggle", handleDetailsToggle, true);
    return () => node.removeEventListener("toggle", handleDetailsToggle, true);
  }, []);

  useEffect(() => {
    return () => {
      if (scrollFrameRef.current !== null) window.cancelAnimationFrame(scrollFrameRef.current);
    };
  }, []);

  const loadUserLlmConfig = async () => {
    const result = await getUserLlmConfig();
    const data = (result.data ?? {}) as {
      config?: {
        enabled: boolean;
        base_url: string;
        model: string;
        has_api_key: boolean;
        extra_headers?: Record<string, string>;
        extra_body?: Record<string, unknown>;
      } | null;
      resolved?: { scope?: "user" | "platform" | "global" };
    };
    const config = data.config;
    setLlmState({
      enabled: config?.enabled ?? true,
      base_url: config?.base_url ?? "",
      model: config?.model ?? "",
      api_key: "",
      extra_headers_text: config?.extra_headers && Object.keys(config.extra_headers).length > 0 ? JSON.stringify(config.extra_headers, null, 2) : "",
      extra_body_text: config?.extra_body && Object.keys(config.extra_body).length > 0 ? JSON.stringify(config.extra_body, null, 2) : "",
      has_api_key: Boolean(config?.has_api_key),
      resolved_scope: data.resolved?.scope ?? "global",
    });
    setShowAdvancedLlmFields(
      Boolean(
        (config?.extra_headers && Object.keys(config.extra_headers).length > 0) ||
        (config?.extra_body && Object.keys(config.extra_body).length > 0),
      ),
    );
  };

  const parseJsonObject = (raw: string, label: string) => {
    const trimmed = raw.trim();
    if (!trimmed) return {};
    const parsed = JSON.parse(trimmed);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error(`${label}必须是 JSON 对象`);
    }
    return parsed as Record<string, unknown>;
  };

  const openLlmDialog = async () => {
    try {
      setLlmError("");
      setShowLlmDialog(true);
      await loadUserLlmConfig();
    } catch (err) {
      setLlmError(err instanceof Error ? err.message : "加载个人 LLM 配置失败");
    }
  };

  const saveUserLlm = async () => {
    try {
      setLlmBusy(true);
      setLlmError("");
      await updateUserLlmConfig({
        enabled: llmState.enabled,
        base_url: llmState.base_url.trim(),
        model: llmState.model.trim(),
        api_key: llmState.api_key.trim() || undefined,
        extra_headers: parseJsonObject(llmState.extra_headers_text, "扩展请求头") as Record<string, string>,
        extra_body: parseJsonObject(llmState.extra_body_text, "扩展请求体"),
      });
      await loadUserLlmConfig();
    } catch (err) {
      setLlmError(err instanceof Error ? err.message : "保存个人 LLM 配置失败");
    } finally {
      setLlmBusy(false);
    }
  };

  const resetUserLlm = async () => {
    if (!window.confirm("确定删除个人 LLM 覆盖并回退到平台默认 / 全局默认吗？")) return;
    try {
      setLlmBusy(true);
      setLlmError("");
      await deleteUserLlmConfig();
      await loadUserLlmConfig();
    } catch (err) {
      setLlmError(err instanceof Error ? err.message : "删除个人 LLM 配置失败");
    } finally {
      setLlmBusy(false);
    }
  };

  const refreshResources = async (nextSessionId: string) => {
    const [skillResult, fileResult] = await Promise.all([listSkills(nextSessionId), listFiles(nextSessionId)]);
    setSkills((skillResult.data ?? []) as SkillItem[]);
    setFiles((fileResult.items ?? []) as FileItem[]);
  };

  const loadSession = async (nextSessionId: string) => {
    const summaryResult = await getSessionSummary(nextSessionId);
    const summary = (summaryResult.data ?? {}) as {
      messages?: SessionMessage[];
      allow_network?: boolean;
      skills?: SkillItem[];
      files?: FileItem[];
    };
    setMessages(createHistoryMessages(summary.messages ?? []));
    setAllowNetwork(summary.allow_network ?? true);
    setSkills(summary.skills ?? []);
    setFiles(summary.files ?? []);
  };

  useEffect(() => {
    const hasBootstrappedLocalSession = Boolean(localSessionId || newlyCreatedSessionRef.current);

    if (isNewSession && !hasBootstrappedLocalSession && !isStreamingRef.current) {
      setLocalSessionId(null);
      newlyCreatedSessionRef.current = null;
      setMessages([]);
      setSkills([]);
      setFiles([]);
      setLoading(false);
      return;
    }

    if (isStreamingRef.current) {
      return;
    }

    const targetSessionId = sessionId || localSessionId;
    if (!targetSessionId) {
      setLoading(false);
      return;
    }

    if (newlyCreatedSessionRef.current === targetSessionId) {
      newlyCreatedSessionRef.current = null;
      return;
    }

    setLoading(true);
    setError("");
    void loadSession(targetSessionId)
      .catch((loadError) => {
        setError(loadError instanceof Error ? loadError.message : "初始化失败");
        setMessages([]);
        setSkills([]);
        setFiles([]);
      })
      .finally(() => setLoading(false));
  }, [sessionId, isNewSession, localSessionId]);

const composerDisabled = !(sessionId || localSessionId || isNewSession);
  const contextUsagePercent = Math.max(0, Math.min(100, Math.round(contextStatus?.percentUsed ?? 0)));
  const contextStateTone = contextStatus?.state ?? "idle";

  const appendAssistantBlock = (messageId: string, block: AssistantBlock) => {
    setMessages((current) =>
      current.map((item) => (item.id === messageId && item.role === "assistant" ? { ...item, blocks: [...item.blocks, block] } : item)),
    );
  };

  const updateAssistantBlock = (messageId: string, blockId: string, updater: (block: AssistantBlock) => AssistantBlock) => {
    setMessages((current) =>
      current.map((item) =>
        item.id === messageId && item.role === "assistant"
          ? { ...item, blocks: item.blocks.map((block) => (block.id === blockId ? updater(block) : block)) }
          : item,
      ),
    );
  };

  const getToolDisplay = (toolName: string, inputValue: Record<string, unknown>) => {
    if (toolName === "sandbox_shell") {
      const rawCommand = String(inputValue.command ?? "").trim();
      const firstToken = rawCommand.split(/\s+/)[0] || "shell";
      return { title: firstToken, meta: String(inputValue.shell ?? "powershell") };
    }
return { title: toolName, meta: "tool" };
  };

  const handleSend = async (userText: string) => {
    if (!userText) return;
    const assistantId = `assistant-${Date.now()}`;

    let effectiveSessionId = sessionId || localSessionId;
    const wasNewSession = isNewSession && !effectiveSessionId;
    if (wasNewSession) {
      try {
        const created = await bootstrapAdminSession(isEmbedMode ? sessionId || undefined : undefined);
        effectiveSessionId = String(created.data?.session_id ?? "");
        setLocalSessionId(effectiveSessionId);
        newlyCreatedSessionRef.current = effectiveSessionId;
      } catch (err) {
        setError(err instanceof Error ? err.message : "创建会话失败");
        return;
      }
    }

    isStreamingRef.current = true;
    shouldStickToBottomRef.current = true;
    setBusy(true);
    setError("");

    setMessages((current) => [
      ...current,
      { id: `user-${Date.now()}`, role: "user", content: userText },
      { id: assistantId, role: "assistant", blocks: [] },
    ]);

    let activeReasoningId = "";
    let activeContentId = "";
    let activeContentText = "";

    try {
      if (!effectiveSessionId) {
        setError("无法获取会话 ID");
        return;
      }
      await streamChat(effectiveSessionId, userText, allowNetwork, (event) => {
        const payload = (event.payload ?? {}) as Record<string, unknown>;

        if (event.type === "context_status") {
          setContextStatus({
            model: String(payload.model ?? ""),
            estimatedTokens: Number(payload.estimated_tokens ?? 0),
            effectiveWindow: Number(payload.effective_window ?? 0),
            contextWindow: Number(payload.context_window ?? 0),
            targetInputTokens: Number(payload.target_input_tokens ?? 0),
            warningThreshold: Number(payload.warning_threshold ?? 0),
            blockingLimit: Number(payload.blocking_limit ?? 0),
            percentUsed: Number(payload.percent_used ?? 0),
            state: "idle",
            detail: "上下文稳定",
          });
          return;
        }

        if (event.type === "context_warning") {
          setContextStatus((current) => ({
            model: current?.model ?? "",
            estimatedTokens: Number(payload.estimated_tokens ?? current?.estimatedTokens ?? 0),
            effectiveWindow: current?.effectiveWindow ?? 0,
            contextWindow: current?.contextWindow ?? 0,
            targetInputTokens: current?.targetInputTokens ?? 0,
            warningThreshold: Number(payload.warning_threshold ?? current?.warningThreshold ?? 0),
            blockingLimit: Number(payload.blocking_limit ?? current?.blockingLimit ?? 0),
            percentUsed: Number(payload.percent_used ?? current?.percentUsed ?? 0),
            state: "warning",
            detail: "接近压缩阈值",
          }));
          return;
        }

        if (event.type === "context_compacted") {
          setContextStatus((current) => {
            const nextEstimated = Number(payload.tokens_after ?? current?.estimatedTokens ?? 0);
            const effectiveWindow = current?.effectiveWindow ?? 0;
            return {
              model: current?.model ?? "",
              estimatedTokens: nextEstimated,
              effectiveWindow,
              contextWindow: current?.contextWindow ?? 0,
              targetInputTokens: current?.targetInputTokens ?? 0,
              warningThreshold: current?.warningThreshold ?? 0,
              blockingLimit: current?.blockingLimit ?? 0,
              percentUsed: effectiveWindow > 0 ? Number(((nextEstimated / effectiveWindow) * 100).toFixed(2)) : current?.percentUsed ?? 0,
              state: "compacted",
              detail: `已压缩 ${String(payload.strategy ?? "context")}，节省 ${formatTokenCount(Number(payload.tokens_saved ?? 0))} tokens`,
            };
          });
          return;
        }

        if (event.type === "context_recovered") {
          setContextStatus((current) => ({
            model: current?.model ?? "",
            estimatedTokens: current?.estimatedTokens ?? 0,
            effectiveWindow: current?.effectiveWindow ?? 0,
            contextWindow: current?.contextWindow ?? 0,
            targetInputTokens: current?.targetInputTokens ?? 0,
            warningThreshold: current?.warningThreshold ?? 0,
            blockingLimit: current?.blockingLimit ?? 0,
            percentUsed: current?.percentUsed ?? 0,
            state: "recovered",
            detail: `上下文已恢复，采用 ${String(payload.strategy ?? "recovery")}`,
          }));
          return;
        }

        if (event.type === "context_blocked") {
          setContextStatus((current) => ({
            model: current?.model ?? "",
            estimatedTokens: Number(payload.estimated_tokens ?? current?.estimatedTokens ?? 0),
            effectiveWindow: Number(payload.effective_window ?? current?.effectiveWindow ?? 0),
            contextWindow: current?.contextWindow ?? 0,
            targetInputTokens: current?.targetInputTokens ?? 0,
            warningThreshold: current?.warningThreshold ?? 0,
            blockingLimit: Number(payload.blocking_limit ?? current?.blockingLimit ?? 0),
            percentUsed: 100,
            state: "blocked",
            detail: String(payload.message ?? "上下文已阻塞"),
          }));
          return;
        }

        if (event.type === "reasoning_delta") {
          if (!activeReasoningId) {
            activeReasoningId = `reasoning-${Date.now()}`;
            appendAssistantBlock(assistantId, { id: activeReasoningId, kind: "reasoning", content: String(payload.delta ?? "") });
          } else {
            updateAssistantBlock(assistantId, activeReasoningId, (block) =>
              block.kind === "reasoning" ? { ...block, content: `${block.content}${String(payload.delta ?? "")}` } : block,
            );
          }
          return;
        }

        if (event.type === "content_delta") {
          activeContentText += String(payload.delta ?? "");
          if (!activeContentId) {
            activeContentId = `content-${Date.now()}`;
            appendAssistantBlock(assistantId, { id: activeContentId, kind: "content", content: activeContentText, status: "streaming" });
          } else {
            updateAssistantBlock(assistantId, activeContentId, (block) =>
              block.kind === "content" ? { ...block, content: activeContentText, status: "streaming" } : block,
            );
          }
          return;
        }

        if (event.type === "content_completed") {
          if (activeContentId && activeContentText) {
            updateAssistantBlock(assistantId, activeContentId, (block) =>
              block.kind === "content" ? { ...block, content: activeContentText, status: "done" } : block,
            );
          }
          activeReasoningId = "";
          return;
        }

        if (event.type === "tool_started") {
          activeReasoningId = "";
          activeContentId = "";
          activeContentText = "";
          const toolInput = (payload.input ?? {}) as Record<string, unknown>;
          const display = getToolDisplay(String(payload.tool_name ?? "tool"), toolInput);
          appendAssistantBlock(assistantId, {
            id: String(payload.id ?? `tool-${Date.now()}`),
            kind: "tool",
            title: display.title,
            meta: display.meta,
            argumentsText: JSON.stringify(toolInput ?? {}, null, 2),
            outputText: "",
            status: "running",
          });
          return;
        }

        if (event.type === "tool_finished") {
          updateAssistantBlock(assistantId, String(payload.id), (block) =>
            block.kind === "tool" ? { ...block, outputText: JSON.stringify(payload.output ?? {}, null, 2), status: "done" } : block,
          );
          if (["sandbox_shell", "create_text_artifact"].includes(String(payload.tool_name ?? ""))) {
            void refreshResources(effectiveSessionId);
          }
          return;
        }

        if (event.type === "message" && typeof payload.summary === "string") {
          activeContentText = payload.summary;
          if (activeContentId) {
            updateAssistantBlock(assistantId, activeContentId, (block) =>
              block.kind === "content" ? { ...block, content: payload.summary as string, status: "done" } : block,
            );
          } else {
            activeContentId = `content-${Date.now()}`;
            appendAssistantBlock(assistantId, { id: activeContentId, kind: "content", content: payload.summary, status: "done" });
          }
          return;
        }

        if (event.type === "artifact_created") {
          void refreshResources(effectiveSessionId);
          return;
        }

        if (event.type === "result") {
          const subtype = String(payload.subtype ?? "");
          if (subtype && subtype !== "success") setError(RESULT_MESSAGES[subtype] ?? "执行失败");
          return;
        }

        if (event.type === "error") {
          appendAssistantBlock(assistantId, {
            id: `tool-error-${Date.now()}`,
            kind: "tool",
            title: "执行错误",
            meta: "error",
            argumentsText: "",
            outputText: `${String(payload.message ?? "执行失败")}${payload.traceback ? `\n\n${payload.traceback}` : ""}`,
            status: "done",
          });
          setError(typeof payload.message === "string" ? payload.message : "执行失败");
        }
      });
    } catch (chatError) {
      setError(chatError instanceof Error ? chatError.message : "对话执行失败");
    } finally {
      isStreamingRef.current = false;
      setBusy(false);
      if (wasNewSession && effectiveSessionId) {
        onSessionCreated?.(effectiveSessionId);
      }
      void onSessionRefresh?.();
    }
  };

  const handleUpload = async (file: File | undefined) => {
    if (!file || !sessionId) return;
    try {
      setError("");
      await uploadFile(sessionId, file);
      await refreshResources(sessionId);
      void onSessionRefresh?.();
      setSidebarView("files");
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "文件上传失败");
    }
  };

  const handleUploadSkill = async (file: File | undefined) => {
    if (!file || !sessionId) return;
    try {
      setError("");
      await uploadSkill(sessionId, file);
      await refreshResources(sessionId);
      void onSessionRefresh?.();
      setSidebarView("skills");
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "技能上传失败");
    }
  };

  const renderMarkdown = (value: string) => ({ __html: marked.parse(value) as string });

  const renderAssistantSegments = (blocks: AssistantBlock[]): AssistantSegment[] => {
    const segments: AssistantSegment[] = [];
    let currentBubble: Array<Extract<AssistantBlock, { kind: "reasoning" | "content" }>> = [];

    for (const block of blocks) {
      if (block.kind === "tool") {
        if (currentBubble.length > 0) {
          segments.push({ id: `bubble-${currentBubble[0].id}`, kind: "bubble", blocks: currentBubble });
          currentBubble = [];
        }
        segments.push({ id: `tool-${block.id}`, kind: "tool", block });
        continue;
      }
      currentBubble.push(block);
    }

    if (currentBubble.length > 0) {
      segments.push({ id: `bubble-${currentBubble[0].id}`, kind: "bubble", blocks: currentBubble });
    }

    return segments;
  };

  const visibleConversations = conversations;

  return (
    <main className="app-layout">
      <aside
        className={`sidebar ${isSidebarOpen ? "is-open" : "is-closed"} ${isResizingSidebar ? "is-resizing" : ""}`}
        style={{ "--sidebar-width": `${sidebarWidth}px` } as React.CSSProperties}
      >
        <div className="sidebar-inner">
          <div className="sidebar-header">
            <div className="sidebar-header__title">
              <h1 className="brand-title">AetherCore</h1>
              {!isEmbedMode && currentUser ? (
                <p className="sidebar-user-meta">
                  {currentUser.full_name}
                  <span>{currentUser.role}</span>
                </p>
              ) : (
                <p className="sidebar-user-meta">嵌入工作台</p>
              )}
            </div>
            {isMobile ? <button className="icon-button" onClick={() => setIsSidebarOpen(false)}><Icons.Menu /></button> : null}
          </div>

          <div className="segment-control">
            <button className={`segment-btn ${sidebarView === "sessions" ? "active" : ""}`} onClick={() => setSidebarView("sessions")}>会话</button>
            <button className={`segment-btn ${sidebarView === "files" ? "active" : ""}`} onClick={() => setSidebarView("files")}>文件</button>
            <button className={`segment-btn ${sidebarView === "skills" ? "active" : ""}`} onClick={() => setSidebarView("skills")}>技能</button>
          </div>

          <div className="sidebar-content">
            {sidebarView === "sessions" ? (
              <div className="tab-pane">
                <div className="pane-header">
                  <h3>历史会话</h3>
                  <button type="button" className="action-button small" onClick={onNewConversation}>
                    新建
                  </button>
                </div>
                <div className="item-list">
                  {visibleConversations.length === 0 ? <div className="empty-state">暂无历史会话</div> : null}
                  {visibleConversations.map((item) => (
                    <div
                      key={item.conversation_id}
                      className={`history-item history-item--compact ${item.session_id === sessionId ? "is-active" : ""}`}
                    >
                      <button
                        type="button"
                        className="history-item__main"
                        onClick={() => onSessionSelect?.(item.session_id)}
                      >
                        <span className="history-item__title" title={item.title || "新对话"}>{item.title || "新对话"}</span>
                      </button>
                      <div className="history-item__actions">
                        <button type="button" className="history-item__action-btn" title="重命名" onClick={(e) => { e.stopPropagation(); onRenameSession?.(item.session_id, item.title); }}>
                          <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"></path></svg>
                        </button>
                        <button type="button" className="history-item__action-btn history-item__action-btn--delete" title="删除" onClick={(e) => { e.stopPropagation(); onDeleteSession?.(item.session_id); }}>
                          <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : sidebarView === "files" ? (
              <div className="tab-pane">
                <div className="pane-header">
                  <h3>会话文件</h3>
                  <label className="action-button small">
                    <span>上传</span>
                    <input type="file" onChange={(e) => { void handleUpload(e.target.files?.[0]); e.currentTarget.value = ""; }} />
                  </label>
                </div>
                <div className="item-list">
                  {files.length === 0 ? <div className="empty-state">当前暂无上传文件</div> : null}
                  {files.map((item, i) => (
                    <article key={item.file_id} className="resource-card anim-enter" style={{ animationDelay: `${i * 0.05}s` }}>
                      <div className="resource-icon"><Icons.File /></div>
                      <div className="resource-info">
                        <strong>{item.name}</strong>
                        <p>{item.category} · {(item.size / 1024).toFixed(1)} KB</p>
                      </div>
                      <a className="download-btn" href={getDownloadUrl(sessionId, item.file_id)} target="_blank" rel="noreferrer" title="下载"><Icons.Download /></a>
                    </article>
                  ))}
                </div>
              </div>
            ) : (
              <div className="tab-pane">
                <div className="pane-header">
                  <h3>技能包</h3>
                  <label className="action-button small">
                    <span>上传</span>
                    <input
                      type="file"
                      accept=".zip,.md"
                      onChange={(e) => { void handleUploadSkill(e.target.files?.[0]); e.currentTarget.value = ""; }}
                    />
                  </label>
                </div>
                <div className="empty-state">
                  支持上传真实技能包目录压缩成的 `.zip`，或单个 `SKILL.md` 文件。
                </div>

                <h3 className="sub-title">已加载技能 ({skills.length})</h3>
                <div className="item-list">
                  {skills.length === 0 ? <div className="empty-state">暂无已加载技能</div> : null}
                  {skills.map((item, i) => (
                    <article key={`${item.name}-${i}`} className="resource-card block anim-enter" style={{ animationDelay: `${i * 0.05 + 0.1}s` }}>
                      <div className="flex-row">
                        <strong>{item.name}</strong>
                        <span className="badge">{item.source}</span>
                      </div>
                      <p className="desc">{item.description}</p>
                    </article>
                  ))}
                </div>
              </div>
            )}
          </div>

          {!isEmbedMode ? (
            <div className="sidebar-footer">
              <button type="button" className="action-button sidebar-footer__button sidebar-footer__button--ghost" onClick={() => void openLlmDialog()}>
                模型配置
              </button>
              <button type="button" className="action-button sidebar-footer__button" onClick={onAdminToggle}>
                管理配置
              </button>
              <button type="button" className="action-button sidebar-footer__button sidebar-footer__button--ghost" onClick={onLogout}>
                退出登录
              </button>
            </div>
          ) : (
            <div className="sidebar-footer">
              <button type="button" className="action-button sidebar-footer__button sidebar-footer__button--ghost" onClick={() => void openLlmDialog()}>
                模型配置
              </button>
            </div>
          )}
        </div>
        {!isMobile ? (
          <div
            className="sidebar-resizer"
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize sidebar"
            onPointerDown={handleSidebarResizeStart}
          />
        ) : null}
      </aside>

      {isMobile && isSidebarOpen ? <div className="sidebar-backdrop" onClick={() => setIsSidebarOpen(false)}></div> : null}

      {showLlmDialog ? (
        <div className="modal-backdrop" onClick={() => setShowLlmDialog(false)}>
          <div className="llm-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="llm-dialog__header">
              <div>
                <h3>个人 LLM 配置</h3>
                <p>当前生效来源：{llmState.resolved_scope === "user" ? "个人覆盖" : llmState.resolved_scope === "platform" ? "平台默认" : "全局默认"}</p>
              </div>
              <button type="button" className="icon-button subtle" onClick={() => setShowLlmDialog(false)}>×</button>
            </div>
            {llmError ? <div className="error-toast anim-shake">{llmError}</div> : null}
            <div className="llm-dialog__body">
              <label className="admin-panel__checkbox">
                <input
                  type="checkbox"
                  checked={llmState.enabled}
                  onChange={(e) => setLlmState((current) => ({ ...current, enabled: e.target.checked }))}
                />
                <span>启用个人 LLM 覆盖</span>
              </label>
              <input
                className="composer-input llm-input"
                value={llmState.base_url}
                onChange={(e) => setLlmState((current) => ({ ...current, base_url: e.target.value }))}
                autoComplete="off"
                name="llm-base-url"
                placeholder="LiteLLM 或内网 OpenAI 兼容服务地址"
              />
              <input
                className="composer-input llm-input"
                value={llmState.model}
                onChange={(e) => setLlmState((current) => ({ ...current, model: e.target.value }))}
                autoComplete="off"
                name="llm-model-id"
                placeholder="模型 ID"
              />
              <input
                className="composer-input llm-input"
                type="password"
                value={llmState.api_key}
                onChange={(e) => setLlmState((current) => ({ ...current, api_key: e.target.value }))}
                autoComplete="new-password"
                name="llm-api-key"
                placeholder={llmState.has_api_key ? "已存在密钥，留空则保持不变" : "API Key"}
              />
              <details className="llm-advanced-panel" open={showAdvancedLlmFields} onToggle={(e) => setShowAdvancedLlmFields((e.currentTarget as HTMLDetailsElement).open)}>
                <summary>高级参数</summary>
                <p className="llm-advanced-panel__hint">仅在对接 LiteLLM、代理网关或内网兼容服务需要额外请求头、额外请求体时填写。留空即可。</p>
                <textarea
                  className="composer-input llm-textarea"
                  value={llmState.extra_headers_text}
                  onChange={(e) => setLlmState((current) => ({ ...current, extra_headers_text: e.target.value }))}
                  autoComplete="off"
                  name="llm-extra-headers"
                  placeholder='额外请求头 JSON，例如 {"x-tenant":"demo"}'
                />
                <textarea
                  className="composer-input llm-textarea"
                  value={llmState.extra_body_text}
                  onChange={(e) => setLlmState((current) => ({ ...current, extra_body_text: e.target.value }))}
                  autoComplete="off"
                  name="llm-extra-body"
                  placeholder='额外请求体 JSON，例如 {"reasoning":{"effort":"medium"}}'
                />
              </details>
            </div>
            <div className="llm-dialog__footer">
              <button type="button" className="action-button sidebar-footer__button sidebar-footer__button--ghost" onClick={() => void resetUserLlm()} disabled={llmBusy}>
                清除覆盖
              </button>
              <button type="button" className="action-button sidebar-footer__button" onClick={() => void saveUserLlm()} disabled={llmBusy || !llmState.base_url.trim() || !llmState.model.trim()}>
                {llmBusy ? "保存中..." : "保存个人 LLM"}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <section className="main-content">
        <header className="top-nav">
          <div className="nav-left">
            <button className="icon-button subtle nav-trigger" onClick={() => setIsSidebarOpen(!isSidebarOpen)}>
              {isSidebarOpen ? <Icons.SidebarClose /> : <Icons.Menu />}
            </button>
            <span className="session-badge">{isNewSession && !sessionId && !localSessionId ? "新会话" : `Session ID: ${sessionId || localSessionId || "Initializing..."}`}</span>
          </div>
          <div className="nav-right">
{contextStatus ? (
              <div className={`context-pill context-pill--${contextStateTone}`}>
                <div className="context-pill__compact">
                  <span className="context-pill__model" title={contextStatus.model}>{contextStatus.model || "Model"}</span>
                  <span className="context-pill__usage">{formatTokenCount(contextStatus.estimatedTokens)} / {formatTokenCount(contextStatus.effectiveWindow || contextStatus.contextWindow)}</span>
                </div>
                <div className="context-pill__popup">
                  <div className="context-pill__detail">
                    <div className="context-pill__meter">
                      <div className="context-pill__meter-bar" style={{ width: `${contextUsagePercent}%` }} />
                    </div>
                    <div className="context-pill__row">
                      <span className="context-pill__row-label">Usage</span>
                      <span className="context-pill__row-value">{contextUsagePercent}%</span>
                    </div>
                    <div className="context-pill__row">
                      <span className="context-pill__row-label">Target</span>
                      <span className="context-pill__row-value">{formatTokenCount(contextStatus.targetInputTokens)}</span>
                    </div>
                    <div className="context-pill__row">
                      <span className="context-pill__row-label">Block</span>
                      <span className="context-pill__row-value">{formatTokenCount(contextStatus.blockingLimit)}</span>
                    </div>
                    {contextStatus.detail ? <div className="context-pill__detail-text">{contextStatus.detail}</div> : null}
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        </header>

        {error ? <div className="error-toast anim-shake">{error}</div> : null}

        <div className="chat-area" ref={historyRef} onScroll={handleHistoryScroll}>
          <div className="chat-container">
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
                    <div dangerouslySetInnerHTML={renderMarkdown(message.content)} className="markdown-body clean" />
                  </div>
                </div>
              ) : (
                <div key={message.id} className="message-row assistant msg-anim">
                  <div className="assistant-content">
                    {renderAssistantSegments(message.blocks).map((segment) =>
                      segment.kind === "tool" ? (
                        <details key={segment.id} className={`tool-card ${segment.block.status}`}>
                          <summary className="tool-header">
                            <div className="tool-title">
                              <svg className="tool-arrow" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
                              {segment.block.title}
                            </div>
                            <div className="tool-status">
                              {segment.block.status === "running" ? <span className="status-run"><Icons.Loader /> 执行中...</span> : <span className="status-done"><Icons.Check /> 完成</span>}
                            </div>
                          </summary>
                          <div className="tool-body-wrapper">
                            <div className="tool-body-inner">
                              <div className="tool-body">
                                {segment.block.argumentsText ? (
                                  <div className="tool-section">
                                    <div className="section-label">Input Args</div>
                                    <pre className="code-block input">{segment.block.argumentsText}</pre>
                                  </div>
                                ) : null}
                                {segment.block.outputText ? (
                                  <div className="tool-section">
                                    <div className="section-label">Output Result</div>
                                    <pre className="code-block output">{segment.block.outputText}</pre>
                                  </div>
                                ) : null}
                              </div>
                            </div>
                          </div>
                        </details>
                      ) : (
                        <div key={segment.id} className="text-bubble">
                          {segment.blocks.map((block) =>
                            block.kind === "reasoning" ? (
                              <details key={block.id} className="reasoning-block" open>
                                <summary><Icons.Sparkles /> Thinking</summary>
                                <div className="reasoning-content markdown-body" dangerouslySetInnerHTML={renderMarkdown(block.content)} />
                              </details>
                            ) : (
                              <div key={block.id} className="markdown-body" dangerouslySetInnerHTML={renderMarkdown(block.content)} />
                            ),
                          )}
                        </div>
                      ),
                    )}
                  </div>
                </div>
              ),
            )}
          </div>
        </div>

        <Composer
          busy={busy}
          disabled={composerDisabled}
          allowNetwork={allowNetwork}
          onAllowNetworkChange={setAllowNetwork}
          onSend={(text) => void handleSend(text)}
          onUpload={(file) => void handleUpload(file)}
        />
      </section>
    </main>
  );
}
