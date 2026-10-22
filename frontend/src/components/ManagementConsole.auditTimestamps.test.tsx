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
