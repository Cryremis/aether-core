import { afterEach, expect, it, vi } from "vitest";
import { TranscriptStore } from "./transcriptStore";
import type { TranscriptMessage } from "./types";

afterEach(() => vi.restoreAllMocks());

it("keeps the history snapshot stable and notifies only the live row once per frame", () => {
  const store = new TranscriptStore();
  const history = Array.from({ length: 5_000 }, (_, index): TranscriptMessage =>
    ({ id: `user-${index}`, role: "user", content: "previous message" }));
  store.set([...history, { id: "live", role: "assistant", blocks: [], elapsedMs: null, streaming: true }]);
  const order = store.snapshot();
  const historyListener = vi.fn();
  const olderListener = vi.fn();
  const liveListener = vi.fn();
  store.subscribe(historyListener);
  store.subscribeRow(history[0].id, olderListener);
  store.subscribeRow("live", liveListener);
  const requestFrame = vi.spyOn(window, "requestAnimationFrame").mockReturnValue(1);
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
  for (let index = 0; index < 100; index++) {
    store.patch("live", (row) => ({ ...row, elapsedMs: index }), true);
  }
  expect(store.snapshot()).toBe(order);
  expect(requestFrame).toHaveBeenCalledTimes(1);
  expect(liveListener).not.toHaveBeenCalled();
  store.flush();
  expect(liveListener).toHaveBeenCalledTimes(1);
  expect(historyListener).not.toHaveBeenCalled();
  expect(olderListener).not.toHaveBeenCalled();
  expect(store.get("live")).toMatchObject({ id: "live", elapsedMs: 99 });
});
