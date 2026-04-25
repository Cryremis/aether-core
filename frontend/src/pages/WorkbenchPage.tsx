// frontend/src/pages/WorkbenchPage.tsx
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import {
  abortSession,
  bootstrapAdminSession,
  deleteUserLlmConfig,
  type ElicitationRequest,
  type ElicitationResponseItem,
  type ElicitationState,
  getDownloadUrl,
  getUserLlmConfig,
  getSessionSummary,
  listFiles,
  listSkills,
  streamElicitationResponse,
  streamChat,
  type WorkboardState,
  updateUserLlmConfig,
  uploadFile,
  uploadSkill,
} from "../api/client";
import { ElicitationPanel } from "../components/ElicitationPanel";
import { WorkboardDock } from "../components/WorkboardDock";
import { ChatTimeline } from "../components/workbench/ChatTimeline";
import { Composer } from "../components/workbench/Composer";
import { ContextStatusPill } from "../components/workbench/ContextStatusPill";
import { LlmConfigDialog } from "../components/workbench/LlmConfigDialog";
import { WorkbenchIcons as Icons } from "../components/workbench/WorkbenchIcons";
import { WorkbenchSidebar } from "../components/workbench/WorkbenchSidebar";
import { formatTokenCount } from "./workbench/markdown";
import type {
  AssistantBlock,
  ChatMessage,
  ContextStatus,
  FileItem,
  LlmDialogState,
  QueuedMessage,
  SessionMessage,
  SidebarView,
  SkillItem,
  WorkbenchPageProps,
} from "./workbench/types";

const SIDEBAR_MIN_WIDTH = 240;
const SIDEBAR_MAX_WIDTH = 460;
const SIDEBAR_DEFAULT_WIDTH = 280;

const RESULT_MESSAGES: Record<string, string> = {
  error_empty_response: "模型未返回可用正文",
  error_max_turns: "执行达到轮次上限",
  error_runtime_limit: "执行达到运行时限",
};

