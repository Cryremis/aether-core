import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { cancelCrossChatDelivery, listCrossChatDeliveries } from "../../api/client";
import { CrossChatDeliveriesDialog } from "./CrossChatDeliveriesDialog";

vi.mock("../../api/client", () => ({
  listCrossChatDeliveries: vi.fn(),
  cancelCrossChatDelivery: vi.fn(),
}));

it("opens the actual peer session and refreshes the receipt after cancelling a queued delivery", async () => {
  (globalThis as unknown as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const openSession = vi.fn();
  const load = vi.mocked(listCrossChatDeliveries);
  const cancel = vi.mocked(cancelCrossChatDelivery);
  const item = {
    delivery_id: "xchat_one", status: "queued", mode: "queue", direction: "sent",
    peer_title: "目标会话", peer_session_id: "sess_target", run_status: null, detail: null,
    message: "实际发送的正文", created_at: "2026-11-10", expires_at: "2026-11-10",
  } as const;
  load.mockResolvedValueOnce({ items: [item], next_cursor: null });
  load.mockResolvedValue({ items: [{ ...item, status: "cancelled" }], next_cursor: null });
  cancel.mockResolvedValue(undefined);
  const click = (label: string) => act(async () => {
    [...host.querySelectorAll("button")].find((button) => button.textContent === label)?.click();
  });
  try {
    await act(async () => root.render(<CrossChatDeliveriesDialog
      sessionId="sess_source" onClose={() => {}} onOpenSession={openSession}
    />));
    expect(host.textContent).toContain("已排队");
    expect(host.textContent).toContain("实际发送的正文");
    await click("打开会话");
    expect(openSession).toHaveBeenCalledWith("sess_target");
    await click("撤销排队");
    expect(cancel).toHaveBeenCalledWith("sess_source", "xchat_one");
    expect(host.textContent).toContain("已撤销");
    expect(host.textContent).not.toContain("撤销排队");
  } finally {
    await act(async () => root.unmount());
    host.remove();
    delete (globalThis as unknown as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT;
  }
});
