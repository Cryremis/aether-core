import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AppPreferencesProvider } from "../i18n";
import type { AuditConversationDetail } from "../api/client";
import { ChatTimeline } from "./workbench/ChatTimeline";
import { convertAuditDetailToChatMessages } from "./ManagementConsole";

const userTimestamp = "2026-10-22T07:36:29.049440+00:00";
const assistantTimestamp = "2026-10-22T07:36:31.049440+00:00";

function buildAuditDetail(): AuditConversationDetail {
  return {
    session_id: "session-1",
    title: "审计会话",
    host_name: "test-host",
    message_count: 2,
    allow_network: false,
    created_at: "2026-10-22T07:36:00+00:00",
    messages: [],
    transcript: [
      { id: "user-1", role: "user", content: "你好", timestamp: userTimestamp },
      {
        id: "assistant-1",
        role: "assistant",
        blocks: [{ id: "content-1", kind: "content", content: "你好，我是 AetherCore。", status: "done" }],
        elapsedMs: null,
        streaming: false,
        timestamp: assistantTimestamp,
      },
    ],
    audit: {} as AuditConversationDetail["audit"],
  };
}

describe("audit conversation timestamps", () => {
  it("keeps a call note ahead of argument summaries in live and audit tool cards", () => {
    const detail = buildAuditDetail();
    detail.transcript![1] = { id: "assistant-note", role: "assistant", elapsedMs: null, streaming: false, blocks: [
      { id: "tool-note", kind: "tool", title: "检查训练环境", toolName: "sandbox_shell",
        meta: "bash", argumentsText: '{"command":"uname -a"}', outputText: "Linux", status: "done" },
    ] };
    const messages = convertAuditDetailToChatMessages(detail);
    const html = renderToStaticMarkup(<AppPreferencesProvider>
      <ChatTimeline loading={false} messages={messages} actionsDisabled={true} />
    </AppPreferencesProvider>);
    expect(html).toContain('class="tool-title__summary">检查训练环境</span>');
    expect(messages[1]).toMatchObject({ blocks: [{ toolName: "sandbox_shell", title: "检查训练环境" }] });
  });

  it("renders compaction summary as a separate expandable audit event", () => {
    const detail = buildAuditDetail();
    detail.transcript!.push({
      id: "compaction-1", role: "system_event", eventType: "context_compacted",
      title: "上下文压缩（仅审计可见）", detail: "输入估算：1000 → 300 tokens。",
      summary: "## 压缩后的任务\n保留事实 192.0.2.10", timestamp: assistantTimestamp,
    });
    const messages = convertAuditDetailToChatMessages(detail);
    expect(messages[2]).toMatchObject({ role: "system_event", summary: "## 压缩后的任务\n保留事实 192.0.2.10" });
    const html = renderToStaticMarkup(
      <AppPreferencesProvider>
        <ChatTimeline loading={false} messages={messages} actionsDisabled={true} showMessageTimestamps={true} />
      </AppPreferencesProvider>,
    );
    expect(html).toContain("查看压缩摘要");
    expect(html).toContain("<details");
    expect(html).toContain("保留事实 192.0.2.10");
  });

  it("maps transcript timestamps to chat messages", () => {
    const messages = convertAuditDetailToChatMessages(buildAuditDetail());

    expect(messages[0]).toMatchObject({ role: "user", timestamp: userTimestamp });
    expect(messages[1]).toMatchObject({ role: "assistant", timestamp: assistantTimestamp });
  });

  it("maps timestamps from the legacy message fallback", () => {
    const detail = buildAuditDetail();
    detail.transcript = undefined;
    detail.messages = [
      { role: "user", content: "你好", timestamp: userTimestamp },
      { role: "assistant", content: "你好，我是 AetherCore。", timestamp: assistantTimestamp },
    ];

    const messages = convertAuditDetailToChatMessages(detail);

    expect(messages[0]).toMatchObject({ role: "user", timestamp: userTimestamp });
    expect(messages[1]).toMatchObject({ role: "assistant", timestamp: assistantTimestamp });
  });

  it("keeps workbench rendering unchanged unless timestamp display is enabled", () => {
    const messages = convertAuditDetailToChatMessages(buildAuditDetail());
    const renderTimeline = (showMessageTimestamps: boolean) => renderToStaticMarkup(
      <AppPreferencesProvider>
        <ChatTimeline
          loading={false}
          messages={messages}
          actionsDisabled={true}
          showMessageTimestamps={showMessageTimestamps}
        />
      </AppPreferencesProvider>,
    );

    expect(renderTimeline(false)).not.toContain("timestamp-badge");
    expect(renderTimeline(true)).toContain("timestamp-badge");
  });
});
