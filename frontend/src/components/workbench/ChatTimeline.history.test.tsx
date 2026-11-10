import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { AppPreferencesProvider } from "../../i18n";
import type { ChatMessage } from "../../pages/workbench/types";
import { ChatTimeline } from "./ChatTimeline";

it("requests an older page when 40 messages do not fill a tall viewport", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const viewport = document.createElement("div");
  Object.defineProperties(viewport, {
    clientWidth: { value: 1600 },
    clientHeight: { value: 8000 },
  });
  document.body.append(viewport);
  const root = createRoot(viewport);
  const onLoadOlder = vi.fn();
  const messages: ChatMessage[] = Array.from({ length: 40 }, (_, index) => ({
    id: `message-${index}`, role: "user", content: "短消息",
  }));
  const render = (hasOlder: boolean, loadingOlder = false) => root.render(
    <AppPreferencesProvider>
      <ChatTimeline loading={false} messages={messages} scrollElement={viewport}
        hasOlder={hasOlder} loadingOlder={loadingOlder} onLoadOlder={onLoadOlder} />
    </AppPreferencesProvider>,
  );
  try {
    await act(async () => render(true));
    await act(async () => {
      await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
    });
    expect(onLoadOlder).toHaveBeenCalledTimes(1);
    expect(viewport.querySelector(".chat-history-loader")?.closest(".chat-virtual-list__row")).toBeNull();
    expect(viewport.querySelector('[data-index="0"]')?.textContent).toContain("短消息");
    await act(async () => render(true, true));
    await act(async () => render(false));
    expect(onLoadOlder).toHaveBeenCalledTimes(1);
  } finally {
    await act(async () => root.unmount());
    viewport.remove();
    vi.unstubAllGlobals();
  }
});