function createHistoryMessages(items: SessionMessage[]): ChatMessage[] {
  return items
    .filter((item) => item.role !== "tool")
    .map((item, index) =>
      item.role === "assistant"
        ? {
            id: `history-assistant-${index}`,
            role: "assistant",
            blocks: (item.blocks ?? []).filter((b) => b.kind !== "elapsed").length
              ? (item.blocks ?? []).filter((b) => b.kind !== "elapsed")
              : [{ id: `history-content-${index}`, kind: "content", content: item.content, status: "done" }],
            elapsedMs: (item.blocks ?? []).find((b) => b.kind === "elapsed")?.elapsed_ms ?? null,
            streaming: false,
          }
        : { id: `history-user-${index}`, role: "user", content: item.content },
    );
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
  const [workboard, setWorkboard] = useState<WorkboardState | null>(null);
  const [elicitation, setElicitation] = useState<ElicitationState | null>(null);
  const [elicitationBusy, setElicitationBusy] = useState(false);
  const [showAdvancedLlmFields, setShowAdvancedLlmFields] = useState(false);
  const [localSessionId, setLocalSessionId] = useState<string | null>(null);
  const [contextStatus, setContextStatus] = useState<ContextStatus | null>(null);
  const [queuedMessages, setQueuedMessages] = useState<QueuedMessage[]>([]);
  const queuedMessagesRef = useRef<QueuedMessage[]>([]);
  const isStreamingRef = useRef(false);
  const abortControllerRef = useRef<AbortController | null>(null);
  const newlyCreatedSessionRef = useRef<string | null>(null);
  const streamingTimerRef = useRef<number | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const scrollFrameRef = useRef<number | null>(null);
  const bottomAnchorFrameRef = useRef<number | null>(null);
  const bottomAnchorUntilRef = useRef(0);
  const detailsToggleShouldStickRef = useRef(false);
  const pendingSessionBottomScrollRef = useRef(false);

  const historyRef = useRef<HTMLDivElement | null>(null);

  const isNearBottom = (node: HTMLDivElement, threshold = 80) => {
    const distanceFromBottom = node.scrollHeight - node.scrollTop - node.clientHeight;
    return distanceFromBottom <= threshold;
  };

  const updateStickToBottom = (node: HTMLDivElement) => {
    shouldStickToBottomRef.current = isNearBottom(node);
  };

  const scrollHistoryToBottom = (behavior: ScrollBehavior = "auto") => {
    const node = historyRef.current;
    if (!node) return;
    if (scrollFrameRef.current !== null) window.cancelAnimationFrame(scrollFrameRef.current);
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      node.scrollTo({ top: node.scrollHeight, behavior });
      scrollFrameRef.current = null;
    });
  };

  const holdBottomAnchor = (durationMs = 320) => {
    const node = historyRef.current;
    if (!node) return;

    bottomAnchorUntilRef.current = Math.max(bottomAnchorUntilRef.current, performance.now() + durationMs);
    if (bottomAnchorFrameRef.current !== null) return;

    const tick = () => {
      const current = historyRef.current;
      if (!current) {
        bottomAnchorFrameRef.current = null;
        return;
      }

      if (shouldStickToBottomRef.current) {
        current.scrollTop = current.scrollHeight;
      }

      if (performance.now() < bottomAnchorUntilRef.current && shouldStickToBottomRef.current) {
        bottomAnchorFrameRef.current = window.requestAnimationFrame(tick);
      } else {
        bottomAnchorFrameRef.current = null;
      }
    };

    bottomAnchorFrameRef.current = window.requestAnimationFrame(tick);
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

  useLayoutEffect(() => {
    const node = historyRef.current;
    if (!node) return;
    if (pendingSessionBottomScrollRef.current && !loading) {
      pendingSessionBottomScrollRef.current = false;
      shouldStickToBottomRef.current = true;
      scrollHistoryToBottom();
      holdBottomAnchor();
      return;
    }
    if (!shouldStickToBottomRef.current) return;
    scrollHistoryToBottom();
  }, [messages, loading]);

  useEffect(() => {
    const node = historyRef.current;
    if (!node) return;

    const rememberBottomIntent = (event: Event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const summary = target.closest("summary");
      if (!summary || !node.contains(summary)) return;
      const details = summary.closest("details");
      if (!(details instanceof HTMLDetailsElement)) return;
      if (!details.classList.contains("tool-card") && !details.classList.contains("code-block-wrapper") && !details.classList.contains("reasoning-block")) {
        return;
      }
      detailsToggleShouldStickRef.current = isNearBottom(node);
    };

    const handleDetailsToggle = (event: Event) => {
      const details = event.target;
      if (!(details instanceof HTMLDetailsElement)) return;
      if (!details.classList.contains("tool-card") && !details.classList.contains("code-block-wrapper") && !details.classList.contains("reasoning-block")) {
        return;
      }

      const shouldKeepBottom = detailsToggleShouldStickRef.current || isNearBottom(node);
      detailsToggleShouldStickRef.current = false;

      details.classList.remove("is-opening", "is-closing");
      void details.offsetHeight;
      details.classList.add(details.open ? "is-opening" : "is-closing");

      if (shouldKeepBottom) {
        shouldStickToBottomRef.current = true;
        scrollHistoryToBottom();
        holdBottomAnchor();
      }

      window.setTimeout(() => {
        details.classList.remove("is-opening", "is-closing");
      }, 240);
    };

    node.addEventListener("pointerdown", rememberBottomIntent, true);
    node.addEventListener("keydown", rememberBottomIntent, true);
    node.addEventListener("toggle", handleDetailsToggle, true);
    return () => {
      node.removeEventListener("pointerdown", rememberBottomIntent, true);
      node.removeEventListener("keydown", rememberBottomIntent, true);
      node.removeEventListener("toggle", handleDetailsToggle, true);
    };
  }, []);

  useEffect(() => {
    return () => {
      if (scrollFrameRef.current !== null) window.cancelAnimationFrame(scrollFrameRef.current);
      if (bottomAnchorFrameRef.current !== null) window.cancelAnimationFrame(bottomAnchorFrameRef.current);
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
      workboard?: WorkboardState;
      elicitation?: ElicitationState;
      context_state?: {
        model_id?: string;
        last_known_token_estimate?: number;
        effective_window?: number;
        context_window?: number;
        target_input_tokens?: number;
        warning_threshold?: number;
        blocking_limit?: number;
        percent_used?: number;
      };
    };
    setMessages(createHistoryMessages(summary.messages ?? []));
    setAllowNetwork(summary.allow_network ?? true);
    setSkills(summary.skills ?? []);
    setFiles(summary.files ?? []);
    setWorkboard(summary.workboard ?? null);
    setElicitation(summary.elicitation ?? null);
    if (summary.context_state && summary.context_state.model_id) {
      setContextStatus({
        model: summary.context_state.model_id,
        estimatedTokens: summary.context_state.last_known_token_estimate ?? 0,
        effectiveWindow: summary.context_state.effective_window ?? 0,
        contextWindow: summary.context_state.context_window ?? 0,
        targetInputTokens: summary.context_state.target_input_tokens ?? 0,
        warningThreshold: summary.context_state.warning_threshold ?? 0,
        blockingLimit: summary.context_state.blocking_limit ?? 0,
        percentUsed: summary.context_state.percent_used ?? 0,
        state: "idle",
        detail: "已恢复上下文状态",
      });
    }
  };

  useEffect(() => {
    const hasBootstrappedLocalSession = Boolean(localSessionId || newlyCreatedSessionRef.current);

    if (isNewSession && !hasBootstrappedLocalSession && !isStreamingRef.current) {
      setLocalSessionId(null);
      newlyCreatedSessionRef.current = null;
      setMessages([]);
      setSkills([]);
      setFiles([]);
      setWorkboard(null);
      setElicitation(null);
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

    shouldStickToBottomRef.current = true;
    pendingSessionBottomScrollRef.current = true;
    setLoading(true);
    setError("");
    void loadSession(targetSessionId)
      .catch((loadError) => {
        setError(loadError instanceof Error ? loadError.message : "初始化失败");
        setMessages([]);
        setSkills([]);
        setFiles([]);
        setWorkboard(null);
        setElicitation(null);
      })
      .finally(() => setLoading(false));
  }, [sessionId, isNewSession, localSessionId]);

const composerDisabled = !(sessionId || localSessionId || isNewSession);

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

  const buildElicitationResponseMessage = (
    request: ElicitationRequest,
    responses: ElicitationResponseItem[],
  ): Extract<ChatMessage, { role: "elicitation_response" }> => ({
    id: `elicitation-response-${Date.now()}`,
    role: "elicitation_response",
    title: request.title,
    summary: request.blocking ? "已提交给 AI，接下来会按你的回答继续执行" : "已提交给 AI，等待后续处理",
    answers: request.questions.map((question) => {
      const answer = responses.find((item) => item.question_id === question.id);
      const parts = [
        ...(answer?.selected_options ?? []),
        ...(answer?.other_text ? [answer.other_text] : []),
        ...(answer?.notes ? [`说明：${answer.notes}`] : []),
      ].filter((item) => item && item.trim().length > 0);
      return {
        id: question.id,
        header: question.header,
        value: parts.join("、") || "未填写",
      };
    }),
  });

  const handleSend = async (userText: string, skipAddUserBubble = false, userBubblesToAdd?: { id: string; content: string }[]) => {
    if (!userText) return;
    
    if (!skipAddUserBubble && isStreamingRef.current) {
      const newMsg = { id: `queued-${Date.now()}`, content: userText, queuedAt: Date.now() };
      setQueuedMessages((current) => [...current, newMsg]);
      queuedMessagesRef.current = [...queuedMessagesRef.current, newMsg];
      return;
    }
    
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

    const roundStartTime = Date.now();

    const abortController = new AbortController();
    abortControllerRef.current = abortController;
    isStreamingRef.current = true;
    shouldStickToBottomRef.current = true;
    setBusy(true);
    setError("");

    if (skipAddUserBubble && userBubblesToAdd && userBubblesToAdd.length > 0) {
      setMessages((current) => [
        ...current,
        ...userBubblesToAdd.map((b) => ({ id: b.id, role: "user" as const, content: b.content })),
        { id: assistantId, role: "assistant", blocks: [], elapsedMs: 0, streaming: true },
      ]);
    } else {
      setMessages((current) => [
        ...current,
        { id: `user-${roundStartTime}`, role: "user", content: userText },
        { id: assistantId, role: "assistant", blocks: [], elapsedMs: 0, streaming: true },
      ]);
    }

    streamingTimerRef.current = window.setInterval(() => {
      const elapsed = Date.now() - roundStartTime;
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantId && item.role === "assistant" ? { ...item, elapsedMs: elapsed } : item,
        ),
      );
    }, 100);

    let activeReasoningId = "";
    let activeContentId = "";
    let activeContentText = "";
    let wasAborted = false;
    let partialContent = "";

    try {
      if (!effectiveSessionId) {
        setError("无法获取会话 ID");
        return;
      }
      await streamChat(effectiveSessionId, userText, allowNetwork, (event) => {
        const payload = (event.payload ?? {}) as Record<string, unknown>;

        if (event.type === "workboard_snapshot" || event.type === "workboard_updated") {
          const snapshot = payload.snapshot as WorkboardState | undefined;
          if (snapshot) setWorkboard(snapshot);
          return;
        }

        if (event.type === "elicitation_snapshot") {
          const snapshot = payload.snapshot as ElicitationState | undefined;
          if (snapshot) setElicitation(snapshot);
          return;
        }

        if (event.type === "ask_requested") {
          const snapshot = payload.snapshot as ElicitationState | undefined;
          if (snapshot) {
            setElicitation(snapshot);
          } else if (payload.request) {
            setElicitation((current) => ({
              session_id: effectiveSessionId,
              revision: (current?.revision ?? 0) + 1,
              pending: payload.request as ElicitationRequest,
              history: current?.history ?? [],
              updated_at: new Date().toISOString(),
            }));
          }
          return;
        }

        if (event.type === "ask_resolved" || event.type === "ask_cancelled") {
          const snapshot = payload.snapshot as ElicitationState | undefined;
          if (snapshot) setElicitation(snapshot);
          return;
        }

        if (event.type === "aborted") {
          wasAborted = true;
          partialContent = String(payload.partial_content ?? "");
          if (partialContent && activeContentId) {
            updateAssistantBlock(assistantId, activeContentId, (block) =>
              block.kind === "content" ? { ...block, content: partialContent, status: "done" } : block,
            );
          }
          return;
        }

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
          const toolName = String(payload.tool_name ?? "");
          const output = (payload.output ?? {}) as Record<string, unknown>;
          if (toolName === "update_workboard") {
            const nextWorkboard = (output.workboard ?? output.snapshot) as WorkboardState | undefined;
            if (nextWorkboard) setWorkboard(nextWorkboard);
          }
          if (toolName === "request_user_input") {
            const nextElicitation = (output.elicitation ?? output.snapshot) as ElicitationState | undefined;
            if (nextElicitation) setElicitation(nextElicitation);
          }
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
      }, abortController.signal);
    } catch (chatError) {
      if (chatError instanceof Error && chatError.name === "AbortError") {
        wasAborted = true;
      } else {
        setError(chatError instanceof Error ? chatError.message : "对话执行失败");
      }
    } finally {
      if (streamingTimerRef.current !== null) {
        window.clearInterval(streamingTimerRef.current);
        streamingTimerRef.current = null;
      }
      const elapsedMs = Date.now() - roundStartTime;
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantId && item.role === "assistant" ? { ...item, elapsedMs, streaming: false } : item,
        ),
      );
      isStreamingRef.current = false;
      abortControllerRef.current = null;
      setBusy(false);
      if (wasNewSession && effectiveSessionId) {
        onSessionCreated?.(effectiveSessionId);
      }
      void onSessionRefresh?.();
      
      const currentQueue = queuedMessagesRef.current;
      if (currentQueue.length > 0) {
        const userBubbles = currentQueue.map((msg, index) => ({
          id: `user-queued-${Date.now() + index}`,
          content: msg.content,
        }));
        const mergedContent = currentQueue.map((msg) => msg.content).join("\n\n");
        queuedMessagesRef.current = [];
        setQueuedMessages([]);
        void handleSend(mergedContent, true, userBubbles);
      }
    }
  };

  const handleStop = async () => {
    if (!abortControllerRef.current || !isStreamingRef.current) return;
    const effectiveSessionId = sessionId || localSessionId;
    if (effectiveSessionId) {
      try {
        await abortSession(effectiveSessionId);
      } catch (err) {
        console.error("中断请求失败:", err);
      }
    }
    abortControllerRef.current.abort();
  };

  const handleElicitationSubmit = async (responses: ElicitationResponseItem[]) => {
    const effectiveSessionId = sessionId || localSessionId;
    const pendingRequest = elicitation?.pending;
    if (!effectiveSessionId || !pendingRequest || isStreamingRef.current) return;

    const assistantId = `assistant-ask-${Date.now()}`;
    const roundStartTime = Date.now();
    const abortController = new AbortController();
    abortControllerRef.current = abortController;
    isStreamingRef.current = true;
    shouldStickToBottomRef.current = true;
    setBusy(true);
    setElicitationBusy(true);
    setError("");

    setMessages((current) => [
      ...current,
      buildElicitationResponseMessage(pendingRequest, responses),
      { id: assistantId, role: "assistant", blocks: [], elapsedMs: 0, streaming: true },
    ]);

    streamingTimerRef.current = window.setInterval(() => {
      const elapsed = Date.now() - roundStartTime;
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantId && item.role === "assistant" ? { ...item, elapsedMs: elapsed } : item,
        ),
      );
    }, 100);

    let activeReasoningId = "";
    let activeContentId = "";
    let activeContentText = "";

    try {
      await streamElicitationResponse(effectiveSessionId, pendingRequest.id, responses, (event) => {
        const payload = (event.payload ?? {}) as Record<string, unknown>;

        if (event.type === "workboard_snapshot" || event.type === "workboard_updated") {
          const snapshot = payload.snapshot as WorkboardState | undefined;
          if (snapshot) setWorkboard(snapshot);
          return;
        }

        if (event.type === "elicitation_snapshot" || event.type === "ask_resolved" || event.type === "ask_cancelled") {
          const snapshot = payload.snapshot as ElicitationState | undefined;
          if (snapshot) setElicitation(snapshot);
          return;
        }

        if (event.type === "ask_requested") {
          const snapshot = payload.snapshot as ElicitationState | undefined;
          if (snapshot) setElicitation(snapshot);
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
          const toolName = String(payload.tool_name ?? "");
          const output = (payload.output ?? {}) as Record<string, unknown>;
          if (toolName === "update_workboard") {
            const nextWorkboard = (output.workboard ?? output.snapshot) as WorkboardState | undefined;
            if (nextWorkboard) setWorkboard(nextWorkboard);
          }
          if (toolName === "request_user_input") {
            const nextElicitation = (output.elicitation ?? output.snapshot) as ElicitationState | undefined;
            if (nextElicitation) setElicitation(nextElicitation);
          }
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
          if (subtype && subtype !== "success") setError(RESULT_MESSAGES[subtype] ?? "鎵ц澶辫触");
          return;
        }

        if (event.type === "error") {
          setError(typeof payload.message === "string" ? payload.message : "鎵ц澶辫触");
        }
      }, abortController.signal);
    } catch (err) {
      if (!(err instanceof Error && err.name === "AbortError")) {
        setError(err instanceof Error ? err.message : "鎻愪氦鍥炵瓟澶辫触");
      }
    } finally {
      if (streamingTimerRef.current !== null) {
        window.clearInterval(streamingTimerRef.current);
        streamingTimerRef.current = null;
      }
      const elapsedMs = Date.now() - roundStartTime;
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantId && item.role === "assistant" ? { ...item, elapsedMs, streaming: false } : item,
        ),
      );
      isStreamingRef.current = false;
      abortControllerRef.current = null;
      setBusy(false);
      setElicitationBusy(false);
      void onSessionRefresh?.();
    }
  };

  const handleRemoveQueued = (id: string) => {
    queuedMessagesRef.current = queuedMessagesRef.current.filter((msg) => msg.id !== id);
    setQueuedMessages(queuedMessagesRef.current);
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

  return (
    <main className="app-layout">
      <WorkbenchSidebar
        conversations={conversations}
        currentUser={currentUser}
        isEmbedMode={isEmbedMode}
        isMobile={isMobile}
        isSidebarOpen={isSidebarOpen}
        isResizingSidebar={isResizingSidebar}
        sidebarWidth={sidebarWidth}
        sidebarView={sidebarView}
        sessionId={sessionId}
        files={files}
        skills={skills}
        onCloseSidebar={() => setIsSidebarOpen(false)}
        onSidebarViewChange={setSidebarView}
        onNewConversation={onNewConversation}
        onSessionSelect={onSessionSelect}
        onRenameSession={onRenameSession}
        onDeleteSession={onDeleteSession}
        onUploadFile={handleUpload}
        onUploadSkill={handleUploadSkill}
        onOpenLlmDialog={() => void openLlmDialog()}
        onAdminToggle={onAdminToggle}
        onLogout={onLogout}
        onSidebarResizeStart={handleSidebarResizeStart}
        getDownloadUrl={(fileId) => getDownloadUrl(sessionId, fileId)}
      />

      <LlmConfigDialog
        open={showLlmDialog}
        llmBusy={llmBusy}
        llmError={llmError}
        llmState={llmState}
        showAdvancedLlmFields={showAdvancedLlmFields}
        onClose={() => setShowLlmDialog(false)}
        onReset={() => void resetUserLlm()}
        onSave={() => void saveUserLlm()}
        onToggleAdvanced={setShowAdvancedLlmFields}
        onChange={(updater) => setLlmState(updater)}
      />

      <section className="main-content">
        <header className="top-nav">
          <div className="nav-left">
            <button className="icon-button subtle nav-trigger" onClick={() => setIsSidebarOpen(!isSidebarOpen)}>
              {isSidebarOpen ? <Icons.SidebarClose /> : <Icons.Menu />}
            </button>
            <span className="session-badge">{isNewSession && !sessionId && !localSessionId ? "新会话" : `Session ID: ${sessionId || localSessionId || "Initializing..."}`}</span>
          </div>
          <div className="nav-right">
            <ContextStatusPill contextStatus={contextStatus} />
          </div>
        </header>

        {error ? <div className="error-toast anim-shake">{error}</div> : null}

        <div className="chat-area" ref={historyRef} onScroll={handleHistoryScroll}>
          <ChatTimeline loading={loading} messages={messages} />
        </div>

        <div className="runtime-panels">
          <WorkboardDock workboard={workboard} busy={busy} />
          <ElicitationPanel request={elicitation?.pending ?? null} busy={busy || elicitationBusy} onSubmit={(responses) => void handleElicitationSubmit(responses)} />
        </div>

        <Composer
          busy={busy}
          disabled={composerDisabled}
          allowNetwork={allowNetwork}
          queuedMessages={queuedMessages}
          onAllowNetworkChange={setAllowNetwork}
          onSend={(text) => void handleSend(text)}
          onStop={() => void handleStop()}
          onRemoveQueued={handleRemoveQueued}
          onUpload={(file) => void handleUpload(file)}
        />
      </section>
    </main>
  );
}
