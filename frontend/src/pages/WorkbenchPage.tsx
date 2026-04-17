// frontend/src/pages/WorkbenchPage.tsx
import { marked } from "marked";
import hljs from "highlight.js";
import "highlight.js/styles/github-dark-dimmed.css";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  getDownloadUrl,
  getSessionSummary,
  listFiles,
  listSkills,
  streamChat,
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

type SidebarView = "sessions" | "files" | "skills";

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
  onAdminToggle?: () => void;
  onLogout?: () => void;
  onNewConversation?: () => void;
  onSessionRefresh?: () => void;
  onSessionSelect?: (sessionId: string) => void;
};

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

      return `
        <div class="code-block-wrapper">
          <div class="code-header">
            <span class="code-lang">${validLang}</span>
            <button class="copy-button" data-code="${encodedCode}" type="button">
              <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
              <span>复制</span>
            </button>
          </div>
          <pre><code class="hljs language-${validLang}">${highlighted}</code></pre>
        </div>
      `;
    },
  },
});

const Icons = {
  Menu: () => <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round"><line x1="3" y1="12" x2="21" y2="12"></line><line x1="3" y1="6" x2="21" y2="6"></line><line x1="3" y1="18" x2="21" y2="18"></line></svg>,
  SidebarClose: () => <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="9" y1="3" x2="9" y2="21"></line></svg>,
  Send: () => <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>,
  Attach: () => <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path></svg>,
  File: () => <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path><polyline points="13 2 13 9 20 9"></polyline></svg>,
  Download: () => <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>,
  Terminal: () => <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><polyline points="4 17 10 11 4 5"></polyline><line x1="12" y1="19" x2="20" y2="19"></line></svg>,
  Loader: () => <svg className="spin-anim" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="2" x2="12" y2="6"></line><line x1="12" y1="18" x2="12" y2="22"></line><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"></line><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"></line><line x1="2" y1="12" x2="6" y2="12"></line><line x1="18" y1="12" x2="22" y2="12"></line><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"></line><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"></line></svg>,
  Check: () => <svg viewBox="0 0 24 24" width="14" height="14" stroke="#10b981" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>,
  Sparkles: () => <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"></path></svg>,
};

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

