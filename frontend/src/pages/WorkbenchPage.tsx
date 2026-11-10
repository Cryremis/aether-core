import { samplingFromForm, loadAdvancedSampling } from "../api/llmOptions";
// frontend/src/pages/WorkbenchPage.tsx
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import {
  abortSession,
  bootstrapAdminSession,
  cancelSubagent,
  deleteUserLlmConfig,
  destroySubagent,
  editSessionTimeline,
  type ElicitationRequest,
  type ElicitationResponseItem,
  type ElicitationState,
  forkSessionTimeline,
  getDownloadUrl,
  getUserLlmConfig,
  getSessionSummary,
  getOlderTranscript,
  TranscriptChangedError,
  type WorkbenchTranscriptPage,
  getSessionElicitation,
  getCrossChatInbox,
  inspectSession,
  listSubagents,
  listFiles,
  listSkills,
  readFileContent,
  rerunSessionTimeline,
  streamElicitationResponse,
  streamChat,
  streamRunEvents,
  ServiceRestartingError,
  SessionRunConflictError,
  type ActiveRunSummary,
  type CommittedChatMessage,
  type SubagentRunSummary,
  type WorkboardState,
  updateSessionWorkboard,
  updateUserLlmConfig,
  updateFileContent,
  uploadFile,
  uploadSkill,
  type TranscriptChatMessage,
} from "../api/client";
import { RunStreamInterruptedError } from "../api/sse";
import { ElicitationPanel } from "../components/ElicitationPanel";
import { WorkboardDock } from "../components/WorkboardDock";
import { ChatTimeline } from "../components/workbench/ChatTimeline";
import { Composer } from "../components/workbench/Composer";
import { ContextStatusPill } from "../components/workbench/ContextStatusPill";
import { LlmConfigDialog } from "../components/workbench/LlmConfigDialog";
import { PersonalSettingsDialog } from "../components/workbench/PersonalSettingsDialog";
import { SessionInspectionDialog } from "../components/workbench/SessionInspectionDialog";
import { CrossChatDeliveriesDialog } from "../components/workbench/CrossChatDeliveriesDialog";
import { WorkbenchIcons as Icons } from "../components/workbench/WorkbenchIcons";
import { WorkbenchSidebar } from "../components/workbench/WorkbenchSidebar";
import { useAppPreferences } from "../i18n";
import { formatTokenCount } from "./workbench/markdown";
import type { CommandAvailability, WorkbenchCommand } from "./workbench/commands";
import { requestHostToolRefresh } from "./workbench/hostToolRefresh";
import { TranscriptStore } from "./workbench/transcriptStore";
import { applyLegacyQuestion, mergeElicitationSnapshot } from "./workbench/elicitation";
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

const MODEL_FAILURE_MESSAGES: Record<string, string> = {
  MODEL_ACCESS_DENIED: "模型服务授权不可用，请联系管理员检查权限。已有内容已保留。",
  MODEL_CONFIGURATION_ERROR: "模型服务拒绝了请求，请联系管理员检查模型配置。已有内容已保留。",
  MODEL_RATE_LIMITED: "模型服务持续限流，已有内容已保留。请稍后发送新消息继续。",
  MODEL_UNAVAILABLE: "模型服务连接未能恢复，已有内容已保留。可以发送新消息重试。",
  MODEL_RESPONSE_INVALID: "模型响应未能完整恢复，已有内容已保留。可以发送新消息重试。",
  RUN_INTERRUPTED_RESTART: "服务重启中断了这一轮，已有内容已保留。可以发送新消息继续。",
};

