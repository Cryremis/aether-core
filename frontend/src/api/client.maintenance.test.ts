import { afterEach, expect, it, vi } from "vitest";
import { bootstrapAdminSession, ServiceRestartingError, streamChat } from "./client";

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
