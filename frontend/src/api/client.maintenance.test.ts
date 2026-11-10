import { afterEach, expect, it, vi } from "vitest";
import { bootstrapAdminSession, ServiceRestartingError, SessionRunConflictError, streamChat } from "./client";

afterEach(() => vi.unstubAllGlobals());

it("reports rejected maintenance requests distinctly and never retries creating a run", async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ code: "service_restarting", detail: "服务正在更新" }),
    { status: 503, headers: { "Content-Type": "application/json", "Retry-After": "3" } }));
  vi.stubGlobal("fetch", fetch);
  await expect(streamChat("session", "保留这条消息", true, () => {})).rejects.toBeInstanceOf(ServiceRestartingError);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0]).toBeDefined();
  await expect(bootstrapAdminSession()).rejects.toBeInstanceOf(ServiceRestartingError);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("reports a competing run as a rejected send so the workbench can keep the draft", async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ detail: "当前会话已有执行中的任务" }),
    { status: 409, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetch);
  await expect(streamChat("session", "不要丢掉这条草稿", true, () => {})).rejects.toBeInstanceOf(SessionRunConflictError);
  expect(fetch).toHaveBeenCalledTimes(1);
});