export function WorkbenchPage({
  conversations,
  currentUser,
  isEmbedMode = false,
  sessionId,
  onAdminToggle,
  onLogout,
  onNewConversation,
  onSessionRefresh,
  onSessionSelect,
}: WorkbenchPageProps) {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [files, setFiles] = useState<FileItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [sidebarView, setSidebarView] = useState<SidebarView>("sessions");
  const [isSidebarOpen, setIsSidebarOpen] = useState(window.innerWidth > 1024);
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 1024);

  const historyRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const handleCopy = async (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      const btn = target.closest(".copy-button") as HTMLButtonElement | null;
      if (!btn) return;

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
    if (!textareaRef.current) return;
    textareaRef.current.style.height = "auto";
    textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
  }, [input]);

  useEffect(() => {
    const node = historyRef.current;
    if (!node) return;
    node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  const refreshResources = async (nextSessionId: string) => {
    const [skillResult, fileResult] = await Promise.all([listSkills(nextSessionId), listFiles(nextSessionId)]);
    setSkills((skillResult.data ?? []) as SkillItem[]);
    setFiles((fileResult.items ?? []) as FileItem[]);
  };

  const loadSession = async (nextSessionId: string) => {
    const summaryResult = await getSessionSummary(nextSessionId);
    const summary = (summaryResult.data ?? {}) as { messages?: SessionMessage[] };
    setMessages(createHistoryMessages(summary.messages ?? []));
    await refreshResources(nextSessionId);
  };

  useEffect(() => {
    setLoading(true);
    setError("");
    void loadSession(sessionId)
      .catch((loadError) => {
        setError(loadError instanceof Error ? loadError.message : "初始化失败");
        setMessages([]);
        setSkills([]);
        setFiles([]);
      })
      .finally(() => setLoading(false));
  }, [sessionId]);

  const canSend = useMemo(() => input.trim().length > 0 && !!sessionId && !busy, [busy, input, sessionId]);

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

  const handleSend = async () => {
    if (!canSend) return;
    const userText = input.trim();
    const assistantId = `assistant-${Date.now()}`;

    setBusy(true);
    setError("");
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";

    setMessages((current) => [
      ...current,
      { id: `user-${Date.now()}`, role: "user", content: userText },
      { id: assistantId, role: "assistant", blocks: [] },
    ]);

    let activeReasoningId = "";
    let activeContentId = "";
    let activeContentText = "";

    try {
      await streamChat(sessionId, userText, (event) => {
        const payload = (event.payload ?? {}) as Record<string, unknown>;

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
            void refreshResources(sessionId);
          }
          void onSessionRefresh?.();
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
          void refreshResources(sessionId);
          void onSessionRefresh?.();
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
      setBusy(false);
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

  return (
    <main className="app-layout">
      <aside className={`sidebar ${isSidebarOpen ? "is-open" : "is-closed"}`}>
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
                  {!isEmbedMode ? (
                    <button type="button" className="action-button small" onClick={onNewConversation}>
                      新建
                    </button>
                  ) : null}
                </div>
                <div className="item-list">
                  {conversations.length === 0 ? <div className="empty-state">暂无历史会话</div> : null}
                  {conversations.map((item) => (
                    <button
                      key={item.conversation_id}
                      type="button"
                      className={`history-item history-item--compact ${item.session_id === sessionId ? "is-active" : ""}`}
                      onClick={() => onSessionSelect?.(item.session_id)}
                    >
                      <strong>{item.title || "新对话"}</strong>
                      <span>{item.session_id}</span>
                    </button>
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
              <button type="button" className="action-button sidebar-footer__button" onClick={onAdminToggle}>
                管理配置
              </button>
              <button type="button" className="action-button sidebar-footer__button sidebar-footer__button--ghost" onClick={onLogout}>
                退出登录
              </button>
            </div>
          ) : null}
        </div>
      </aside>

      {isMobile && isSidebarOpen ? <div className="sidebar-backdrop" onClick={() => setIsSidebarOpen(false)}></div> : null}

      <section className="main-content">
        <header className="top-nav">
          <div className="nav-left">
            <button className="icon-button subtle nav-trigger" onClick={() => setIsSidebarOpen(!isSidebarOpen)}>
              {isSidebarOpen ? <Icons.SidebarClose /> : <Icons.Menu />}
            </button>
            <span className="session-badge">Session ID: {sessionId || "Initializing..."}</span>
          </div>
        </header>

        {error ? <div className="error-toast anim-shake">{error}</div> : null}

        <div className="chat-area" ref={historyRef}>
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
                            <div className="tool-title"><Icons.Terminal /> {segment.block.title}</div>
                            <div className="tool-status">
                              {segment.block.status === "running" ? <span className="status-run"><Icons.Loader /> 执行中...</span> : <span className="status-done"><Icons.Check /> 完成</span>}
                            </div>
                          </summary>
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
                  void handleSend();
                }
              }}
              placeholder="发送指令，与系统深度交互..."
              rows={1}
            />
            <div className="composer-actions">
              <label className="icon-button attach-btn" title="上传文件">
                <input type="file" onChange={(e) => { void handleUpload(e.target.files?.[0]); e.currentTarget.value = ""; }} />
                <Icons.Attach />
              </label>
              <button className={`icon-button send-btn ${canSend ? "active" : ""}`} disabled={!canSend} onClick={handleSend} title="发送">
                <Icons.Send />
              </button>
            </div>
          </div>
          <div className="composer-footer">AetherCore System · Advanced Mode</div>
        </div>
      </section>
    </main>
  );
}
