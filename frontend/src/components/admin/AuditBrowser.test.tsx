import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getAdminConversationDetail, listAdminConversations, type AuditConversationDetail } from "../../api/client";
import { AuditBrowser } from "./AuditBrowser";

vi.mock("../../api/client", () => ({ listAdminConversations: vi.fn(), getAdminConversationDetail: vi.fn() }));
vi.mock("../workbench/ChatTimeline", () => ({ ChatTimeline: ({ messages }: { messages: unknown[] }) => <div data-timeline>{JSON.stringify(messages)}</div> }));

const items = ["a", "b"].map((id) => ({ session_id: id, conversation_id: id, title: `会话 ${id}`, host_name: "test", message_count: 230, created_at: "2026-01-01T00:00:00Z", last_message_at: "2026-02-01T00:00:00Z" }));
const page = { items, total: 650, limit: 50, offset: 0 };
function detail(id: string): AuditConversationDetail {
  return { session_id: id, title: id, host_name: "test", message_count: 230, allow_network: false, created_at: "2026-01-01", audit: items.find((item) => item.session_id === id)!, messages: [], transcript: [{ id, role: "user", content: `内容 ${id}` }], transcript_page: { total: 230, start: 200, end: 230, has_more: true } };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("AuditBrowser", () => {
  let host: HTMLDivElement;
  let root: Root;
  const click = async (text: string) => {
    const button = Array.from(host.querySelectorAll("button")).find((item) => item.textContent?.includes(text));
    expect(button).toBeDefined();
    await act(async () => button!.click());
  };
  beforeEach(() => {
    (globalThis as unknown as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
    vi.clearAllMocks();
    vi.mocked(listAdminConversations).mockResolvedValue({ data: page });
    vi.mocked(getAdminConversationDetail).mockImplementation(async (id) => ({ data: detail(id) }));
    host = document.createElement("div");
    root = createRoot(host);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    delete (globalThis as unknown as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT;
  });

  it("paginates metadata and refreshes the list independently of details", async () => {
    await act(async () => root.render(<AuditBrowser platformId={7} />));
    expect(listAdminConversations).toHaveBeenCalledWith(7, expect.objectContaining({ limit: 50, offset: 0 }), expect.any(AbortSignal));
    expect(getAdminConversationDetail).not.toHaveBeenCalled();
    await click("会话 a");
    expect(getAdminConversationDetail).toHaveBeenCalledTimes(1);
    expect(getAdminConversationDetail).toHaveBeenCalledWith("a", { limit: 30, before: undefined }, expect.any(AbortSignal));
    await click("刷新列表");
    expect(getAdminConversationDetail).toHaveBeenCalledTimes(1);
    await click("下一页");
    expect(listAdminConversations).toHaveBeenLastCalledWith(7, expect.objectContaining({ offset: 50 }), expect.any(AbortSignal));
    await click("更早消息");
    expect(getAdminConversationDetail).toHaveBeenLastCalledWith("a", { limit: 30, before: 200 }, expect.any(AbortSignal));
  });

  it("sends search and date filters to the server and resets pagination on sort", async () => {
    await act(async () => root.render(<AuditBrowser platformId={7} />));
    const input = host.querySelector<HTMLInputElement>('input[type="search"]')!;
    const from = host.querySelector<HTMLInputElement>('[aria-label="开始日期"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "测试用户");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(from, "2026-02-01");
      from.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => host.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(listAdminConversations).toHaveBeenLastCalledWith(7, expect.objectContaining({ q: "测试用户", time_from: new Date("2026-02-01T00:00:00").toISOString() }), expect.any(AbortSignal));
    const sort = host.querySelector<HTMLSelectElement>('[aria-label="排序字段"]')!;
    await act(async () => { sort.value = "created_at"; sort.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(listAdminConversations).toHaveBeenLastCalledWith(7, expect.objectContaining({ sort_by: "created_at", offset: 0 }), expect.any(AbortSignal));
  });

  it("aborts stale details and prevents a late response replacing the selected conversation", async () => {
    const first = deferred<{ data: AuditConversationDetail }>();
    vi.mocked(getAdminConversationDetail).mockImplementation((id) => id === "a" ? first.promise : Promise.resolve({ data: detail(id) }));
    await act(async () => root.render(<AuditBrowser platformId={7} />));
    await click("会话 a");
    await click("会话 b");
    expect(vi.mocked(getAdminConversationDetail).mock.calls[0][2]?.aborted).toBe(true);
    await act(async () => first.resolve({ data: detail("a") }));
    expect(host.querySelector("[data-timeline]")?.textContent).toContain("内容 b");
    expect(host.querySelector("[data-timeline]")?.textContent).not.toContain("内容 a");
  });

  it("handles refresh failures and allows retry", async () => {
    await act(async () => root.render(<AuditBrowser platformId={7} />));
    vi.mocked(listAdminConversations).mockRejectedValueOnce(new Error("列表不可用"));
    await click("刷新列表");
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("列表不可用");
    await click("刷新列表");
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });
});
