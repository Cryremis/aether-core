// frontend/src/pages/WorkbenchPage.tsx
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import {
  abortSession,
  bootstrapAdminSession,
  deleteUserLlmConfig,
  editSessionTimeline,
  type ElicitationRequest,
  type ElicitationResponseItem,
  type ElicitationState,
  forkSessionTimeline,
  getDownloadUrl,
  getUserLlmConfig,
  getSessionSummary,
  listFiles,
  listSkills,
  readFileContent,
  rerunSessionTimeline,
  streamElicitationResponse,
  streamChat,
  streamRunEvents,
  type ActiveRunSummary,
  type CommittedChatMessage,
  type WorkboardState,
  updateSessionWorkboard,
  updateUserLlmConfig,
  updateFileContent,
  uploadFile,
  uploadSkill,
  type TranscriptChatMessage,
} from "../api/client";
import { ElicitationPanel } from "../components/ElicitationPanel";
import { WorkboardDock } from "../components/WorkboardDock";
import { ChatTimeline } from "../components/workbench/ChatTimeline";
import { Composer } from "../components/workbench/Composer";
import { ContextStatusPill } from "../components/workbench/ContextStatusPill";
import { LlmConfigDialog } from "../components/workbench/LlmConfigDialog";
import { PersonalSettingsDialog } from "../components/workbench/PersonalSettingsDialog";
import { WorkbenchIcons as Icons } from "../components/workbench/WorkbenchIcons";
import { WorkbenchSidebar } from "../components/workbench/WorkbenchSidebar";
import { useAppPreferences } from "../i18n";
import { formatTokenCount } from "./workbench/markdown";
import type {
  AssistantBlock,
  ChatMessage,
  ContextStatus,
  FileItem,
  LlmDialogState,
  PendingUserEcho,
  QueuedMessage,
  SidebarView,
  SkillItem,
  TranscriptMessage,
  WorkboardOperation,
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

const TEXT_PREVIEW_TYPES = [
  "text/",
  "application/json",
  "application/javascript",
  "application/typescript",
  "application/xml",
  "application/x-yaml",
];

function randomIdSegment() {
  return Math.random().toString(36).slice(2, 8);
}

function buildSystemEventMessage(
  eventType: Extract<ChatMessage, { role: "system_event" }>["eventType"],
  payload: Record<string, unknown>,
): Extract<ChatMessage, { role: "system_event" }> {
  const reason = typeof payload.reason === "string" ? payload.reason : "";
  const titleByType: Record<Extract<ChatMessage, { role: "system_event" }>["eventType"], string> = {
    runtime_created: "沙箱已创建",
    runtime_recreated: "沙箱已重建",
    context_compacted: "上下文已压缩",
    context_recovered: "上下文已恢复",
    context_warning: "上下文接近上限",
    context_blocked: "上下文已阻塞",
  };
  const detail = reason ? `原因: ${reason}` : undefined;
  return {
    id: `system-event-${eventType}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role: "system_event",
    title: titleByType[eventType],
    detail,
    eventType,
  };
}

function fromTranscriptMessages(items: TranscriptChatMessage[]): TranscriptMessage[] {
  return items.map((item, index) =>
    item.role === "assistant"
      ? {
          id: item.id || `history-assistant-${index}`,
          role: "assistant",
          blocks: (item.blocks ?? []).filter((b) => b.kind !== "elapsed").length
            ? ((item.blocks ?? []).filter((b) => b.kind !== "elapsed") as AssistantBlock[])
            : [{ id: `history-content-${index}`, kind: "content", content: "", status: "done" }],
          elapsedMs: item.elapsedMs ?? null,
          streaming: false,
          responseStartedAt: item.responseStartedAt ? Date.parse(item.responseStartedAt) : undefined,
        }
      : item.role === "elicitation_response"
        ? {
            id: item.id || `history-elicitation-response-${index}`,
            role: "elicitation_response",
            request_id: item.request_id,
            title: item.title,
            summary: item.summary,
            answers: item.answers,
          }
        : {
            id: item.id || `history-user-${index}`,
            role: "user",
            content: item.content,
          },
  );
}

function fromCommittedMessage(item: CommittedChatMessage): Extract<TranscriptMessage, { role: "user" | "elicitation_response" }> {
  if (item.role === "elicitation_response") {
    return {
      id: item.id,
      request_id: item.request_id,
      role: "elicitation_response",
      title: item.title,
      summary: item.summary,
      answers: item.answers,
    };
  }
  return {
    id: item.id,
    role: "user",
    content: item.content,
  };
}

function buildRuntimeNoticeBlock(payload: Record<string, unknown>): AssistantBlock {
  const reason = typeof payload.reason === "string" ? payload.reason : "";
  return {
    id: `runtime-notice-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    kind: "runtime_notice",
    eventType: "runtime_recreated",
    title: "沙箱已重建",
    detail: reason || undefined,
  };
}

function stringifyStructured(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function getOpenWorkItemCount(workboard: WorkboardState | null): number {
  if (!workboard) return 0;
  return workboard.items.filter((item) => item.status !== "completed" && item.status !== "cancelled").length;
}

function isTextFile(item: FileItem | null) {
  if (!item) return false;
  const mediaType = item.media_type || "";
  if (TEXT_PREVIEW_TYPES.some((prefix) => mediaType.startsWith(prefix))) return true;
  return /\.(txt|md|json|csv|tsv|xml|yaml|yml|js|jsx|ts|tsx|py|html|css|scss|log|ini|toml)$/i.test(item.name);
}

function appendTranscriptMessage(
  current: TranscriptMessage[],
  message: TranscriptMessage,
): TranscriptMessage[] {
  const existingIndex = current.findIndex((item) => item.id === message.id);
  if (existingIndex >= 0) {
    return current.map((item, index) => (index === existingIndex ? message : item));
  }
  return [...current, message];
}

function insertTranscriptMessageBeforeAssistant(
  current: TranscriptMessage[],
  message: Extract<TranscriptMessage, { role: "user" | "elicitation_response" }>,
  assistantId: string | null,
): TranscriptMessage[] {
  const existingIndex = current.findIndex((item) => item.id === message.id);
  const withoutExisting =
    existingIndex >= 0 ? current.filter((item, index) => index !== existingIndex) : current;
  if (!assistantId) {
    return [...withoutExisting, message];
  }
  const assistantIndex = withoutExisting.findIndex((item) => item.role === "assistant" && item.id === assistantId);
  if (assistantIndex < 0) {
    return [...withoutExisting, message];
  }
  return [
    ...withoutExisting.slice(0, assistantIndex),
    message,
    ...withoutExisting.slice(assistantIndex),
  ];
}

function trimTranscriptForRerun(
  current: TranscriptMessage[],
  anchorMessageId: string,
  editedContent?: string,
): TranscriptMessage[] {
  const anchorIndex = current.findIndex((item) => item.id === anchorMessageId);
  if (anchorIndex < 0) {
    return current;
  }
  return current.slice(0, anchorIndex + 1).map((item, index) => {
    if (index !== anchorIndex || item.role !== "user" || editedContent === undefined) {
      return item;
    }
    return { ...item, content: editedContent };
  });
}


export function WorkbenchPage({
  conversations,
  currentUser,
  isEmbedMode = false,
  sessionId,
  isNewSession = false,
  adminEntryHref,
  onLogout,
  onNewConversation,
  onDeleteSession,
  onRenameSession,
  onSessionCreated,
  onSessionRefresh,
  onSessionSelect,
}: WorkbenchPageProps) {
  const { t } = useAppPreferences();
  const [transcriptMessages, setTranscriptMessages] = useState<TranscriptMessage[]>([]);
  const [pendingUserEcho, setPendingUserEcho] = useState<PendingUserEcho | null>(null);
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [files, setFiles] = useState<FileItem[]>([]);
  const [selectedFile, setSelectedFile] = useState<FileItem | null>(null);
  const [filePreviewContent, setFilePreviewContent] = useState("");
  const [filePreviewLoading, setFilePreviewLoading] = useState(false);
  const [filePreviewSaving, setFilePreviewSaving] = useState(false);
  const [filePreviewError, setFilePreviewError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [sidebarView, setSidebarView] = useState<SidebarView>("sessions");
  const [isSidebarOpen, setIsSidebarOpen] = useState(window.innerWidth > 1024);
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 1024);
  const [sidebarWidth, setSidebarWidth] = useState(SIDEBAR_DEFAULT_WIDTH);
  const [isResizingSidebar, setIsResizingSidebar] = useState(false);
  const [showLlmDialog, setShowLlmDialog] = useState(false);
  const [showPersonalSettingsDialog, setShowPersonalSettingsDialog] = useState(false);
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
  const [workboardVisibilityBySession, setWorkboardVisibilityBySession] = useState<Record<string, boolean>>({});
  const [elicitation, setElicitation] = useState<ElicitationState | null>(null);
  const [elicitationBusy, setElicitationBusy] = useState(false);
  const [showAdvancedLlmFields, setShowAdvancedLlmFields] = useState(false);
  const [localSessionId, setLocalSessionId] = useState<string | null>(null);
  const [contextStatus, setContextStatus] = useState<ContextStatus | null>(null);
  const [queuedMessages, setQueuedMessages] = useState<QueuedMessage[]>([]);
  const queuedMessagesRef = useRef<QueuedMessage[]>([]);
  const transcriptMessagesRef = useRef<TranscriptMessage[]>([]);
  const pendingUserEchoRef = useRef<PendingUserEcho | null>(null);
  const pendingAssistantIdRef = useRef<string | null>(null);
  const isStreamingRef = useRef(false);
  const abortControllerRef = useRef<AbortController | null>(null);
  const liveRunRef = useRef<{ runId: string; assistantId: string } | null>(null);
  const newlyCreatedSessionRef = useRef<string | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const scrollFrameRef = useRef<number | null>(null);
  const bottomAnchorFrameRef = useRef<number | null>(null);
  const bottomAnchorUntilRef = useRef(0);
  const autoScrollingRef = useRef(false);
  const previousScrollTopRef = useRef(0);
  const loadScrollSettleUntilRef = useRef(0);
  const detailsToggleShouldStickRef = useRef(false);
  const pendingSessionBottomScrollRef = useRef(false);
  const workboardOpChainRef = useRef<Promise<void>>(Promise.resolve());
  const workboardOpenCountBySessionRef = useRef<Record<string, number>>({});

  const historyRef = useRef<HTMLDivElement | null>(null);
  const historyContentRef = useRef<HTMLDivElement | null>(null);
  const activeSessionId = sessionId || localSessionId || "";
  const displayedWorkboard = workboard?.session_id === activeSessionId ? workboard : null;
  const pendingElicitationRequest = elicitation?.session_id === activeSessionId ? elicitation.pending : null;
  const workboardVisible = activeSessionId ? (workboardVisibilityBySession[activeSessionId] ?? false) : false;
  const messages: ChatMessage[] = (() => {
    if (!pendingUserEcho) return transcriptMessages;
    const anchorAssistantId = pendingAssistantIdRef.current || liveRunRef.current?.assistantId;
    if (!anchorAssistantId) return [...transcriptMessages, pendingUserEcho];
    const assistantIndex = transcriptMessages.findIndex((item) => item.role === "assistant" && item.id === anchorAssistantId);
    if (assistantIndex < 0) return [...transcriptMessages, pendingUserEcho];
    return [
      ...transcriptMessages.slice(0, assistantIndex),
      pendingUserEcho,
      ...transcriptMessages.slice(assistantIndex),
    ];
  })();

  useEffect(() => {
    pendingUserEchoRef.current = pendingUserEcho;
  }, [pendingUserEcho]);

  useEffect(() => {
    transcriptMessagesRef.current = transcriptMessages;
  }, [transcriptMessages]);

  useEffect(() => {
    if (!activeSessionId) return;

    const nextOpenCount = getOpenWorkItemCount(displayedWorkboard);
    const previousOpenCount = workboardOpenCountBySessionRef.current[activeSessionId];
    workboardOpenCountBySessionRef.current[activeSessionId] = nextOpenCount;

    if (previousOpenCount === undefined) {
      return;
    }

    if (previousOpenCount === 0 && nextOpenCount > 0) {
      setWorkboardVisibilityBySession((current) => ({ ...current, [activeSessionId]: true }));
      return;
    }

    if (previousOpenCount > 0 && nextOpenCount === 0) {
      setWorkboardVisibilityBySession((current) => ({ ...current, [activeSessionId]: false }));
    }
  }, [activeSessionId, displayedWorkboard]);

  const isNearBottom = (node: HTMLDivElement, threshold = 80) => {
    const distanceFromBottom = node.scrollHeight - node.scrollTop - node.clientHeight;
    return distanceFromBottom <= threshold;
  };

  const updateStickToBottom = (node: HTMLDivElement) => {
    shouldStickToBottomRef.current = isNearBottom(node);
  };

  const stopKeepBottom = () => {
    shouldStickToBottomRef.current = false;
    bottomAnchorUntilRef.current = 0;
    if (bottomAnchorFrameRef.current !== null) {
      window.cancelAnimationFrame(bottomAnchorFrameRef.current);
      bottomAnchorFrameRef.current = null;
    }
  };

  const scrollHistoryToBottom = (behavior: ScrollBehavior = "auto") => {
    const node = historyRef.current;
    if (!node) return;
    if (scrollFrameRef.current !== null) window.cancelAnimationFrame(scrollFrameRef.current);
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      autoScrollingRef.current = true;
      node.scrollTo({ top: node.scrollHeight, behavior });
      previousScrollTopRef.current = node.scrollTop;
      window.setTimeout(() => {
        autoScrollingRef.current = false;
      }, 0);
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
        autoScrollingRef.current = true;
        current.scrollTop = current.scrollHeight;
        previousScrollTopRef.current = current.scrollTop;
        window.setTimeout(() => {
          autoScrollingRef.current = false;
        }, 0);
      }

      if (performance.now() < bottomAnchorUntilRef.current && shouldStickToBottomRef.current) {
        bottomAnchorFrameRef.current = window.requestAnimationFrame(tick);
      } else {
        bottomAnchorFrameRef.current = null;
      }
    };

    bottomAnchorFrameRef.current = window.requestAnimationFrame(tick);
  };

  const keepHistoryPinnedToBottom = (durationMs = 320) => {
    shouldStickToBottomRef.current = true;
    scrollHistoryToBottom();
    holdBottomAnchor(durationMs);
  };

  const handleHistoryScroll = () => {
    const node = historyRef.current;
    if (!node) return;
    const currentTop = node.scrollTop;
    const movedUp = currentTop < previousScrollTopRef.current - 2;
    previousScrollTopRef.current = currentTop;
    if (movedUp && !autoScrollingRef.current && !isNearBottom(node)) {
      stopKeepBottom();
      return;
    }
    updateStickToBottom(node);
  };

  const handleHistoryWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    if (event.deltaY < 0) {
      stopKeepBottom();
    }
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
      keepHistoryPinnedToBottom(900);
      return;
    }
    if (!shouldStickToBottomRef.current) return;
    scrollHistoryToBottom();
  }, [messages, loading]);

  useEffect(() => {
    const contentNode = historyContentRef.current;
    if (!contentNode || typeof ResizeObserver === "undefined") return;

    const observer = new ResizeObserver(() => {
      if (!historyRef.current) return;
      const shouldSettle = performance.now() < loadScrollSettleUntilRef.current;
      if (!shouldSettle && !shouldStickToBottomRef.current) return;
      keepHistoryPinnedToBottom(240);
    });

    observer.observe(contentNode);
    return () => observer.disconnect();
  }, []);

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
        keepHistoryPinnedToBottom(320);
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

  const refreshFiles = async (nextSessionId: string) => {
    const fileResult = await listFiles(nextSessionId);
    setFiles((fileResult.items ?? []) as FileItem[]);
  };

  const syncTranscriptFromServer = async (targetSessionId: string) => {
    const summaryResult = await getSessionSummary(targetSessionId);
    const summary = (summaryResult.data ?? {}) as {
      transcript?: TranscriptChatMessage[];
      elicitation?: ElicitationState;
    };
    setTranscriptMessages(fromTranscriptMessages(summary.transcript ?? []));
    setPendingUserEcho(null);
    setElicitation(summary.elicitation ?? null);
  };

  const loadSession = async (nextSessionId: string) => {
    const summaryResult = await getSessionSummary(nextSessionId);
    const summary = (summaryResult.data ?? {}) as {
      transcript?: TranscriptChatMessage[];
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
      active_run?: ActiveRunSummary | null;
    };
    const transcriptMessages = fromTranscriptMessages(summary.transcript ?? []);
    setTranscriptMessages(transcriptMessages);
    setPendingUserEcho(null);
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
    liveRunRef.current = null;
    if (summary.active_run?.status === "running") {
      restoreActiveRunAssistant(summary.active_run);
      return summary.active_run;
    }
    return null;
  };

  const handleWorkboardOps = async (ops: WorkboardOperation[]) => {
    const effectiveSessionId = sessionId || localSessionId;
    if (!effectiveSessionId) {
      throw new Error("当前没有可编辑的会话");
    }

    const run = async () => {
      const result = await updateSessionWorkboard(effectiveSessionId, { ops });
      const nextWorkboard = (result.data ?? null) as WorkboardState | null;
      if (nextWorkboard) {
        setWorkboard(nextWorkboard);
      }
    };

    const queued = workboardOpChainRef.current.then(run);
    workboardOpChainRef.current = queued.catch(() => undefined);
    await queued;
  };

  useEffect(() => {
    const hasBootstrappedLocalSession = Boolean(localSessionId || newlyCreatedSessionRef.current);

    if (isNewSession && !hasBootstrappedLocalSession && !isStreamingRef.current) {
      setLocalSessionId(null);
      newlyCreatedSessionRef.current = null;
      setTranscriptMessages([]);
      setPendingUserEcho(null);
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
    loadScrollSettleUntilRef.current = performance.now() + 1200;
    previousScrollTopRef.current = 0;
    setLoading(true);
    setError("");
    void loadSession(targetSessionId)
      .then((activeRun) => {
        if (!activeRun) return;
        const reconnectAssistantId = activeRun.assistant.id || `live-${activeRun.run_id}`;
        const reconnectAbortController = new AbortController();
        abortControllerRef.current = reconnectAbortController;
        isStreamingRef.current = true;
        setBusy(true);
        const reconnectRefs = {
          activeReasoningId: { value: "" },
          activeContentId: { value: "" },
          activeContentText: { value: "" },
        };
        const handleEvent = buildEventProcessor(reconnectAssistantId, targetSessionId, reconnectRefs);
        void streamRunEvents(targetSessionId, activeRun.run_id, handleEvent, reconnectAbortController.signal)
          .catch((streamError) => {
            if (!(streamError instanceof Error && streamError.name === "AbortError")) {
              setError(streamError instanceof Error ? streamError.message : "恢复运行订阅失败");
            }
          })
          .finally(() => {
            isStreamingRef.current = false;
            abortControllerRef.current = null;
            setBusy(false);
            void onSessionRefresh?.();
          });
      })
      .catch((loadError) => {
        setError(loadError instanceof Error ? loadError.message : "初始化失败");
        setTranscriptMessages([]);
        setPendingUserEcho(null);
        setSkills([]);
        setFiles([]);
        setWorkboard(null);
        setElicitation(null);
      })
      .finally(() => setLoading(false));
  }, [sessionId, isNewSession, localSessionId]);

const composerDisabled = !(sessionId || localSessionId || isNewSession) || Boolean(pendingElicitationRequest?.blocking);

  const appendAssistantBlock = (messageId: string, block: AssistantBlock) => {
    setTranscriptMessages((current) =>
      current.map((item) => (item.id === messageId && item.role === "assistant" ? { ...item, blocks: [...item.blocks, block] } : item)),
    );
  };

  const appendSystemEvent = (eventType: Extract<TranscriptMessage, { role: "system_event" }>["eventType"], payload: Record<string, unknown>) => {
    setTranscriptMessages((current) => [...current, buildSystemEventMessage(eventType, payload)]);
  };

  const appendAssistantRuntimeNotice = (messageId: string, payload: Record<string, unknown>) => {
    setTranscriptMessages((current) =>
      current.map((item) =>
        item.id === messageId && item.role === "assistant"
          ? { ...item, blocks: [...item.blocks, buildRuntimeNoticeBlock(payload)] }
          : item,
      ),
    );
  };

  const updateAssistantBlock = (messageId: string, blockId: string, updater: (block: AssistantBlock) => AssistantBlock) => {
    setTranscriptMessages((current) =>
      current.map((item) =>
        item.id === messageId && item.role === "assistant"
          ? { ...item, blocks: item.blocks.map((block) => (block.id === blockId ? updater(block) : block)) }
          : item,
      ),
    );
  };

  const upsertAssistantBlock = (messageId: string, blockId: string, createBlock: () => AssistantBlock, updater: (block: AssistantBlock) => AssistantBlock) => {
    setTranscriptMessages((current) =>
      current.map((item) => {
        if (item.id !== messageId || item.role !== "assistant") return item;
        let found = false;
        const nextBlocks = item.blocks.map((block) => {
          if (block.id !== blockId) return block;
          found = true;
          return updater(block);
        });
        return {
          ...item,
          blocks: found ? nextBlocks : [...nextBlocks, createBlock()],
        };
      }),
    );
  };

  const upsertAssistantMessage = (
    messageId: string,
    updater: (message: Extract<TranscriptMessage, { role: "assistant" }> | null) => Extract<TranscriptMessage, { role: "assistant" }>,
  ) => {
    setTranscriptMessages((current) => {
      let found = false;
      const next = current.map((item) => {
        if (item.id === messageId && item.role === "assistant") {
          found = true;
          return updater(item);
        }
        return item;
      });
      if (found) {
        return next;
      }
      return [...next, updater(null)];
    });
  };

  const commitPendingMessage = (clientMessageId: string | null, committedMessage: CommittedChatMessage) => {
    const normalized = fromCommittedMessage(committedMessage);
    const anchorAssistantId = pendingAssistantIdRef.current || liveRunRef.current?.assistantId || null;
    if (normalized.role === "user" || normalized.role === "elicitation_response") {
      setTranscriptMessages((current) => {
        const shouldReplaceLastUser =
          normalized.role === "user" && current.some((item) => item.id === normalized.id && item.role === "user");
        if (shouldReplaceLastUser) {
          return current.map((item) => (item.id === normalized.id && item.role === "user" ? normalized : item));
        }
        return insertTranscriptMessageBeforeAssistant(current, normalized, anchorAssistantId);
      });
    } else {
      setTranscriptMessages((current) => appendTranscriptMessage(current, normalized));
    }
    if (clientMessageId && pendingUserEchoRef.current?.id === clientMessageId) {
      setPendingUserEcho(null);
    }
  };

  const restoreActiveRunAssistant = (activeRun: ActiveRunSummary) => {
    const assistantId = activeRun.assistant.id || `live-${activeRun.run_id}`;
    liveRunRef.current = { runId: activeRun.run_id, assistantId };
    const responseStartedAt = Date.parse(activeRun.assistant.response_started_at ?? "");
    const normalizedResponseStartedAt = Number.isFinite(responseStartedAt) ? responseStartedAt : undefined;
    upsertAssistantMessage(assistantId, () => ({
      id: assistantId,
      role: "assistant",
      blocks: (activeRun.assistant.blocks ?? []) as AssistantBlock[],
      elapsedMs: activeRun.assistant.elapsedMs ?? null,
      streaming: activeRun.assistant.streaming,
      responseStartedAt: activeRun.assistant.streaming ? normalizedResponseStartedAt : undefined,
    }));
  };

  const settleAssistantBlocksAborted = (messageId: string, partialContent?: string) => {
    setTranscriptMessages((current) =>
      current.map((item) => {
        if (item.id !== messageId || item.role !== "assistant") return item;
        return {
          ...item,
          blocks: item.blocks.map((block) => {
            if (block.kind === "tool" && block.status === "running") {
              return {
                ...block,
                status: "aborted",
                outputText: block.outputText || JSON.stringify({ summary: "工具执行已停止", aborted: true }, null, 2),
              };
            }
            if (block.kind === "content" && block.status === "streaming") {
              return {
                ...block,
                content: partialContent && partialContent.length > 0 ? partialContent : block.content,
                status: "aborted",
              };
            }
            return block;
          }),
        };
      }),
    );
  };

  const buildEventProcessor = (
    assistantId: string,
    effectiveSessionId: string,
    refs: {
      activeReasoningId: { value: string };
      activeContentId: { value: string };
      activeContentText: { value: string };
      wasAborted?: { value: boolean };
      partialContent?: { value: string };
    },
  ) => {
    return (event: Record<string, unknown>) => {
      const eventType = String(event.type ?? "");
      const payload = (event.payload ?? {}) as Record<string, unknown>;

      if (eventType === "run_started") {
        const runId = String(payload.run_id ?? "");
        if (runId) {
          liveRunRef.current = { runId, assistantId };
        }
        return;
      }

      if (eventType === "assistant_visible_started") {
        setTranscriptMessages((current) =>
          current.map((item) =>
            item.id === assistantId && item.role === "assistant" && item.streaming && !item.responseStartedAt
              ? { ...item, responseStartedAt: Date.now() }
              : item,
          ),
        );
        return;
      }

      if (eventType === "message_committed") {
        const message = payload.message as CommittedChatMessage | undefined;
        const clientMessageId =
          typeof payload.client_message_id === "string" && payload.client_message_id.trim().length > 0
            ? payload.client_message_id
            : null;
        if (message && (message.role === "user" || message.role === "elicitation_response")) {
          commitPendingMessage(clientMessageId, message);
        }
        return;
      }

      if (eventType === "workboard_snapshot" || eventType === "workboard_updated") {
        const snapshot = payload.snapshot as WorkboardState | undefined;
        if (snapshot) setWorkboard(snapshot);
        return;
      }

      if (eventType === "files_snapshot") {
        setFiles((payload.items ?? []) as FileItem[]);
        return;
      }

      if (eventType === "elicitation_snapshot") {
        const snapshot = payload.snapshot as ElicitationState | undefined;
        if (snapshot) setElicitation(snapshot);
        return;
      }

      if (eventType === "ask_requested") {
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

      if (eventType === "ask_resolved" || eventType === "ask_cancelled") {
        const snapshot = payload.snapshot as ElicitationState | undefined;
        if (snapshot) setElicitation(snapshot);
        return;
      }

      if (eventType === "aborted") {
        const partial = String(payload.partial_content ?? "");
        refs.wasAborted && (refs.wasAborted.value = true);
        refs.partialContent && (refs.partialContent.value = partial);
        if (partial && refs.activeContentId.value) {
          updateAssistantBlock(assistantId, refs.activeContentId.value, (block) =>
            block.kind === "content" ? { ...block, content: partial, status: "aborted" } : block,
          );
        }
        settleAssistantBlocksAborted(assistantId, partial);
        return;
      }

      if (eventType === "context_status") {
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

      if (eventType === "context_warning") {
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
      }

      if (eventType === "context_compacted") {
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
      }

      if (eventType === "context_recovered") {
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
      }

      if (eventType === "context_blocked") {
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
      }

      if (eventType === "runtime_recreated") {
        appendAssistantRuntimeNotice(assistantId, payload);
        return;
      }

      if (eventType === "runtime_created") {
        return;
      }

      if (
        eventType === "context_compacted" ||
        eventType === "context_recovered" ||
        eventType === "context_warning" ||
        eventType === "context_blocked"
      ) {
        appendSystemEvent(eventType as Extract<ChatMessage, { role: "system_event" }>["eventType"], payload);
      }

      if (eventType === "reasoning_delta") {
        if (!refs.activeReasoningId.value) {
          refs.activeReasoningId.value = `reasoning-${Date.now()}`;
          appendAssistantBlock(assistantId, {
            id: refs.activeReasoningId.value,
            kind: "reasoning",
            content: String(payload.delta ?? ""),
          });
        } else {
          updateAssistantBlock(assistantId, refs.activeReasoningId.value, (block) =>
            block.kind === "reasoning" ? { ...block, content: `${block.content}${String(payload.delta ?? "")}` } : block,
          );
        }
        return;
      }

      if (eventType === "content_delta") {
        refs.activeContentText.value += String(payload.delta ?? "");
        if (!refs.activeContentId.value) {
          refs.activeContentId.value = `content-${Date.now()}`;
          appendAssistantBlock(assistantId, {
            id: refs.activeContentId.value,
            kind: "content",
            content: refs.activeContentText.value,
            status: "streaming",
          });
        } else {
          updateAssistantBlock(assistantId, refs.activeContentId.value, (block) =>
            block.kind === "content" ? { ...block, content: refs.activeContentText.value, status: "streaming" } : block,
          );
        }
        return;
      }

      if (eventType === "content_completed") {
        if (refs.activeContentId.value && refs.activeContentText.value) {
          updateAssistantBlock(assistantId, refs.activeContentId.value, (block) =>
            block.kind === "content" ? { ...block, content: refs.activeContentText.value, status: "done" } : block,
          );
        }
        refs.activeReasoningId.value = "";
        return;
      }

      if (eventType === "tool_started") {
        refs.activeReasoningId.value = "";
        refs.activeContentId.value = "";
        refs.activeContentText.value = "";
        const displayPayload = (payload.tool_display ?? {}) as Record<string, unknown>;
        if (typeof displayPayload.title !== "string") return;
        appendAssistantBlock(assistantId, {
          id: String(payload.id ?? `tool-${Date.now()}`),
          kind: "tool",
          title: displayPayload.title,
          meta: typeof displayPayload.meta === "string" ? displayPayload.meta : "tool",
          argumentsText: stringifyStructured(payload.input ?? {}),
          outputText: "",
          liveOutputText: "",
          status: "running",
        });
        return;
      }

      if (eventType === "tool_output_delta") {
        const toolId = String(payload.id ?? "");
        if (!toolId) return;
        upsertAssistantBlock(
          assistantId,
          toolId,
          () => ({
            id: toolId,
            kind: "tool",
            title: "运行命令",
            meta: "",
            argumentsText: "",
            outputText: "",
            liveOutputText: String(payload.text ?? ""),
            status: "running",
          }),
          (block) =>
            block.kind === "tool"
              ? {
                  ...block,
                  liveOutputText: `${block.liveOutputText ?? ""}${String(payload.text ?? "")}`,
                  status: "running",
                }
              : block,
        );
        return;
      }

      if (eventType === "tool_finished") {
        const toolName = String(payload.tool_name ?? "");
        const output = payload.output;
        if (toolName === "update_workboard") {
          const nextWorkboard = ((output as Record<string, unknown> | undefined)?.workboard ??
            (output as Record<string, unknown> | undefined)?.snapshot) as WorkboardState | undefined;
          if (nextWorkboard) setWorkboard(nextWorkboard);
        }
        if (toolName === "request_user_input") {
          const nextElicitation = ((output as Record<string, unknown> | undefined)?.elicitation ??
            (output as Record<string, unknown> | undefined)?.snapshot) as ElicitationState | undefined;
          if (nextElicitation) setElicitation(nextElicitation);
        }
        const toolId = String(payload.id ?? "");
        upsertAssistantBlock(
          assistantId,
          toolId,
          () => ({
            id: toolId || `tool-${Date.now()}`,
            kind: "tool",
            title: "运行命令",
            meta: "",
            argumentsText: "",
            outputText: stringifyStructured(output),
            liveOutputText: undefined,
            status:
              typeof output === "object" && output !== null && "aborted" in (output as Record<string, unknown>) && (output as Record<string, unknown>).aborted === true
                ? "aborted"
                : "done",
          }),
          (block) =>
            block.kind === "tool"
              ? {
                  ...block,
                  outputText: stringifyStructured(output),
                  liveOutputText: undefined,
                  status:
                    typeof output === "object" && output !== null && "aborted" in (output as Record<string, unknown>) && (output as Record<string, unknown>).aborted === true
                      ? "aborted"
                      : "done",
                }
              : block,
        );
        return;
      }

      if (eventType === "message" && typeof payload.summary === "string") {
        refs.activeContentText.value = payload.summary;
        if (refs.activeContentId.value) {
          updateAssistantBlock(assistantId, refs.activeContentId.value, (block) =>
            block.kind === "content" ? { ...block, content: payload.summary as string, status: "done" } : block,
          );
        } else {
          refs.activeContentId.value = `content-${Date.now()}`;
          appendAssistantBlock(assistantId, {
            id: refs.activeContentId.value,
            kind: "content",
            content: payload.summary,
            status: "done",
          });
        }
        return;
      }

      if (eventType === "artifact_created") return;

      if (eventType === "result") {
        const subtype = String(payload.subtype ?? "");
        if (subtype && subtype !== "success") setError(RESULT_MESSAGES[subtype] ?? "执行失败");
        return;
      }

      if (eventType === "completed") {
        const elapsedMs = Number(payload.elapsed_ms ?? 0);
        setTranscriptMessages((current) =>
          current.map((item) =>
            item.id === assistantId && item.role === "assistant"
              ? {
                  ...item,
                  elapsedMs: elapsedMs > 0 ? elapsedMs : item.elapsedMs,
                  streaming: false,
                  responseStartedAt: item.responseStartedAt,
                }
              : item,
          ),
        );
        liveRunRef.current = null;
        return;
      }

      if (eventType === "error") {
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
    };
  };

  const buildElicitationResponseMessage = (
    request: ElicitationRequest,
    responses: ElicitationResponseItem[],
    clientMessageId?: string,
  ): PendingUserEcho => ({
    id: clientMessageId || `elicitation-response-${Date.now()}`,
    role: "elicitation_response",
    request_id: request.id,
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

  const handleSend = async (
    userText: string,
    skipAddUserBubble = false,
    pendingUserBubble?: { id: string; content: string },
  ) => {
    if (!userText) return;
    
    if (!skipAddUserBubble && isStreamingRef.current) {
      const newMsg = { id: `queued-${Date.now()}`, content: userText, queuedAt: Date.now() };
      setQueuedMessages((current) => [...current, newMsg]);
      queuedMessagesRef.current = [...queuedMessagesRef.current, newMsg];
      return;
    }
    
    const roundStartTime = Date.now();
    const assistantId = `assistant-${roundStartTime}`;
    const primaryClientMessageId = `client-user-${roundStartTime}-${randomIdSegment()}`;

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

    const abortController = new AbortController();
    abortControllerRef.current = abortController;
    pendingAssistantIdRef.current = assistantId;
    isStreamingRef.current = true;
    shouldStickToBottomRef.current = true;
    setBusy(true);
    setError("");
    setPendingUserEcho(
      skipAddUserBubble && pendingUserBubble
        ? {
            id: pendingUserBubble.id,
            role: "user",
            content: pendingUserBubble.content,
          }
        : {
            id: primaryClientMessageId,
            role: "user",
            content: userText,
          },
    );

    upsertAssistantMessage(assistantId, () => ({
      id: assistantId,
      role: "assistant",
      blocks: [],
      elapsedMs: null,
      streaming: true,
      responseStartedAt: undefined,
    }));

    let activeReasoningId = "";
    let activeContentId = "";
    let activeContentText = "";
    let wasAborted = false;
    let partialContent = "";
    const eventRefs = {
      activeReasoningId: {
        get value() {
          return activeReasoningId;
        },
        set value(value: string) {
          activeReasoningId = value;
        },
      },
      activeContentId: {
        get value() {
          return activeContentId;
        },
        set value(value: string) {
          activeContentId = value;
        },
      },
      activeContentText: {
        get value() {
          return activeContentText;
        },
        set value(value: string) {
          activeContentText = value;
        },
      },
      wasAborted: {
        get value() {
          return wasAborted;
        },
        set value(value: boolean) {
          wasAborted = value;
        },
      },
      partialContent: {
        get value() {
          return partialContent;
        },
        set value(value: string) {
          partialContent = value;
        },
      },
    };

    try {
      if (!effectiveSessionId) {
        setError("无法获取会话 ID");
        return;
      }
      const handleEvent = buildEventProcessor(assistantId, effectiveSessionId, eventRefs);
      await streamChat(effectiveSessionId, userText, allowNetwork, handleEvent, abortController.signal, {
        clientMessageId: skipAddUserBubble && pendingUserBubble
          ? pendingUserBubble.id
          : primaryClientMessageId,
      });
    } catch (chatError) {
      if (chatError instanceof Error && chatError.name === "AbortError") {
        wasAborted = true;
      } else {
        setError(chatError instanceof Error ? chatError.message : "对话执行失败");
      }
    } finally {
      setTranscriptMessages((current) =>
        current.map((item) =>
          item.id === assistantId && item.role === "assistant"
            ? { ...item, streaming: false, responseStartedAt: item.responseStartedAt }
            : item,
        ),
      );
      isStreamingRef.current = false;
      abortControllerRef.current = null;
      pendingAssistantIdRef.current = null;
      setBusy(false);
      setPendingUserEcho((current) => {
        if (!current) return null;
        const expectedId = skipAddUserBubble && pendingUserBubble ? pendingUserBubble.id : primaryClientMessageId;
        return current.id === expectedId ? null : current;
      });
      if (wasNewSession && effectiveSessionId) {
        onSessionCreated?.(effectiveSessionId);
      }
      void onSessionRefresh?.(effectiveSessionId || undefined);
      
      const currentQueue = queuedMessagesRef.current;
      if (currentQueue.length > 0) {
        const mergedContent = currentQueue.map((msg) => msg.content).join("\n\n");
        const mergedBubble = {
          id: `client-user-queued-${Date.now()}-${randomIdSegment()}`,
          content: mergedContent,
        };
        queuedMessagesRef.current = [];
        setQueuedMessages([]);
        void handleSend(mergedContent, true, mergedBubble);
      }
    }
  };

  const rerunFromTimeline = async (messageId: string, promptOverride?: string) => {
    const effectiveSessionId = sessionId || localSessionId;
    if (!effectiveSessionId || isStreamingRef.current) return;
    const previousTranscript = transcriptMessagesRef.current;

    setBusy(true);
    setError("");
    shouldStickToBottomRef.current = true;

    if (promptOverride !== undefined) {
      setTranscriptMessages((current) => trimTranscriptForRerun(current, messageId, promptOverride));
    }

    try {
      const rerunResult = promptOverride === undefined
        ? await rerunSessionTimeline(effectiveSessionId, messageId)
        : await editSessionTimeline(effectiveSessionId, messageId, promptOverride);

      setTranscriptMessages((current) =>
        trimTranscriptForRerun(current, rerunResult.anchor_message_id, promptOverride),
      );

      const assistantId = `assistant-rerun-${Date.now()}`;
      const roundStartTime = Date.now();
      const abortController = new AbortController();
      abortControllerRef.current = abortController;
      pendingAssistantIdRef.current = assistantId;
      isStreamingRef.current = true;

      upsertAssistantMessage(assistantId, () => ({
        id: assistantId,
        role: "assistant",
        blocks: [],
        elapsedMs: null,
        streaming: true,
        responseStartedAt: undefined,
      }));

      let activeReasoningId = "";
      let activeContentId = "";
      let activeContentText = "";
      let wasAborted = false;
      let partialContent = "";
      const eventRefs = {
        activeReasoningId: {
          get value() {
            return activeReasoningId;
          },
          set value(value: string) {
            activeReasoningId = value;
          },
        },
        activeContentId: {
          get value() {
            return activeContentId;
          },
          set value(value: string) {
            activeContentId = value;
          },
        },
        activeContentText: {
          get value() {
            return activeContentText;
          },
          set value(value: string) {
            activeContentText = value;
          },
        },
        wasAborted: {
          get value() {
            return wasAborted;
          },
          set value(value: boolean) {
            wasAborted = value;
          },
        },
        partialContent: {
          get value() {
            return partialContent;
          },
          set value(value: string) {
            partialContent = value;
          },
        },
      };

      try {
        const handleEvent = buildEventProcessor(assistantId, effectiveSessionId, eventRefs);
        await streamChat(
          effectiveSessionId,
          rerunResult.rerun_prompt,
          allowNetwork,
          handleEvent,
          abortController.signal,
          { replaceLastUserMessage: true },
        );
      } catch (chatError) {
        if (chatError instanceof Error && chatError.name === "AbortError") {
          wasAborted = true;
        } else {
          setError(chatError instanceof Error ? chatError.message : "重跑失败");
        }
      } finally {
        setTranscriptMessages((current) =>
          current.map((item) =>
            item.id === assistantId && item.role === "assistant"
              ? { ...item, streaming: false, responseStartedAt: item.responseStartedAt }
              : item,
          ),
        );
        isStreamingRef.current = false;
        abortControllerRef.current = null;
        pendingAssistantIdRef.current = null;
        if (wasAborted) {
          setError("重跑已中断");
        }
        void onSessionRefresh?.(effectiveSessionId);
      }
    } catch (err) {
      if (promptOverride !== undefined) {
        setTranscriptMessages(previousTranscript);
      }
      setError(err instanceof Error ? err.message : "重跑失败");
    } finally {
      setBusy(false);
    }
  };

  const handleForkFromMessage = async (messageId: string) => {
    const effectiveSessionId = sessionId || localSessionId;
    if (!effectiveSessionId || isStreamingRef.current) return;
    try {
      setBusy(true);
      setError("");
      const result = await forkSessionTimeline(effectiveSessionId, messageId);
      onSessionCreated?.(result.session_id);
      onSessionSelect?.(result.session_id);
      void onSessionRefresh?.(result.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "分叉失败");
    } finally {
      setBusy(false);
    }
  };

  const handleRerunFromMessage = async (messageId: string) => {
    await rerunFromTimeline(messageId);
  };

const handleEditUserMessage = async (messageId: string, editedContent: string) => {
    if (!editedContent.trim()) {
      setError("消息不能为空");
      return;
    }
    await rerunFromTimeline(messageId, editedContent);
  };

  const handleStop = async () => {
    if (!abortControllerRef.current || !isStreamingRef.current) return;
    const effectiveSessionId = sessionId || localSessionId;
    if (effectiveSessionId) {
      try {
        await abortSession(effectiveSessionId);
      } catch (err) {
        console.error("中断请求失败:", err);
        abortControllerRef.current.abort();
      }
    }
  };

  const handleElicitationSubmit = async (responses: ElicitationResponseItem[]) => {
    const effectiveSessionId = sessionId || localSessionId;
    const pendingRequest = elicitation?.pending;
    if (!effectiveSessionId || !pendingRequest || isStreamingRef.current) return;

    const assistantId = `assistant-ask-${Date.now()}`;
    const roundStartTime = Date.now();
    const abortController = new AbortController();
    abortControllerRef.current = abortController;
    pendingAssistantIdRef.current = assistantId;
    isStreamingRef.current = true;
    shouldStickToBottomRef.current = true;
    setBusy(true);
    setElicitationBusy(true);
    setError("");
    const clientMessageId = `client-elicitation-${Date.now()}-${randomIdSegment()}`;
    setPendingUserEcho(buildElicitationResponseMessage(pendingRequest, responses, clientMessageId));

    upsertAssistantMessage(assistantId, () => ({
      id: assistantId,
      role: "assistant",
      blocks: [],
      elapsedMs: null,
      streaming: true,
      responseStartedAt: undefined,
    }));
    setElicitation((current) =>
      current
        ? {
            ...current,
            revision: current.revision + 1,
            pending: null,
            updated_at: new Date().toISOString(),
          }
        : current,
    );

    let activeReasoningId = "";
    let activeContentId = "";
    let activeContentText = "";
    const eventRefs = {
      activeReasoningId: {
        get value() {
          return activeReasoningId;
        },
        set value(value: string) {
          activeReasoningId = value;
        },
      },
      activeContentId: {
        get value() {
          return activeContentId;
        },
        set value(value: string) {
          activeContentId = value;
        },
      },
      activeContentText: {
        get value() {
          return activeContentText;
        },
        set value(value: string) {
          activeContentText = value;
        },
      },
    };

    try {
      const handleEvent = buildEventProcessor(assistantId, effectiveSessionId, eventRefs);
      await streamElicitationResponse(
        effectiveSessionId,
        pendingRequest.id,
        responses,
        handleEvent,
        abortController.signal,
        { clientMessageId },
      );
    } catch (err) {
      if (!(err instanceof Error && err.name === "AbortError")) {
        setError(err instanceof Error ? err.message : "鎻愪氦鍥炵瓟澶辫触");
      }
    } finally {
      setTranscriptMessages((current) =>
        current.map((item) =>
          item.id === assistantId && item.role === "assistant"
            ? { ...item, streaming: false, responseStartedAt: item.responseStartedAt }
            : item,
        ),
      );
      isStreamingRef.current = false;
      abortControllerRef.current = null;
      pendingAssistantIdRef.current = null;
      setBusy(false);
      setElicitationBusy(false);
      setPendingUserEcho((current) => (current?.id === clientMessageId ? null : current));
      void onSessionRefresh?.(effectiveSessionId);
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
      await refreshFiles(sessionId);
      void onSessionRefresh?.(sessionId);
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
      const skillResult = await listSkills(sessionId);
      setSkills((skillResult.data ?? []) as SkillItem[]);
      void onSessionRefresh?.(sessionId);
      setSidebarView("skills");
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "技能上传失败");
    }
  };

  const handlePreviewFile = async (file: FileItem) => {
    setSelectedFile(file);
    setFilePreviewContent("");
    setFilePreviewError("");
    if (!sessionId) return;
    if (!isTextFile(file)) {
      setFilePreviewError("这个文件类型暂不支持文本预览，可以直接下载查看。");
      return;
    }
    try {
      setFilePreviewLoading(true);
      const result = await readFileContent(sessionId, file.file_id);
      const data = (result.data ?? {}) as { content?: string };
      setFilePreviewContent(data.content ?? "");
    } catch (err) {
      setFilePreviewError(err instanceof Error ? err.message : "读取文件失败");
    } finally {
      setFilePreviewLoading(false);
    }
  };

  const handleSaveFilePreview = async () => {
    if (!sessionId || !selectedFile) return;
    if (selectedFile.category === "platform") {
      setFilePreviewError("共享平台基线文件为只读资源，请到平台基线工作区内修改。");
      return;
    }
    try {
      setFilePreviewSaving(true);
      setFilePreviewError("");
      const result = await updateFileContent(sessionId, selectedFile.file_id, filePreviewContent);
      const data = (result.data ?? {}) as { items?: FileItem[] };
      if (data.items) setFiles(data.items);
    } catch (err) {
      setFilePreviewError(err instanceof Error ? err.message : "保存文件失败");
    } finally {
      setFilePreviewSaving(false);
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
        onOpenPersonalSettings={() => setShowPersonalSettingsDialog(true)}
        onOpenLlmDialog={() => void openLlmDialog()}
        adminEntryHref={adminEntryHref}
        onLogout={onLogout}
        onSidebarResizeStart={handleSidebarResizeStart}
        getDownloadUrl={(fileId) => getDownloadUrl(sessionId, fileId)}
        onPreviewFile={(file) => void handlePreviewFile(file)}
      />

      {selectedFile ? (
        <aside className="file-preview-drawer">
          <div className="file-preview-drawer__header">
            <div>
              <h3>{selectedFile.name}</h3>
              <p>{selectedFile.relative_path || "work"}</p>
            </div>
            <button type="button" className="icon-button" onClick={() => setSelectedFile(null)} title="关闭">
              <Icons.Close />
            </button>
          </div>
          {filePreviewError ? <div className="file-preview-drawer__error">{filePreviewError}</div> : null}
          {filePreviewLoading ? (
            <div className="file-preview-drawer__placeholder">正在读取文件...</div>
          ) : isTextFile(selectedFile) ? (
            <textarea
              className="file-preview-drawer__editor"
              value={filePreviewContent}
              onChange={(event) => setFilePreviewContent(event.target.value)}
              readOnly={selectedFile.category === "platform"}
              spellCheck={false}
            />
          ) : (
            <div className="file-preview-drawer__placeholder">当前文件只能下载查看。</div>
          )}
          <div className="file-preview-drawer__footer">
            <a className="action-button small" href={getDownloadUrl(sessionId, selectedFile.file_id)} target="_blank" rel="noreferrer">
              下载
            </a>
            <button
              type="button"
              className="action-button small"
              disabled={!isTextFile(selectedFile) || selectedFile.category === "platform" || filePreviewSaving || filePreviewLoading}
              onClick={() => void handleSaveFilePreview()}
            >
              {filePreviewSaving ? "保存中" : "保存"}
            </button>
          </div>
        </aside>
      ) : null}

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

      <PersonalSettingsDialog
        currentUser={currentUser}
        open={showPersonalSettingsDialog}
        onClose={() => setShowPersonalSettingsDialog(false)}
        onLogout={onLogout}
      />

      <section className="main-content">
        <header className="top-nav">
          <div className="nav-left">
            <button className="icon-button subtle nav-trigger" onClick={() => setIsSidebarOpen(!isSidebarOpen)}>
              {isSidebarOpen ? <Icons.SidebarClose /> : <Icons.Menu />}
            </button>
            <span className="session-badge">{isNewSession && !sessionId && !localSessionId ? t("workbench.session.new") : `Session ID: ${sessionId || localSessionId || t("workbench.session.initializing")}`}</span>
          </div>
          <div className="nav-right">
            <ContextStatusPill contextStatus={contextStatus} />
          </div>
        </header>

        {error ? <div className="error-toast anim-shake">{error}</div> : null}

        <div className="chat-area" ref={historyRef} onScroll={handleHistoryScroll} onWheel={handleHistoryWheel}>
          <ChatTimeline
            contentRef={historyContentRef}
            loading={loading}
            messages={messages}
            actionsDisabled={busy || loading}
            onForkUserMessage={(messageId) => void handleForkFromMessage(messageId)}
            onRerunFromMessage={(messageId) => void handleRerunFromMessage(messageId)}
            onEditUserMessage={(messageId, content) => void handleEditUserMessage(messageId, content)}
          />
        </div>

        <div className="runtime-panels">
          <WorkboardDock
            workboard={displayedWorkboard}
            visible={workboardVisible}
            busy={busy}
            onToggle={() => {
              if (!activeSessionId) return;
              setWorkboardVisibilityBySession((current) => ({
                ...current,
                [activeSessionId]: false,
              }));
            }}
            onApplyOps={handleWorkboardOps}
          />
          <ElicitationPanel
            request={pendingElicitationRequest}
            busy={busy || elicitationBusy}
            onSubmit={(responses) => void handleElicitationSubmit(responses)}
          />
        </div>

        <Composer
          busy={busy}
          disabled={composerDisabled}
          allowNetwork={allowNetwork}
          queuedMessages={queuedMessages}
          workboardVisible={workboardVisible}
          workboardCount={displayedWorkboard?.items?.length ?? 0}
          workboardCompleted={displayedWorkboard?.items?.filter((i) => i.status === "completed").length ?? 0}
          onAllowNetworkChange={setAllowNetwork}
          onWorkboardToggle={() =>
            activeSessionId
              ? setWorkboardVisibilityBySession((current) => ({
                  ...current,
                  [activeSessionId]: !(current[activeSessionId] ?? false),
                }))
              : undefined
          }
          onSend={(text) => void handleSend(text)}
          onStop={() => void handleStop()}
          onRemoveQueued={handleRemoveQueued}
          onUpload={(file) => void handleUpload(file)}
        />
      </section>
    </main>
  );
}
