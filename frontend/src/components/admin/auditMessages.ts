import type { AuditConversationDetail, TranscriptAssistantBlock, TranscriptChatMessage } from "../../api/client";
import type { ChatMessage, AssistantBlock } from "../../pages/workbench/types";

export function isRuntimeActive(status: string) {
  return ["provisioning", "running", "executing"].includes(status);
}

export function getRuntimeStatusLabel(status: string) {
  if (status === "running") return "运行中";
  if (status === "executing") return "执行中";
  if (status === "provisioning") return "创建中";
  if (status === "expired") return "已过期";
  if (status === "collected") return "已回收";
  if (status === "failed") return "已失效";
  if (status === "missing") return "容器丢失";
  return status;
}

export function getRuntimeStatusClass(status: string) {
  if (status === "running" || status === "executing" || status === "provisioning") return "approved";
  if (status === "expired" || status === "failed" || status === "missing") return "rejected";
  return "returned";
}

export function convertAuditDetailToChatMessages(detail: AuditConversationDetail | null): ChatMessage[] {
  if (!detail) return [];

  const transcript = detail.transcript;
  if (transcript && transcript.length > 0) {
    return transcript.map((msg, index): ChatMessage => {
      if (msg.role === "user") {
        return {
          id: `audit-${detail.session_id}-t-${index}`,
          role: "user",
          content: msg.content || "",
          timestamp: msg.timestamp ?? null,
        };
      }

      if (msg.role === "system_event") {
        return {
          id: `audit-${detail.session_id}-t-${index}`,
          role: "system_event",
          title: msg.title,
          detail: msg.detail,
          summary: msg.summary,
          eventType: msg.eventType,
          timestamp: msg.timestamp ?? null,
        };
      }

      if (msg.role === "elicitation_response") {
        return {
          id: `audit-${detail.session_id}-t-${index}`,
          role: "elicitation_response",
          title: msg.title,
          summary: msg.summary,
          answers: msg.answers,
          timestamp: msg.timestamp ?? null,
        };
      }

      const msgBlocks = (msg as Extract<TranscriptChatMessage, { role: "assistant" }>).blocks ?? [];
      const blocks: AssistantBlock[] = msgBlocks.map((block, blockIndex): AssistantBlock => {
        if (block.kind === "reasoning") {
          return {
            id: `audit-${detail.session_id}-t-${index}-b-${blockIndex}`,
            kind: "reasoning",
            content: block.content || "",
          };
        }
        if (block.kind === "content") {
          return {
            id: `audit-${detail.session_id}-t-${index}-b-${blockIndex}`,
            kind: "content",
            content: block.content || "",
            status: "done",
          };
        }
        if (block.kind === "tool") {
          const toolBlock = block as Extract<TranscriptAssistantBlock, { kind: "tool" }>;
          return {
            id: `audit-${detail.session_id}-t-${index}-b-${blockIndex}`,
            kind: "tool",
            title: toolBlock.title || "Tool",
            toolName: toolBlock.toolName,
            meta: toolBlock.meta || "",
            argumentsText: toolBlock.argumentsText || "",
            outputText: toolBlock.outputText || "",
            status: "done",
          };
        }
        if (block.kind === "runtime_notice") {
          return {
            id: `audit-${detail.session_id}-t-${index}-b-${blockIndex}`,
            kind: "runtime_notice",
            eventType: "runtime_recreated",
            title: block.title || "",
            detail: block.detail,
          };
        }
        return {
          id: `audit-${detail.session_id}-t-${index}-b-${blockIndex}`,
          kind: "content",
          content: "",
          status: "done",
        };
      });

      return {
        id: `audit-${detail.session_id}-t-${index}`,
        role: "assistant",
        blocks,
        elapsedMs: null,
        streaming: false,
        timestamp: msg.timestamp ?? null,
      };
    });
  }

  const messages = detail.messages ?? [];
  return messages.map((msg, index): ChatMessage => {
    if (msg.role === "user") {
      return {
        id: `audit-${detail.session_id}-m-${index}`,
        role: "user",
        content: msg.content || "",
        timestamp: msg.timestamp ?? null,
      };
    }

    const blocks: AssistantBlock[] = [];
    const normalizedTopLevelContent = msg.content?.trim() ?? "";
    const normalizedBlockContents = Array.isArray(msg.blocks)
      ? msg.blocks
          .filter((block: Record<string, unknown>) => block.kind === "content" && typeof block.content === "string")
          .map((block: Record<string, unknown>) => String(block.content).trim())
          .filter(Boolean)
      : [];
    const shouldIncludeTopLevelContent = Boolean(
      normalizedTopLevelContent &&
      !normalizedBlockContents.some((content) => content === normalizedTopLevelContent),
    );

    if (shouldIncludeTopLevelContent) {
      blocks.push({
        id: `audit-${detail.session_id}-m-${index}-c`,
        kind: "content",
        content: normalizedTopLevelContent,
        status: "done",
      });
    }
    if (Array.isArray(msg.blocks) && msg.blocks.length > 0) {
      msg.blocks.forEach((block: Record<string, unknown>, blockIndex: number) => {
        const kind = block.kind as string;
        const content = typeof block.content === "string" ? block.content : "";
        if (kind === "reasoning") {
          blocks.push({
            id: `audit-${detail.session_id}-m-${index}-b-${blockIndex}`,
            kind: "reasoning",
            content,
          });
        } else if (kind === "content") {
          blocks.push({
            id: `audit-${detail.session_id}-m-${index}-b-${blockIndex}`,
            kind: "content",
            content,
            status: "done",
          });
        } else {
          blocks.push({
            id: `audit-${detail.session_id}-m-${index}-b-${blockIndex}`,
            kind: "content",
            content: JSON.stringify(block, null, 2),
            status: "done",
          });
        }
      });
    }

    return {
      id: `audit-${detail.session_id}-m-${index}`,
      role: "assistant",
      blocks,
      elapsedMs: null,
      streaming: false,
      timestamp: msg.timestamp ?? null,
    };
  });
}