const RESULT_MESSAGES: Record<string, string> = {
  incomplete: "回复未完整生成，已保留收到的内容，可继续追问",
  error_empty_response: "模型未返回可用正文",
  error_max_turns: "执行达到轮次上限",
  error_runtime_limit: "执行达到运行时限",
  error_stalled: "Agent 陷入重复调用循环，已注入提示并继续",
  error_context_overflow: "对话过长，上下文窗口超限",
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
  let detail = reason ? `原因: ${reason}` : undefined;
  if (eventType === "context_compacted") {
    const before = Number(payload.tokens_before ?? 0);
    const after = Number(payload.tokens_after ?? 0);
    detail = `输入估算 ${before.toLocaleString("en-US")} → ${after.toLocaleString("en-US")} tokens`;
  }
  return {
    id: typeof payload.compaction_id === "string" ? payload.compaction_id
      : `system-event-${eventType}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role: "system_event",
    title: titleByType[eventType],
    detail,
    eventType,
    timestamp: typeof payload.timestamp === "string" ? payload.timestamp : undefined,
  };
}

function fromTranscriptMessages(items: TranscriptChatMessage[]): TranscriptMessage[] {
  return items.map((item, index) =>
    item.role === "system_event" ? { ...item } : item.role === "assistant"
      ? {
          id: item.id || `history-assistant-${index}`,
          role: "assistant",
          blocks: (item.blocks ?? []).filter((b) => b.kind !== "elapsed").length
            ? ((item.blocks ?? []).filter((b) => b.kind !== "elapsed") as AssistantBlock[])
            : [{ id: `history-content-${index}`, kind: "content", content: "", status: "done" }],
          elapsedMs: item.elapsedMs ?? null,
          streaming: false,
          responseStartedAt: item.responseStartedAt ? Date.parse(item.responseStartedAt) : undefined,
          timestamp: item.timestamp,
        }
      : item.role === "elicitation_response"
        ? {
            id: item.id || `history-elicitation-response-${index}`,
            role: "elicitation_response",
            request_id: item.request_id,
            title: item.title,
            summary: item.summary,
            answers: item.answers,
            timestamp: item.timestamp,
          }
        : {
            id: item.id || `history-user-${index}`,
            role: "user",
            content: item.content,
            timestamp: item.timestamp,
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
  message: TranscriptMessage,
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


type LiveRunRef = { current: { runId: string; assistantId: string } | null };

export function WorkbenchPage({
  conversations,
  currentUser,
  isEmbedMode = false,
  embedHostOrigin = "",
  embedSupportsToolRefresh = false,
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
  onAssistantPreview,
}: WorkbenchPageProps) {
  const { t, theme, setTheme, themeLocked, notifyOnComplete } = useAppPreferences();
  const [transcriptStore] = useState(() => new TranscriptStore());
  const transcriptMessages = useSyncExternalStore(transcriptStore.subscribe, transcriptStore.snapshot);
  const setTranscriptMessages = transcriptStore.set;
  const [olderCursor, setOlderCursor] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState("");
  const historyPageRequest = useRef<AbortController | null>(null);
  const olderPageRequest = useRef<AbortController | null>(null);
  const transcriptOwnerRef = useRef("");
  const transcriptPageRef = useRef<WorkbenchTranscriptPage["transcript_page"] | null>(null);
  const scrollToLatestRef = useRef<(() => void) | null>(null);
  const [pendingUserEcho, setPendingUserEcho] = useState<PendingUserEcho | null>(null);
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [files, setFiles] = useState<FileItem[]>([]);
  const [selectedFile, setSelectedFile] = useState<FileItem | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [draggedNames, setDraggedNames] = useState<string[]>([]);
  const [overlayVisible, setOverlayVisible] = useState(false);
  const overlayExitTimer = useRef<number | null>(null);

  // --- 任务完成通知所需的状态追踪 ---
  const hostNameRef = useRef<string>("");
  const tabVisibleRef = useRef(!document.hidden);
  const embedDrawerOpenRef = useRef(true);

  useEffect(() => {
    return () => { if (overlayExitTimer.current) window.clearTimeout(overlayExitTimer.current); };
  }, []);

  useEffect(() => {
    const handler = () => { tabVisibleRef.current = !document.hidden; };
    document.addEventListener("visibilitychange", handler);
    return () => document.removeEventListener("visibilitychange", handler);
  }, []);

  useEffect(() => {
    // 嵌入式: 宿主 loader 在抽屉开/合时推送状态(非嵌入式恒为 true,不影响判定)
    const handler = (event: MessageEvent) => {
      if (event.source !== window.parent) return;
      const data = event.data as { source?: string; type?: string; payload?: { open?: boolean } } | null;
      if (!data || data.source !== "aethercore-host" || data.type !== "aethercore:drawer") return;
      embedDrawerOpenRef.current = Boolean(data.payload?.open);
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, []);
  const [filePreviewContent, setFilePreviewContent] = useState("");
  const [filePreviewLoading, setFilePreviewLoading] = useState(false);
  const [filePreviewSaving, setFilePreviewSaving] = useState(false);
  const [filePreviewError, setFilePreviewError] = useState("");
  const [busy, setBusy] = useState(false);
  const [stoppingSessionId, setStoppingSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [connectionNotice, setConnectionNotice] = useState("");
  const [reconnectAvailable, setReconnectAvailable] = useState(false);
  const [sessionReload, setSessionReload] = useState(0);
  const reloadTargetRef = useRef<string | null>(null);
  const crossChatInboxRef = useRef<{ sessionId: string; cursor: string | null } | null>(null);
  const requestSessionReload = (target: string) => {
    reloadTargetRef.current = target;
    setSessionReload((value) => value + 1);
  };
    // 支持通过 ?sidebar=schedules 深链到指定工作台侧栏，便于用户和测试直达定时任务。
    const [sidebarView, setSidebarView] = useState<SidebarView>(() => {
      const view = new URLSearchParams(window.location.search).get("sidebar");
      return view === "schedules" ? "schedules" : "sessions";
    });
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
    sampling_temperature: "",
    sampling_frequency_penalty: "",
    sampling_presence_penalty: "",
    sampling_top_p: "",
    sampling_repetition_penalty: "",
  });
  const [allowNetwork, setAllowNetwork] = useState(true);
  const [reasoningEffort, setReasoningEffort] = useState("");
  const [reasoningEffortOptions, setReasoningEffortOptions] = useState<string[]>(["low", "medium", "high", "max"]);
  const [workboard, setWorkboard] = useState<WorkboardState | null>(null);
  const [workboardVisibilityBySession, setWorkboardVisibilityBySession] = useState<Record<string, boolean>>({});
  const [subagents, setSubagents] = useState<SubagentRunSummary[]>([]);
  const [subagentPopoverOpen, setSubagentPopoverOpen] = useState(false);
  const [activeSubagentSessionId, setActiveSubagentSessionId] = useState<string | null>(
    () => new URLSearchParams(window.location.search).get("subagent_session_id"),
  );
  const [activeSubagentName, setActiveSubagentName] = useState("");
  const [childBusy, setChildBusy] = useState(false);
  const [elicitation, setElicitation] = useState<ElicitationState | null>(null);
  const [elicitationBusy, setElicitationBusy] = useState(false);
  const submittingElicitationRef = useRef(false);
  const [showAdvancedLlmFields, setShowAdvancedLlmFields] = useState(false);
  const [localSessionId, setLocalSessionId] = useState<string | null>(null);
  const [contextStatus, setContextStatus] = useState<ContextStatus | null>(null);
  const [compactionProgress, setCompactionProgress] = useState("");
  const [inspection, setInspection] = useState<{ sessionId: string; resource: "context/status" | "tools" } | null>(null);
  const [deliverySessionId, setDeliverySessionId] = useState<string | null>(null);
  const [refreshNotice, setRefreshNotice] = useState<{ sessionId: string; text: string } | null>(null);
  const [refreshingTools, setRefreshingTools] = useState<string | null>(null);
  const toolRefreshRef = useRef<string | null>(null);
  const mountedRef = useRef(true);
  const [queuedMessages, setQueuedMessages] = useState<QueuedMessage[]>([]);
  const queuedMessagesRef = useRef<QueuedMessage[]>([]);
  const pendingUserEchoRef = useRef<PendingUserEcho | null>(null);
  const pendingAssistantIdRef = useRef<string | null>(null);
  const isStreamingRef = useRef(false);
  const childAbortControllerRef = useRef<AbortController | null>(null);
  const activeViewSessionIdRef = useRef("");
  const returningFromSubagentRef = useRef(false);
  const lastMainSessionIdRef = useRef<string | null>(null);

  const notifyHost = (type: "aethercore:run-status" | "aethercore:tool-status", payload: Record<string, unknown>) => {
    if (!mountedRef.current || !isEmbedMode || !embedHostOrigin || window.parent === window) return;
    window.parent.postMessage({ source: "aethercore-workbench", type, payload }, embedHostOrigin);
  };
  const abortControllerRef = useRef<AbortController | null>(null);
  const liveRunRef = useRef<{ runId: string; assistantId: string } | null>(null);
  const childLiveRunRef = useRef<{ runId: string; assistantId: string } | null>(null);
  const lastRunIdRef = useRef("");
  const newlyCreatedSessionRef = useRef<string | null>(null);
  const workboardOpChainRef = useRef<Promise<void>>(Promise.resolve());
  const workboardOpenCountBySessionRef = useRef<Record<string, number>>({});

  const [historyElement, setHistoryElement] = useState<HTMLDivElement | null>(null);
  const mainSessionId = sessionId || localSessionId || "";
  const isSubagentView = Boolean(activeSubagentSessionId);
  const activeSessionId = activeSubagentSessionId || mainSessionId;
  const activeSessionIdRef = useRef(activeSessionId);
  activeSessionIdRef.current = activeSessionId;
  const displayedBusy = isSubagentView ? childBusy : busy;
  const displayedWorkboard = !isSubagentView && workboard?.session_id === mainSessionId ? workboard : null;
  const pendingElicitationRequest = !isSubagentView && elicitation?.session_id === mainSessionId ? elicitation.pending : null;
  const workboardVisible = !isSubagentView && mainSessionId ? (workboardVisibilityBySession[mainSessionId] ?? false) : false;
  const messages = useMemo<ChatMessage[]>(() => {
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
  }, [pendingUserEcho, transcriptMessages]);

  useEffect(() => {
    activeViewSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  useEffect(() => {
    if (lastMainSessionIdRef.current === null) {
      lastMainSessionIdRef.current = mainSessionId;
      return;
    }
    if (lastMainSessionIdRef.current !== mainSessionId && activeSubagentSessionId) {
      childAbortControllerRef.current?.abort();
      activeViewSessionIdRef.current = mainSessionId;
      setActiveSubagentSessionId(null);
      setActiveSubagentName("");
      updateSubagentUrl(null);
    }
    lastMainSessionIdRef.current = mainSessionId;
  }, [activeSubagentSessionId, mainSessionId]);

  useEffect(() => {
    pendingUserEchoRef.current = pendingUserEcho;
  }, [pendingUserEcho]);

  useEffect(() => {
    if (!mainSessionId || isSubagentView) return;

    const nextOpenCount = getOpenWorkItemCount(displayedWorkboard);
    const previousOpenCount = workboardOpenCountBySessionRef.current[mainSessionId];
    workboardOpenCountBySessionRef.current[mainSessionId] = nextOpenCount;

    if (previousOpenCount === undefined) {
      return;
    }

    if (previousOpenCount === 0 && nextOpenCount > 0) {
      setWorkboardVisibilityBySession((current) => ({ ...current, [mainSessionId]: true }));
      return;
    }

    if (previousOpenCount > 0 && nextOpenCount === 0) {
      setWorkboardVisibilityBySession((current) => ({ ...current, [mainSessionId]: false }));
    }
  }, [displayedWorkboard, isSubagentView, mainSessionId]);

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
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      historyPageRequest.current?.abort();
      olderPageRequest.current?.abort();
      transcriptStore.flush();
      abortControllerRef.current?.abort();
      childAbortControllerRef.current?.abort();
    };
  }, []);

  const loadUserLlmConfig = async () => {
    const result = await getUserLlmConfig();
    const data = (result.data ?? {}) as {
      config?: {
        enabled: boolean;
        base_url: string;
        model: string;
        api_format?: LlmDialogState["api_format"];
        has_api_key: boolean;
        extra_headers?: Record<string, string>;
        extra_body?: Record<string, unknown>;
        sampling?: {
          context_window?: number | null;
          max_output_tokens?: number | null;
          model_defaults?: string[];
          native_compaction?: boolean;
          model_profile?: LlmDialogState["model_profile"];
          deployment_revision?: string;
          stream_usage?: boolean;
          reasoning_replay?: LlmDialogState["reasoning_replay"];
          reasoning_field?: LlmDialogState["reasoning_field"];
          temperature?: number | null;
          frequency_penalty?: number | null;
          presence_penalty?: number | null;
          top_p?: number | null;
          repetition_penalty?: number | null;
          reasoning_effort?: string | null;
        } | null;
      } | null;
      resolved?: {
        scope?: "user" | "platform" | "global";
        sampling_sources?: Record<string, string>;
        reasoning_effort_options?: string[];
        sampling?: { reasoning_effort?: string | null } | null;
      } | null;
    };
    const config = data.config;
    const s = config?.sampling;
    if (data.resolved?.reasoning_effort_options) {
      setReasoningEffortOptions(data.resolved.reasoning_effort_options);
    }
    const resolvedEffort = data.resolved?.sampling?.reasoning_effort;
    setReasoningEffort(resolvedEffort != null ? String(resolvedEffort) : "");
    setLlmState({
      api_format: config?.api_format,
      sampling_context_window: s?.context_window != null ? String(s.context_window) : "",
      sampling_max_output_tokens: s?.max_output_tokens != null ? String(s.max_output_tokens) : "",
      sampling_reasoning_effort: s?.reasoning_effort != null ? String(s.reasoning_effort) : "",
      native_compaction: s?.native_compaction,
      ...loadAdvancedSampling(s as Record<string, unknown> | undefined),
      model_profile: s?.model_profile,
      deployment_revision: s?.deployment_revision,
      stream_usage: s?.stream_usage,
      reasoning_replay: s?.reasoning_replay,
      reasoning_field: s?.reasoning_field,
      sampling_sources: data.resolved?.sampling_sources,
      model_defaults: s?.model_defaults ?? [],
      enabled: config?.enabled ?? true,
      base_url: config?.base_url ?? "",
      model: config?.model ?? "",
      api_key: "",
      extra_headers_text: config?.extra_headers && Object.keys(config.extra_headers).length > 0 ? JSON.stringify(config.extra_headers, null, 2) : "",
      extra_body_text: config?.extra_body && Object.keys(config.extra_body).length > 0 ? JSON.stringify(config.extra_body, null, 2) : "",
      has_api_key: Boolean(config?.has_api_key),
      resolved_scope: data.resolved?.scope ?? "global",
      sampling_temperature: s?.temperature != null ? String(s.temperature) : "",
      sampling_frequency_penalty: s?.frequency_penalty != null ? String(s.frequency_penalty) : "",
      sampling_presence_penalty: s?.presence_penalty != null ? String(s.presence_penalty) : "",
      sampling_top_p: s?.top_p != null ? String(s.top_p) : "",
      sampling_repetition_penalty: s?.repetition_penalty != null ? String(s.repetition_penalty) : "",
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
        api_format: llmState.api_format,
        base_url: llmState.base_url.trim(),
        model: llmState.model.trim(),
        api_key: llmState.api_key.trim() || undefined,
        extra_headers: parseJsonObject(llmState.extra_headers_text, "扩展请求头") as Record<string, string>,
        extra_body: parseJsonObject(llmState.extra_body_text, "扩展请求体"),
        sampling: samplingFromForm(llmState as unknown as Record<string, unknown>),
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

  // 任务完成系统通知: 仅在用户不在场(tab 后台或嵌入式抽屉关闭)且已授权时弹出。
  // 标题用宿主平台名(嵌入式)或 AetherCore,正文为 agent 最后回复(由系统自行截断)。
  // 注意: 必须在 completed 事件到达的同一宏任务内同步发出,
  // 不可用 setTimeout 延迟——后台 tab 的定时器会被浏览器节流,通知会推迟到切回前台才弹。
  const maybeNotifyCompletion = (failureSummary: string | null, completionBody: string) => {
    if (!notifyOnComplete) return;
    if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
    const userPresent = tabVisibleRef.current && (!isEmbedMode || embedDrawerOpenRef.current);
    if (userPresent) return;
    const body = (failureSummary || completionBody).trim();
    if (!body) return;
    const title = hostNameRef.current || "AetherCore";
    try {
      const notification = new Notification(title, { body, tag: `aethercore-${sessionId || ""}` });
      notification.onclick = () => {
        window.focus();
        if (isEmbedMode) {
          window.parent.postMessage({ source: "aethercore-workbench", type: "aethercore:open-drawer" }, "*");
        }
        notification.close();
      };
    } catch {
      // 个别环境构造 Notification 可能抛错(如扩展上下文),静默降级
    }
  };

  const loadSession = async (
    nextSessionId: string,
    signal: AbortSignal,
    options?: { preserveSubagents?: boolean; liveRunTarget?: LiveRunRef },
  ) => {
    const summaryResult = await getSessionSummary(nextSessionId, signal);
    if (signal.aborted || activeViewSessionIdRef.current !== nextSessionId) return null;
    const summary = (summaryResult.data ?? {}) as {
      title?: string;
      transcript?: TranscriptChatMessage[];
      transcript_page?: WorkbenchTranscriptPage["transcript_page"];
      allow_network?: boolean;
      host_name?: string | null;
      skills?: SkillItem[];
      files?: FileItem[];
      workboard?: WorkboardState;
      elicitation?: ElicitationState;
      context_state?: {
        model_id?: string;
        last_known_token_estimate?: number;
        budget_source?: string;
        effective_window?: number;
        context_window?: number;
        target_input_tokens?: number;
        warning_threshold?: number;
        blocking_limit?: number;
        percent_used?: number;
        last_api_usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
      };
      active_run?: ActiveRunSummary | null;
      cross_chat_inbox_cursor?: string | null;
      subagents?: SubagentRunSummary[];
    };
    if (activeSessionIdRef.current === nextSessionId) {
      crossChatInboxRef.current = { sessionId: nextSessionId, cursor: summary.cross_chat_inbox_cursor ?? null };
    }
    const activeRun = summary.active_run?.status === "running" ? summary.active_run : null;
    const page = summary.transcript_page;
    const previousPage = transcriptPageRef.current;
    const incoming = summary.transcript ?? [];
    const rows = fromTranscriptMessages(incoming.filter((row) =>
      row.role !== "assistant" || !activeRun || row.runId !== activeRun.run_id));
    const anchor = incoming[0]?.id;
    const current = transcriptStore.read();
    const overlap = anchor ? current.findIndex((row) => row.id === anchor) : -1;
    if (page && previousPage?.revision === page.revision && previousPage.start < page.start && overlap >= 0) {
      setTranscriptMessages([...current.slice(0, overlap), ...rows]);
      transcriptPageRef.current = { ...page, start: previousPage.start, next_cursor: previousPage.next_cursor };
    } else {
      setTranscriptMessages(rows);
      transcriptPageRef.current = page ?? null;
    }
    setOlderCursor(transcriptPageRef.current?.next_cursor ?? null);
    setCompactionProgress(activeRun?.compaction_stage === "pruning" ? "正在裁剪历史上下文"
      : activeRun?.compaction_stage === "summarizing" ? "正在生成任务摘要" : "");
    setPendingUserEcho(null);
    setAllowNetwork(summary.allow_network ?? true);
    hostNameRef.current = summary.host_name || "";
    setSkills(summary.skills ?? []);
    setFiles(summary.files ?? []);
    setWorkboard(summary.workboard ?? null);
    setElicitation(summary.elicitation ?? null);
    if (!options?.preserveSubagents) {
      setSubagents(summary.subagents ?? []);
    }
    if (summary.context_state && summary.context_state.model_id) {
      const ctx = summary.context_state;
      const estimatedTokens = ctx.last_known_token_estimate || ctx.last_api_usage?.prompt_tokens || 0;
      const effectiveWindow = ctx.effective_window ?? 0;
      const percentUsed = ctx.percent_used || (estimatedTokens && effectiveWindow ? (estimatedTokens / effectiveWindow) * 100 : 0);
      setContextStatus({
        model: ctx.model_id ?? "",
        estimatedTokens,
        effectiveWindow,
        contextWindow: ctx.context_window ?? 0,
        targetInputTokens: ctx.target_input_tokens ?? 0,
        warningThreshold: ctx.warning_threshold ?? 0,
        blockingLimit: ctx.blocking_limit ?? 0,
        percentUsed,
        state: "idle",
        detail: ctx.budget_source === "unverified_fallback" ? "窗口为未验证的保守估算，请设置实际部署上限" : ctx.budget_source === "catalog_unverified" ? "窗口来自模型目录，请核对实际部署上限" : "已恢复上下文状态",
      });
    }
    const targetLiveRunRef = options?.liveRunTarget ?? liveRunRef;
    if (targetLiveRunRef === liveRunRef) {
      liveRunRef.current = null;
    }
    if (options?.preserveSubagents && summary.title) {
      setActiveSubagentName(summary.title.replace(/^Subagent:\s*/, ""));
    }
    if (activeRun) {
      restoreActiveRunAssistant(activeRun, targetLiveRunRef);
      return activeRun;
    }
    return null;
  };

  const loadOlderTranscript = async () => {
    if (!olderCursor || !activeSessionId || olderPageRequest.current || loading) return;
    if (olderError === "聊天历史已变化，请重新加载会话") {
      if (!displayedBusy) requestSessionReload(activeSessionId);
      return;
    }
    const request = new AbortController();
    olderPageRequest.current = request;
    setLoadingOlder(true);
    setOlderError("");
    try {
      const page = await getOlderTranscript(activeSessionId, olderCursor, request.signal);
      if (request.signal.aborted || activeViewSessionIdRef.current !== activeSessionId) return;
      setTranscriptMessages((current) => {
        const ids = new Set(current.map((item) => item.id));
        return [...fromTranscriptMessages(page.transcript).filter((item) => !ids.has(item.id)), ...current];
      });
      setOlderCursor(page.transcript_page.next_cursor);
      if (transcriptPageRef.current) {
        transcriptPageRef.current = { ...transcriptPageRef.current, start: page.transcript_page.start,
          next_cursor: page.transcript_page.next_cursor };
      }
    } catch (error) {
      if (request.signal.aborted) return;
      setOlderError(error instanceof TranscriptChangedError ? "聊天历史已变化，请重新加载会话"
        : error instanceof Error ? error.message : "读取更早消息失败");
    } finally {
      if (olderPageRequest.current === request) {
        olderPageRequest.current = null;
        setLoadingOlder(false);
      }
    }
  };

  const refreshSubagents = async (targetSessionId = mainSessionId) => {
    if (!targetSessionId) return;
    const result = await listSubagents(targetSessionId);
    setSubagents(((result.data ?? []) as SubagentRunSummary[]));
  };

  const updateSubagentUrl = (childSessionId: string | null) => {
    const params = new URLSearchParams(window.location.search);
    if (childSessionId) {
      params.set("subagent_session_id", childSessionId);
    } else {
      params.delete("subagent_session_id");
    }
    const query = params.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
  };

  const openSubagentSession = (childSessionId: string, name?: string) => {
    if (!childSessionId) return;
    if (activeSubagentSessionId && activeSubagentSessionId !== childSessionId) {
      childAbortControllerRef.current?.abort();
    }
    activeViewSessionIdRef.current = childSessionId;
    setActiveSubagentSessionId(childSessionId);
    setActiveSubagentName(name || "子代理");
    setSubagentPopoverOpen(false);
    updateSubagentUrl(childSessionId);
  };

  const exitSubagentSession = () => {
    childAbortControllerRef.current?.abort();
    returningFromSubagentRef.current = true;
    activeViewSessionIdRef.current = mainSessionId;
    setActiveSubagentSessionId(null);
    setActiveSubagentName("");
    setSubagentPopoverOpen(false);
    updateSubagentUrl(null);
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
      setOlderCursor(null);
      transcriptPageRef.current = null;
      setCompactionProgress("");
      transcriptOwnerRef.current = "";
      setPendingUserEcho(null);
      setSkills([]);
      setFiles([]);
      setWorkboard(null);
      setElicitation(null);
      setSubagents([]);
      setLoading(false);
      return;
    }

    if (isStreamingRef.current && !activeSubagentSessionId && !returningFromSubagentRef.current) {
      return;
    }

    const targetSessionId = activeSubagentSessionId || sessionId || localSessionId;
    if (!targetSessionId) {
      setLoading(false);
      return;
    }
    const recovering = reloadTargetRef.current === targetSessionId;
    reloadTargetRef.current = null;

    if (newlyCreatedSessionRef.current === targetSessionId) {
      newlyCreatedSessionRef.current = null;
      return;
    }

    olderPageRequest.current?.abort();
    olderPageRequest.current = null;
    setLoadingOlder(false);
    setOlderError("");
    if (transcriptOwnerRef.current !== targetSessionId) {
      transcriptOwnerRef.current = targetSessionId;
      setTranscriptMessages([]);
      setOlderCursor(null);
      transcriptPageRef.current = null;
      setCompactionProgress("");
    }
    const historyRequest = new AbortController();
    historyPageRequest.current = historyRequest;
    setLoading(true);
    setError("");
    setConnectionNotice("");
    setReconnectAvailable(false);
    const isChildView = Boolean(activeSubagentSessionId);
    if (!isChildView && isStreamingRef.current && returningFromSubagentRef.current) {
      returningFromSubagentRef.current = false;
      void loadSession(targetSessionId, historyRequest.signal)
        .catch((loadError) => {
          if (!historyRequest.signal.aborted) setError(loadError instanceof Error ? loadError.message : "初始化失败");
        })
        .finally(() => { if (!historyRequest.signal.aborted) setLoading(false); });
      return () => historyRequest.abort();
    }
    if (!isChildView) {
      returningFromSubagentRef.current = false;
    }

    void loadSession(targetSessionId, historyRequest.signal, {
      preserveSubagents: isChildView,
      liveRunTarget: isChildView ? childLiveRunRef : liveRunRef,
    })
      .then((activeRun) => {
        if (!activeRun || !mountedRef.current || historyRequest.signal.aborted) return;
        const reconnectAssistantId = activeRun.assistant.id || `live-${activeRun.run_id}`;
        const reconnectAbortController = new AbortController();
        if (isChildView) {
          childAbortControllerRef.current = reconnectAbortController;
          setChildBusy(true);
        } else {
          abortControllerRef.current = reconnectAbortController;
          isStreamingRef.current = true;
          setBusy(true);
        }
        const snapshotBlocks = [...(activeRun.assistant.blocks ?? [])] as AssistantBlock[];
        const currentContent = [...snapshotBlocks].reverse().find((block) => block.kind === "content");
        const currentReasoning = [...snapshotBlocks].reverse().find((block) => block.kind === "reasoning");
        const reconnectRefs = {
          activeReasoningId: { value: currentReasoning?.id ?? "" },
          activeContentId: { value: currentContent?.id ?? "" },
          activeContentText: { value: currentContent?.kind === "content" ? currentContent.content : "" },
        };
        const handleEvent = buildEventProcessor(
          reconnectAssistantId,
          targetSessionId,
          reconnectRefs,
          isChildView ? childLiveRunRef : liveRunRef,
        );
        void streamRunEvents(targetSessionId, activeRun.run_id, handleEvent, reconnectAbortController.signal, activeRun.last_seq ?? 0)
          .catch((streamError) => {
            if (!(streamError instanceof Error && streamError.name === "AbortError")) {
              if (streamError instanceof RunStreamInterruptedError) {
                setConnectionNotice("连接仍未恢复，任务进度已保留。");
                setReconnectAvailable(true);
              } else {
                setError(streamError instanceof Error ? streamError.message : "恢复运行订阅失败");
              }
            }
          })
          .finally(() => {
            if (isChildView) {
              if (childAbortControllerRef.current === reconnectAbortController) {
                childAbortControllerRef.current = null;
              }
              if (activeViewSessionIdRef.current === targetSessionId) {
                setChildBusy(false);
              }
              void refreshSubagents(sessionId || localSessionId || undefined).catch(() => undefined);
            } else {
              isStreamingRef.current = false;
              abortControllerRef.current = null;
              setBusy(false);
              void onSessionRefresh?.();
            }
          });
      })
      .catch((loadError) => {
        if (historyRequest.signal.aborted) return;
        if (recovering) {
          setConnectionNotice("暂时无法读取最新进度，当前显示的内容已保留。");
          setReconnectAvailable(true);
          return;
        }
        setError(loadError instanceof Error ? loadError.message : "初始化失败");
        setTranscriptMessages([]);
        setPendingUserEcho(null);
        setSkills([]);
        setFiles([]);
        setWorkboard(null);
        setElicitation(null);
        if (!isChildView) setSubagents([]);
      })
      .finally(() => { if (!historyRequest.signal.aborted) setLoading(false); });
    return () => historyRequest.abort();
  }, [activeSubagentSessionId, sessionId, isNewSession, localSessionId, sessionReload]);

  const pendingQuestionId = pendingElicitationRequest?.id;
  useEffect(() => {
    if (!mainSessionId || !pendingQuestionId || displayedBusy || elicitationBusy) return;
    const controller = new AbortController();
    let inFlight = false;
    const poll = async () => {
      if (document.hidden || controller.signal.aborted || inFlight) return;
      inFlight = true;
      try {
        const snapshot = await getSessionElicitation(mainSessionId, controller.signal);
        if (controller.signal.aborted || submittingElicitationRef.current || isStreamingRef.current) return;
        const previous = snapshot.history.find((item) => item.id === pendingQuestionId);
        setElicitation((current) => mergeElicitationSnapshot(current, snapshot));
        if (previous?.status === "resolved") requestSessionReload(mainSessionId);
        if (previous?.status === "cancelled") {
          setConnectionNotice("问题已停止或超时，可以发送新消息继续。");
        }
      } catch {
        // 没有确认新状态时继续保留面板与草稿，等待下一次查询。
      } finally {
        inFlight = false;
      }
    };
    const timer = window.setInterval(() => void poll(), 5000);
    window.addEventListener("focus", poll);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      window.removeEventListener("focus", poll);
    };
  }, [mainSessionId, pendingQuestionId, displayedBusy, elicitationBusy]);

  useEffect(() => {
    if (!mainSessionId || isSubagentView || displayedBusy || loading) return;
    const controller = new AbortController();
    let checking = false;
    const checkInbox = async () => {
      const current = crossChatInboxRef.current;
      if (document.hidden || checking || isStreamingRef.current || current?.sessionId !== mainSessionId) return;
      checking = true;
      try {
        const cursor = await getCrossChatInbox(mainSessionId, controller.signal);
        if (!controller.signal.aborted && !isStreamingRef.current && crossChatInboxRef.current === current
            && cursor && cursor !== current.cursor) requestSessionReload(mainSessionId);
      } catch {
        // 轻量收件轮询失败时保留当前展示，下一次检查继续。
      } finally {
        checking = false;
      }
    };
    const timer = window.setInterval(() => void checkInbox(), 7000);
    window.addEventListener("focus", checkInbox);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      window.removeEventListener("focus", checkInbox);
    };
  }, [mainSessionId, isSubagentView, displayedBusy, loading]);

  useEffect(() => {
    if (!mainSessionId || !subagents.some((item) => item.status === "running")) return;
    const timer = window.setInterval(() => {
      void refreshSubagents(mainSessionId).catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [mainSessionId, subagents]);

const composerDisabled = loading || !(sessionId || localSessionId || isNewSession) || Boolean(pendingElicitationRequest?.blocking);

  const appendAssistantBlock = (messageId: string, block: AssistantBlock) => {
    transcriptStore.patch(messageId, (item) => item.role === "assistant" ? { ...item, blocks: [...item.blocks, block] } : item);
  };

  const appendSystemEvent = (eventType: Extract<TranscriptMessage, { role: "system_event" }>["eventType"],
    payload: Record<string, unknown>, assistantId: string) => {
    const row = buildSystemEventMessage(eventType, payload);
    setTranscriptMessages((current) => eventType === "context_compacted" || eventType === "context_recovered"
      ? insertTranscriptMessageBeforeAssistant(current, row, assistantId) : appendTranscriptMessage(current, row));
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

  const updateAssistantBlock = (messageId: string, blockId: string, updater: (block: AssistantBlock) => AssistantBlock, defer = false) => {
    transcriptStore.patch(messageId, (item) => item.role === "assistant"
      ? { ...item, blocks: item.blocks.map((block) => block.id === blockId ? updater(block) : block) } : item, defer);
  };

  const upsertAssistantBlock = (messageId: string, blockId: string, createBlock: () => AssistantBlock,
    updater: (block: AssistantBlock) => AssistantBlock, defer = false) => {
    transcriptStore.patch(messageId, (item) => {
        if (item.role !== "assistant") return item;
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
      }, defer);
  };

  const upsertAssistantMessage = (
    messageId: string,
    updater: (message: Extract<TranscriptMessage, { role: "assistant" }> | null) => Extract<TranscriptMessage, { role: "assistant" }>,
  ) => {
    if (!transcriptStore.patch(messageId, (item) => item.role === "assistant" ? updater(item) : item)) {
      setTranscriptMessages((current) => [...current, updater(null)]);
    }
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

  const restoreActiveRunAssistant = (activeRun: ActiveRunSummary, targetLiveRunRef: LiveRunRef = liveRunRef) => {
    const assistantId = activeRun.assistant.id || `live-${activeRun.run_id}`;
    if (activeRun.last_seq != null) eventCursorsRef.current.set(`${activeRun.session_id}:${activeRun.run_id}`, activeRun.last_seq);
    targetLiveRunRef.current = { runId: activeRun.run_id, assistantId };
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

  const eventCursorsRef = useRef(new Map<string, number>());

  const buildEventProcessor = (
    assistantId: string,
    effectiveSessionId: string,
    refs: {
      activeReasoningId: { value: string | null };
      activeContentId: { value: string | null };
      activeContentText: { value: string };
      wasAborted?: { value: boolean };
      partialContent?: { value: string };
    },
    activeLiveRunRef: LiveRunRef = liveRunRef,
  ) => {
    return (event: Record<string, unknown>) => {
      if (activeViewSessionIdRef.current !== effectiveSessionId) return;
      if (event.session_id && event.session_id !== effectiveSessionId) return;
      if (event.run_id && typeof event.seq === "number") {
        const key = `${effectiveSessionId}:${event.run_id}`;
        const previous = eventCursorsRef.current.get(key) ?? 0;
        if (event.seq <= previous) return;
        eventCursorsRef.current.set(key, event.seq);
      }
      const eventType = String(event.type ?? "");
      if (eventType !== "content_delta" && eventType !== "reasoning_delta" && eventType !== "tool_output_delta") {
        transcriptStore.flush();
      }
      const payload = (event.payload ?? {}) as Record<string, unknown>;
      if (eventType === "transport_status") {
        setConnectionNotice(payload.state === "reconnecting" ? "连接暂时中断，正在重新连接并恢复进度…" : "");
        return;
      }
      const previewMetadata = {
        runId: typeof event.run_id === "string" ? event.run_id : undefined,
        attemptId: typeof event.attempt_id === "string" ? event.attempt_id : undefined,
        seq: typeof event.seq === "number" ? event.seq : undefined,
        contextEpoch: typeof event.context_epoch_id === "number" ? event.context_epoch_id : undefined,
      };

      if (eventType === "run_started" || (eventType === "run_status_changed" && payload.reason === "elicitation_resumed")) {
        const runId = String(event.run_id ?? payload.run_id ?? "");
        if (runId) {
          activeLiveRunRef.current = { runId, assistantId };
          lastRunIdRef.current = runId;
        }
        notifyHost("aethercore:run-status", {
          session_id: effectiveSessionId,
          run_id: String(payload.run_id ?? activeLiveRunRef.current?.runId ?? ""),
          assistant_id: assistantId,
          active: true,
        });
        return;
      }

      if (eventType.startsWith("subagent_")) {
        const subagentId = String(payload.subagent_id ?? "");
        if (!subagentId) return;
        setSubagents((current) => {
          const existing = current.find((item) => item.subagent_id === subagentId);
          const next: SubagentRunSummary = {
            subagent_id: subagentId,
            latest_run_id: String(payload.latest_run_id ?? existing?.latest_run_id ?? ""),
            child_session_id: String(payload.child_session_id ?? existing?.child_session_id ?? ""),
            name: String(payload.name ?? existing?.name ?? "Subagent"),
            task: String(payload.task ?? existing?.task ?? ""),
            status: String(payload.status ?? (eventType === "subagent_created" ? "running" : existing?.status ?? "running")),
            current_action: existing?.current_action ?? null,
            result: typeof payload.result === "string" ? payload.result : existing?.result ?? null,
            error: typeof payload.error === "string" ? payload.error : existing?.error ?? null,
            cancel_requested_at: existing?.cancel_requested_at ?? null,
            created_at: existing?.created_at ?? null,
            finished_at: existing?.finished_at ?? null,
            destroyed_at: existing?.destroyed_at ?? null,
            user_can_message: false,
          };
          const others = current.filter((item) => item.subagent_id !== subagentId);
          return [...others, next].sort((left, right) => left.name.localeCompare(right.name));
        });
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
        if (snapshot) setElicitation((current) => mergeElicitationSnapshot(current, snapshot));
        return;
      }

      if (eventType === "ask_requested") {
        const snapshot = payload.snapshot as ElicitationState | undefined;
        if (snapshot) {
          setElicitation((current) => mergeElicitationSnapshot(current, snapshot));
        } else if (payload.request) {
          setElicitation((current) => applyLegacyQuestion(current, effectiveSessionId, payload.request as ElicitationRequest));
        }
        return;
      }

      if (eventType === "ask_resolved" || eventType === "ask_cancelled") {
        const snapshot = payload.snapshot as ElicitationState | undefined;
        if (snapshot) setElicitation((current) => mergeElicitationSnapshot(current, snapshot));
        return;
      }

      if (eventType === "aborted") {
        setCompactionProgress("");
        setContextStatus((current) => current?.state === "compacting"
          ? { ...current, state: "idle", detail: "上下文压缩已取消" } : current);
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
          detail: payload.budget_source === "unverified_fallback" ? "窗口为未验证的保守估算，请设置实际部署上限" : payload.budget_source === "catalog_unverified" ? "窗口来自模型目录，请核对实际部署上限" : "上下文稳定",
        });
        return;
      }

      if (eventType === "context_compaction_progress") {
        const stage = String(payload.stage);
        setCompactionProgress(stage === "pruning" ? "正在裁剪历史上下文"
          : stage === "summarizing" ? "正在生成任务摘要" : "");
        setContextStatus((current) => current && ({
          ...current,
          state: stage === "finished" ? (current.state === "compacting" ? "idle" : current.state) : "compacting",
          detail: stage === "pruning" ? "正在裁剪历史上下文"
            : stage === "summarizing" ? "正在生成任务摘要"
              : current.state === "compacted" ? current.detail : "本次压缩结束",
        }));
        return;
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
        const estimated = Number(payload.estimated_tokens ?? 0);
        const budget = Number(payload.effective_window ?? 0);
        setContextStatus((current) => ({
          model: current?.model ?? "",
          estimatedTokens: Number(payload.estimated_tokens ?? current?.estimatedTokens ?? 0),
          effectiveWindow: Number(payload.effective_window ?? current?.effectiveWindow ?? 0),
          contextWindow: Number(payload.context_window ?? current?.contextWindow ?? 0),
          targetInputTokens: current?.targetInputTokens ?? 0,
          warningThreshold: current?.warningThreshold ?? 0,
          blockingLimit: Number(payload.blocking_limit ?? current?.blockingLimit ?? 0),
          percentUsed: budget > 0 ? Math.round(estimated / budget * 100) : 100,
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
        eventType === "context_blocked"
      ) {
        appendSystemEvent(eventType as Extract<ChatMessage, { role: "system_event" }>["eventType"], payload, assistantId);
      }

      if (eventType === "reasoning_delta") {
        if (typeof payload.part_id === "string" && refs.activeReasoningId.value !== payload.part_id) refs.activeReasoningId.value = "";
        if (!refs.activeReasoningId.value) {
          refs.activeReasoningId.value = String(payload.part_id ?? `reasoning-${Date.now()}`);
          appendAssistantBlock(assistantId, {
            id: refs.activeReasoningId.value,
            kind: "reasoning",
            content: String(payload.delta ?? ""),
            started_at: new Date().toISOString(),
          });
        } else {
          updateAssistantBlock(assistantId, refs.activeReasoningId.value, (block) =>
            block.kind === "reasoning" ? { ...block, content: `${block.content}${String(payload.delta ?? "")}` } : block,
            true,
          );
        }
        return;
      }

      if (eventType === "content_delta") {
        if (typeof payload.part_id === "string" && refs.activeContentId.value !== payload.part_id) {
          refs.activeContentId.value = "";
          refs.activeContentText.value = "";
        }
        refs.activeContentText.value += String(payload.delta ?? "");
        if (!refs.activeContentId.value) {
          if (refs.activeReasoningId.value) {
            const endedAt = new Date().toISOString();
            updateAssistantBlock(assistantId, refs.activeReasoningId.value, (block) =>
              block.kind === "reasoning" ? { ...block, ended_at: endedAt, status: "completed" } : block,
            );
            refs.activeReasoningId.value = null;
          }
          refs.activeContentId.value = String(payload.part_id ?? `content-${Date.now()}`);
          appendAssistantBlock(assistantId, {
            id: refs.activeContentId.value,
            kind: "content",
            content: refs.activeContentText.value,
            status: "streaming",
          });
        } else {
          updateAssistantBlock(assistantId, refs.activeContentId.value, (block) =>
            block.kind === "content" ? { ...block, content: refs.activeContentText.value, status: "streaming" } : block,
            true,
          );
        }
        onAssistantPreview?.({
          sessionId: effectiveSessionId,
          messageId: assistantId,
          contentId: refs.activeContentId.value,
          content: refs.activeContentText.value,
          status: "streaming",
          ...previewMetadata,
        });
        return;
      }

      if (eventType === "content_completed") {
        if (refs.activeContentId.value && refs.activeContentText.value) {
          updateAssistantBlock(assistantId, refs.activeContentId.value, (block) =>
            block.kind === "content" ? { ...block, content: refs.activeContentText.value, status: payload.status === "incomplete" ? "incomplete" : "done" } : block,
          );
          onAssistantPreview?.({
            sessionId: effectiveSessionId,
            messageId: assistantId,
            contentId: refs.activeContentId.value,
            content: refs.activeContentText.value,
            status: payload.status === "incomplete" ? "incomplete" : "completed",
            ...previewMetadata,
          });
        }
        if (refs.activeReasoningId.value) {
          const endedAt = new Date().toISOString();
          updateAssistantBlock(assistantId, refs.activeReasoningId.value, (block) =>
            block.kind === "reasoning" ? { ...block, ended_at: endedAt, status: "completed" } : block,
          );
        }
        refs.activeReasoningId.value = "";
        return;
      }

      if (eventType === "tool_started") {
        notifyHost("aethercore:tool-status", {
          session_id: effectiveSessionId,
          run_id: activeLiveRunRef.current?.runId ?? "",
          tool_call_id: String(payload.id ?? ""),
          tool_name: String(payload.tool_name ?? ""),
          status: "started",
        });
        if (refs.activeReasoningId.value) {
          const endedAt = new Date().toISOString();
          updateAssistantBlock(assistantId, refs.activeReasoningId.value, (block) =>
            block.kind === "reasoning" ? { ...block, ended_at: endedAt, status: "completed" } : block,
          );
        }
        refs.activeReasoningId.value = "";
        refs.activeContentId.value = "";
        refs.activeContentText.value = "";
        const displayPayload = (payload.tool_display ?? {}) as Record<string, unknown>;
        const toolName = String(payload.tool_name ?? "tool");
        appendAssistantBlock(assistantId, {
          id: String(payload.id ?? `tool-${Date.now()}`),
          kind: "tool",
          title: typeof displayPayload.title === "string" && displayPayload.title.trim() ? displayPayload.title : toolName,
          toolName,
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
                  liveOutputText: `${block.liveOutputText ?? ""}${String(payload.text ?? "")}`.slice(-65_536),
                  status: "running",
                }
              : block,
          true,
        );
        return;
      }

      if (eventType === "tool_finished") {
        notifyHost("aethercore:tool-status", {
          session_id: effectiveSessionId,
          run_id: activeLiveRunRef.current?.runId ?? "",
          tool_call_id: String(payload.id ?? ""),
          tool_name: String(payload.tool_name ?? ""),
          status: "finished",
        });
        const output = typeof payload.output === "string" ? payload.output : "";
        const structuredResult = payload.result as { status?: string } | undefined;
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
            outputText: output,
            liveOutputText: undefined,
            status: structuredResult?.status === "aborted" ? "aborted" : "done",
          }),
          (block) =>
            block.kind === "tool"
              ? {
                  ...block,
                  outputText: output,
                  liveOutputText: undefined,
                  status: structuredResult?.status === "aborted" ? "aborted" : "done",
                }
              : block,
        );
        return;
      }

      if (eventType === "artifact_created") return;

      if (eventType === "stream_retry") {
        const attempt = Number(payload.attempt ?? 0);
        const delay = Number(payload.delay ?? 0);
        const retryReason = String(payload.reason ?? "transport");
        refs.activeContentText.value = "";
        refs.activeContentId.value = null;
        refs.activeReasoningId.value = null;
        const retryLabel = retryReason === "upstream_status" ? "模型服务暂时不可用" : "网络中断";
        setConnectionNotice(`${retryLabel}，第 ${attempt} 次重连中（${delay}秒）…`);
        return;
      }

      if (eventType === "result") {
        const subtype = String(payload.subtype ?? "");
        const stopReason = String(payload.stop_reason ?? "");
        if (subtype && subtype !== "success") {
          setError(subtype === "error_context_overflow" && typeof payload.message === "string"
            ? payload.message
            : RESULT_MESSAGES[subtype] ?? "执行失败");
        } else if (stopReason === "stream_interrupted") {
          setError("回复因网络中断被截断");
        }
        return;
      }

      if (eventType === "completed") {
        if (payload.historical_pause) return;
        setCompactionProgress("");
        setConnectionNotice(payload.subtype === "timeout" ? "提问等待超时，可以发送新消息继续。" : "");
        const elapsedMs = Number(payload.elapsed_ms ?? 0);
        const completionSubtype = String(payload.subtype ?? "");
        const now = new Date().toISOString();
        setTranscriptMessages((current) =>
          current.map((item) =>
            item.id === assistantId && item.role === "assistant"
              ? {
                  ...item,
                  elapsedMs: elapsedMs > 0 ? elapsedMs : item.elapsedMs,
                  streaming: false,
                  responseStartedAt: item.responseStartedAt,
                  blocks: item.blocks?.map((block) =>
                    block.kind === "reasoning" && !block.ended_at
                      ? { ...block, ended_at: now, status: "completed" }
                      : block
                  ),
                }
              : item,
          ),
        );
        activeLiveRunRef.current = null;
        notifyHost("aethercore:run-status", {
          session_id: effectiveSessionId,
          run_id: String(payload.run_id ?? ""),
          assistant_id: assistantId,
          active: false,
          status: String(payload.subtype ?? "completed"),
        });
        // 正文从流式累计 ref 同步读取(React functional updater 非同步执行,不可依赖其副作用)
        if (completionSubtype !== "awaiting_user_input") {
          maybeNotifyCompletion(
            completionSubtype && completionSubtype !== "success"
              ? (RESULT_MESSAGES[completionSubtype] ?? "任务已中断") : null,
            refs.activeContentText.value,
          );
        }
        return;
      }

      if (eventType === "error") {
        setCompactionProgress("");
        setConnectionNotice("");
        const message = MODEL_FAILURE_MESSAGES[String(payload.error_code ?? "")]
          ?? "当前服务暂时无法完成请求，已保留已有进度，请稍后继续。";
        appendAssistantBlock(assistantId, {
          id: `tool-error-${Date.now()}`,
          kind: "tool",
          title: "执行错误",
          meta: "error",
          argumentsText: "",
          outputText: message,
          status: "done",
        });
        setError(message);
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
    if (toolRefreshRef.current && toolRefreshRef.current === mainSessionId) return false;
    
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
        if (!mountedRef.current) return;
        effectiveSessionId = String(created.data?.session_id ?? "");
        setLocalSessionId(effectiveSessionId);
        newlyCreatedSessionRef.current = effectiveSessionId;
      } catch (err) {
        setError(err instanceof Error ? err.message : "创建会话失败");
        return err instanceof ServiceRestartingError ? false : undefined;
      }
    }

    const abortController = new AbortController();
    abortControllerRef.current = abortController;
    pendingAssistantIdRef.current = assistantId;
    isStreamingRef.current = true;
    scrollToLatestRef.current?.();
    setBusy(true);
    setError("");
    setPendingUserEcho(
      userText.trim().toLowerCase() === "/compact" ? null : skipAddUserBubble && pendingUserBubble
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
    let streamEndedWithError = false;
    let streamDisconnected = false;
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
        reasoningEffort: reasoningEffort || null,
        disabledCapabilityIds: (() => { try { return JSON.parse(localStorage.getItem(localStorage.getItem("aethercore-capabilities:active") || "") || "[]"); } catch { return []; } })(),
      });
    } catch (chatError) {
      streamEndedWithError = true;
      if (chatError instanceof Error && chatError.name === "AbortError") {
        wasAborted = true;
      } else if (chatError instanceof RunStreamInterruptedError) {
        streamDisconnected = true;
        setConnectionNotice("连接仍未恢复，任务进度已保留。");
        setReconnectAvailable(true);
      } else {
        setError(chatError instanceof Error ? chatError.message : "对话执行失败");
        if (chatError instanceof ServiceRestartingError || chatError instanceof SessionRunConflictError) {
          setTranscriptMessages((current) => current.filter((item) => item.id !== assistantId));
          if (chatError instanceof SessionRunConflictError && effectiveSessionId) requestSessionReload(effectiveSessionId);
          if (skipAddUserBubble) {
            const unsent = { id: pendingUserBubble?.id ?? primaryClientMessageId, content: userText, queuedAt: Date.now() };
            queuedMessagesRef.current = [unsent, ...queuedMessagesRef.current];
            setQueuedMessages(queuedMessagesRef.current);
          }
          return false;
        }
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
      if (!streamDisconnected) {
        notifyHost("aethercore:run-status", {
          session_id: effectiveSessionId,
          run_id: lastRunIdRef.current,
          assistant_id: assistantId,
          active: false,
          status: wasAborted ? "aborted" : streamEndedWithError ? "error" : "completed",
        });
      }
      void onSessionRefresh?.(effectiveSessionId || undefined);
      
      const currentQueue = queuedMessagesRef.current;
      if (mountedRef.current && !streamEndedWithError && currentQueue.length > 0) {
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
    const previousTranscript = transcriptStore.read();

    setBusy(true);
    setError("");
    scrollToLatestRef.current?.();

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
          { replaceLastUserMessage: true, reasoningEffort: reasoningEffort || null, disabledCapabilityIds: (() => { try { return JSON.parse(localStorage.getItem(localStorage.getItem("aethercore-capabilities:active") || "") || "[]"); } catch { return []; } })() },
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
    const effectiveSessionId = isSubagentView && activeSessionId ? activeSessionId : sessionId || localSessionId;
    if (!effectiveSessionId || stoppingSessionId === effectiveSessionId) return;
    setStoppingSessionId(effectiveSessionId);
    try {
      const stopped = await abortSession(effectiveSessionId);
      if (activeSessionIdRef.current !== effectiveSessionId) return;
      if (!isSubagentView && stopped.workboard) setWorkboard(stopped.workboard);
      if (!isSubagentView && stopped.elicitation) setElicitation(stopped.elicitation);
      // 保持事件流，接收清单收尾和 completed；不把断开连接当成后台已停止。
    } catch (err) {
      if (activeSessionIdRef.current === effectiveSessionId) setError(err instanceof Error ? err.message : "停止失败，请重试");
    } finally {
      setStoppingSessionId((current) => current === effectiveSessionId ? null : current);
    }
  };

  const handleStopSubagent = async (subagent: SubagentRunSummary) => {
    if (!mainSessionId) return;
    try {
      await cancelSubagent(mainSessionId, subagent.subagent_id);
      await refreshSubagents();
    } catch (err) {
      setError(err instanceof Error ? err.message : "停止子代理失败");
    }
  };

  const handleDestroySubagent = async (subagent: SubagentRunSummary) => {
    if (!mainSessionId || !window.confirm(`确定销毁子代理「${subagent.name}」吗？历史记录会保留。`)) return;
    try {
      await destroySubagent(mainSessionId, subagent.subagent_id);
      await refreshSubagents(mainSessionId);
      if (activeSubagentSessionId === subagent.child_session_id) {
        exitSubagentSession();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "销毁子代理失败");
    }
  };

  const handleElicitationSubmit = async (responses: ElicitationResponseItem[]) => {
    const effectiveSessionId = sessionId || localSessionId;
    const pendingRequest = elicitation?.pending;
    if (!effectiveSessionId || !pendingRequest || submittingElicitationRef.current) return;
    if (isStreamingRef.current) {
      setConnectionNotice("当前任务还在执行，结束后即可提交已填写的答案。");
      return;
    }
    submittingElicitationRef.current = true;

    const assistantId = `assistant-ask-${Date.now()}`;
    const roundStartTime = Date.now();
    const abortController = new AbortController();
    abortControllerRef.current = abortController;
    pendingAssistantIdRef.current = assistantId;
    isStreamingRef.current = true;
    scrollToLatestRef.current?.();
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
    // 收到 ask_resolved 后再清除问题；维护期拒绝请求时保留用户填写的答案。

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
      if (err instanceof RunStreamInterruptedError) {
        setConnectionNotice("答案已提交，连接仍未恢复。");
        setReconnectAvailable(true);
      } else if (!(err instanceof Error && err.name === "AbortError")) {
        setConnectionNotice(err instanceof Error ? err.message : "暂时无法提交答案，请重试。");
        try {
          const snapshot = await getSessionElicitation(effectiveSessionId, abortController.signal);
          if (activeSessionIdRef.current === effectiveSessionId) {
            setElicitation((current) => mergeElicitationSnapshot(current, snapshot));
            if (snapshot.history.some((item) => item.id === pendingRequest.id && item.status === "resolved")) {
              requestSessionReload(effectiveSessionId);
            }
          }
        } catch {
          // 保留草稿，用户可以重新提交；后端按问题 ID 识别重复回答。
        }
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
      submittingElicitationRef.current = false;
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

  const currentToolRefresh = refreshingTools === mainSessionId;
  const busyCommandReason = displayedBusy || pendingElicitationRequest?.blocking
    ? "请等待当前任务结束"
    : undefined;
  const missingSessionReason = mainSessionId ? undefined : "请先开启会话";
  const newSessionReason = queuedMessages.length ? "请先发送或移除排队中的消息" : undefined;
  const commandAvailability: CommandAvailability = {
    new: onNewConversation ? newSessionReason : "当前工作台无法新建会话",
    stop: !displayedBusy && !pendingElicitationRequest?.blocking ? "当前没有运行中的任务" : undefined,
    context: missingSessionReason,
    tools: missingSessionReason,
    deliveries: missingSessionReason,
    compact: missingSessionReason ?? busyCommandReason ?? (currentToolRefresh ? "正在刷新工具目录" : undefined),
    refresh: !isEmbedMode || !embedHostOrigin || !embedSupportsToolRefresh
      ? "当前宿主未接入工具刷新"
      : missingSessionReason ?? busyCommandReason ?? (currentToolRefresh ? "正在刷新工具目录" : undefined),
  };

  const requestNewConversation = () => {
    if (newSessionReason) setError(newSessionReason);
    else onNewConversation?.();
  };

  const refreshHostTools = async () => {
    if (toolRefreshRef.current || isStreamingRef.current) return;
    const target = mainSessionId;
    toolRefreshRef.current = target;
    setRefreshingTools(target);
    const show = (text: string) => {
      if (activeSessionIdRef.current === target) setRefreshNotice({ sessionId: target, text });
    };
    show("正在向宿主刷新工具目录…");
    try {
      const previous = await inspectSession(target, "tools");
      if (activeSessionIdRef.current !== target) return;
      const result = await requestHostToolRefresh(embedHostOrigin, target, previous.revision);
      const confirmed = await inspectSession(target, "tools");
      if (confirmed.revision < result.revision) throw new Error("AetherCore 尚未确认该工具目录版本，请重试。");
      const changes = `新增 ${result.added} 项，移除 ${result.removed} 项，更新 ${result.updated} 项`;
      show(result.revision === previous.revision ? "工具目录已是最新。" : `工具目录已刷新（${changes}）。`);
    } catch (error) {
      show(error instanceof Error ? error.message : "刷新工具目录失败。");
    } finally {
      toolRefreshRef.current = null;
      setRefreshingTools(null);
    }
  };

  const runWorkbenchCommand = (command: WorkbenchCommand) => {
    if (commandAvailability[command]) return;
    switch (command) {
      case "new": requestNewConversation(); break;
      case "stop": void handleStop(); break;
      case "compact": if (!isStreamingRef.current) void handleSend("/compact"); break;
      case "context": setInspection({ sessionId: mainSessionId, resource: "context/status" }); break;
      case "tools": setInspection({ sessionId: mainSessionId, resource: "tools" }); break;
      case "deliveries": setDeliverySessionId(mainSessionId); break;
      case "refresh": void refreshHostTools(); break;
    }
  };

  return (
    <main className="app-layout">
      {deliverySessionId && deliverySessionId === mainSessionId && (
        <CrossChatDeliveriesDialog
          key={deliverySessionId}
          sessionId={deliverySessionId}
          onClose={() => setDeliverySessionId(null)}
          onOpenSession={!displayedBusy && !queuedMessages.length && onSessionSelect ? (id) => {
            setDeliverySessionId(null);
            onSessionSelect(id);
          } : undefined}
        />
      )}
      {inspection?.sessionId === mainSessionId && (
        <SessionInspectionDialog
          key={`${inspection.sessionId}:${inspection.resource}`}
          sessionId={inspection.sessionId}
          resource={inspection.resource}
          onClose={() => setInspection(null)}
          onManageTools={() => { setInspection(null); setSidebarView("skills"); setIsSidebarOpen(true); }}
        />
      )}
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
        onNewConversation={onNewConversation ? requestNewConversation : undefined}
        onSessionSelect={onSessionSelect}
        onRenameSession={onRenameSession}
        onDeleteSession={onDeleteSession}
        onUploadFile={handleUpload}
        onUploadSkill={handleUploadSkill}
        onRefreshCapabilities={() => void refreshResources(sessionId)}
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
        open={showPersonalSettingsDialog}
        onClose={() => setShowPersonalSettingsDialog(false)}
        onLogout={onLogout}
      />

      <section
        className={`main-content ${isDragging ? "is-drag-over" : ""}`}
        onDragEnter={(e) => {
          e.preventDefault();
          if (!isDragging) {
            const fileCount = Array.from(e.dataTransfer.items || [])
              .filter((item) => item.kind === "file").length;
            const count = Math.max(fileCount || e.dataTransfer.files?.length || 1, 1);
            setDraggedNames(new Array(count).fill(""));
            setIsDragging(true);
            setOverlayVisible(true);
            if (overlayExitTimer.current) { window.clearTimeout(overlayExitTimer.current); overlayExitTimer.current = null; }
          }
        }}
        onDragOver={(e) => { e.preventDefault(); }}
        onDragLeave={(e) => {
          e.preventDefault();
          if (e.currentTarget.contains(e.relatedTarget as Node)) return;
          setIsDragging(false);
          if (overlayExitTimer.current) window.clearTimeout(overlayExitTimer.current);
          overlayExitTimer.current = window.setTimeout(() => {
            setOverlayVisible(false);
            setDraggedNames([]);
          }, 220);
        }}
        onDrop={(e) => {
          e.preventDefault();
          setIsDragging(false);
          setDraggedNames([]);
          if (overlayExitTimer.current) { window.clearTimeout(overlayExitTimer.current); overlayExitTimer.current = null; }
          setOverlayVisible(false);
          if (e.dataTransfer.files?.length > 0) {
            Array.from(e.dataTransfer.files).forEach((f) => void handleUpload(f));
          }
        }}
      >
        {overlayVisible ? (
          <div className={`drag-over-overlay ${isDragging ? "" : "is-exiting"}`}>
            <div className="drag-over-card-stack">
              {draggedNames.length <= 1 ? (
                <div className="drag-file-card drag-file-card--single">
                  <div className="drag-file-card__icon"><Icons.File /></div>
                </div>
              ) : (
                <>
                  {draggedNames.slice(0, 5).map((name, i, arr) => {
                    const total = arr.length;
                    const mid = (total - 1) / 2;
                    const angle = (i - mid) * 7;
                    const offset = (i - mid) * 10;
                    return (
                      <div
                        key={i}
                        className="drag-file-card drag-file-card--fan"
                        style={{
                          transform: `translateX(${offset}px) rotate(${angle}deg)`,
                          zIndex: total - i,
                        }}
                      >
                        <div className="drag-file-card__icon"><Icons.File /></div>
                        {name ? <span className="drag-file-card__name">{name}</span> : null}
                      </div>
                    );
                  })}
                  {draggedNames.length > 5 ? (
                    <div className="drag-file-card drag-file-card--fan drag-file-card--more" style={{ transform: `translateX(50px) rotate(35deg)`, zIndex: 0 }}>
                      <span>+{draggedNames.length - 5}</span>
                    </div>
                  ) : null}
                </>
              )}
            </div>
            <span className="drag-over-hint">{draggedNames.length > 1 ? `松开以添加 ${draggedNames.length} 个文件到工作区` : "松开以上传文件到工作区"}</span>
          </div>
        ) : null}
        <header className="top-nav">
          <div className="nav-left">
            <button className="icon-button subtle nav-trigger" onClick={() => setIsSidebarOpen(!isSidebarOpen)}>
              {isSidebarOpen ? <Icons.SidebarClose /> : <Icons.Menu />}
            </button>
            <span className="session-badge">
              {isSubagentView
                ? `子代理 · ${activeSubagentName || activeSubagentSessionId}`
                : isNewSession && !sessionId && !localSessionId
                  ? t("workbench.session.new")
                  : `Session ID: ${sessionId || localSessionId || t("workbench.session.initializing")}`}
            </span>
          </div>
          <div className="nav-right">
            {!themeLocked ? (
              <button
                className="theme-toggle-pill"
                onClick={() => setTheme(theme === "system" ? "light" : theme === "light" ? "dark" : "system")}
                title={theme === "system" ? "跟随系统" : theme === "light" ? "浅色" : "深色"}
              >
                {theme === "light" ? <Icons.Sun /> : theme === "dark" ? <Icons.Moon /> : <Icons.ThemeAuto />}
                <span>{theme === "system" ? "auto" : theme}</span>
              </button>
            ) : null}
            <ContextStatusPill contextStatus={contextStatus} onOpenLlmConfig={() => void openLlmDialog()} />
          </div>
        </header>

        {connectionNotice ? <div className="connection-notice" role="status">
          {connectionNotice}
          {reconnectAvailable && <button type="button" onClick={() => requestSessionReload(activeSessionId)}>重新连接</button>}
        </div> : null}
        {error ? <div className="error-toast anim-shake">{error}</div> : null}
        {compactionProgress && contextStatus?.state !== "compacting" &&
          <div className="connection-notice" role="status">{compactionProgress}</div>}

        <div className="chat-area chat-area--virtual" ref={setHistoryElement}>
          <ChatTimeline
            key={activeSessionId}
            sessionId={activeSessionId}
            scrollElement={historyElement}
            scrollToLatestRef={scrollToLatestRef}
            transcriptStore={transcriptStore}
            hasOlder={Boolean(olderCursor)}
            loadingOlder={loadingOlder}
            olderError={olderError}
            onLoadOlder={() => void loadOlderTranscript()}
            loading={loading}
            messages={messages}
            subagents={subagents}
            actionsDisabled={displayedBusy || loading}
            onOpenSubagent={(childSessionId) => openSubagentSession(childSessionId)}
            onForkUserMessage={(messageId) => void handleForkFromMessage(messageId)}
            onRerunFromMessage={(messageId) => void handleRerunFromMessage(messageId)}
            onEditUserMessage={(messageId, content) => void handleEditUserMessage(messageId, content)}
          />
        </div>

        <div className="runtime-panels">
          <WorkboardDock
            workboard={displayedWorkboard}
            visible={workboardVisible}
            busy={displayedBusy}
            onToggle={() => {
              if (!mainSessionId) return;
              setWorkboardVisibilityBySession((current) => ({
                ...current,
                [mainSessionId]: false,
              }));
            }}
            onApplyOps={handleWorkboardOps}
          />
          <ElicitationPanel
            request={pendingElicitationRequest}
            busy={elicitationBusy}
            waitingForRun={displayedBusy}
            onSubmit={(responses) => void handleElicitationSubmit(responses)}
          />
        </div>

        {refreshNotice?.sessionId === mainSessionId && (
          <div className="workbench-command-notice" role="status">{refreshNotice.text}</div>
        )}
        <Composer
          busy={displayedBusy}
          canStop={displayedBusy || Boolean(pendingElicitationRequest?.blocking)}
          stopping={stoppingSessionId === activeSessionId}
          disabled={composerDisabled || currentToolRefresh}
          onCommand={runWorkbenchCommand}
          commandAvailability={commandAvailability}
          allowNetwork={allowNetwork}
          reasoningEffort={reasoningEffort}
          reasoningEffortOptions={reasoningEffortOptions}
          queuedMessages={queuedMessages}
          workboardVisible={workboardVisible}
          workboardCount={displayedWorkboard?.items?.length ?? 0}
          workboardCompleted={displayedWorkboard?.items?.filter((i) => i.status === "completed").length ?? 0}
          subagents={subagents}
          subagentPopoverOpen={subagentPopoverOpen}
          activeChildSessionId={activeSubagentSessionId}
          readOnlySubagentView={isSubagentView}
          onAllowNetworkChange={setAllowNetwork}
          onReasoningEffortChange={setReasoningEffort}
          onWorkboardToggle={() =>
            mainSessionId
              ? setWorkboardVisibilityBySession((current) => ({
                  ...current,
                  [mainSessionId]: !(current[mainSessionId] ?? false),
                }))
              : undefined
          }
          onSubagentPopoverOpenChange={setSubagentPopoverOpen}
          onOpenSubagent={(subagent) => openSubagentSession(subagent.child_session_id, subagent.name)}
          onStopSubagent={(subagent) => void handleStopSubagent(subagent)}
          onDestroySubagent={(subagent) => void handleDestroySubagent(subagent)}
          onExitSubagent={exitSubagentSession}
          onSend={(text) => handleSend(text)}
          onStop={() => void handleStop()}
          onRemoveQueued={handleRemoveQueued}
          onUpload={(file) => void handleUpload(file)}
        />
      </section>
    </main>
  );
}
