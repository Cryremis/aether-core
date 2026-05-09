import type { CurrentUserProfile, WorkboardOperation as ApiWorkboardOperation } from "../../api/client";

export type FileItem = {
  file_id: string;
  name: string;
  category: string;
  size: number;
};

export type SkillItem = {
  name: string;
  description: string;
  source: string;
};

export type AssistantBlock =
  | { id: string; kind: "reasoning"; content: string }
  | { id: string; kind: "content"; content: string; status: "streaming" | "done" | "aborted" }
  | { id: string; kind: "elapsed"; elapsed_ms: number }
  | {
      id: string;
      kind: "runtime_notice";
      eventType: "runtime_recreated";
      title: string;
      detail?: string;
    }
  | {
      id: string;
      kind: "tool";
      title: string;
      meta: string;
      argumentsText: string;
      outputText: string;
      liveOutputText?: string;
      status: "running" | "done" | "aborted";
    };

export type AssistantSegment =
  | { id: string; kind: "bubble"; blocks: Array<Extract<AssistantBlock, { kind: "reasoning" | "content" | "runtime_notice" }>> }
  | { id: string; kind: "tool"; block: Extract<AssistantBlock, { kind: "tool" }> };

export type TranscriptMessage =
  | { id: string; role: "user"; content: string }
  | {
      id: string;
      role: "system_event";
      title: string;
      detail?: string;
      eventType: "runtime_created" | "runtime_recreated" | "context_compacted" | "context_recovered" | "context_warning" | "context_blocked";
    }
  | {
      id: string;
      role: "elicitation_response";
      title: string;
      summary: string;
      answers: Array<{ id: string; header: string; value: string }>;
      request_id?: string;
    }
  | { id: string; role: "assistant"; blocks: AssistantBlock[]; elapsedMs: number | null; streaming: boolean; startTime?: number };

export type PendingUserEcho =
  | { id: string; role: "user"; content: string }
  | {
      id: string;
      role: "elicitation_response";
      request_id?: string;
      title: string;
      summary: string;
      answers: Array<{ id: string; header: string; value: string }>;
    };

export type ChatMessage = TranscriptMessage | PendingUserEcho;

export type QueuedMessage = {
  id: string;
  content: string;
  queuedAt: number;
};

export type ContextStatus = {
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

export type SidebarView = "sessions" | "files" | "skills";

export type LlmDialogState = {
  enabled: boolean;
  base_url: string;
  model: string;
  api_key: string;
  extra_headers_text: string;
  extra_body_text: string;
  has_api_key: boolean;
  resolved_scope: "user" | "platform" | "global";
};

export type WorkbenchConversation = {
  conversation_id: string;
  session_id: string;
  title: string;
};

export type WorkbenchPageProps = {
  conversations: WorkbenchConversation[];
  currentUser?: CurrentUserProfile | null;
  isEmbedMode?: boolean;
  sessionId: string;
  isNewSession?: boolean;
  adminEntryHref?: string;
  onOpenPlatformRegistration?: () => void;
  onLogout?: () => void;
  onNewConversation?: () => void;
  onDeleteSession?: (sessionId: string) => void;
  onRenameSession?: (sessionId: string, currentTitle: string) => void;
  onSessionCreated?: (sessionId: string) => void;
  onSessionRefresh?: (sessionId?: string) => void;
  onSessionSelect?: (sessionId: string) => void;
};

export type WorkboardOperation = ApiWorkboardOperation;
