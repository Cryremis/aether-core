import { act, type ComponentProps } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { Composer } from "./Composer";

it("restores explicitly rejected text without overwriting a newer draft", async () => {
  (globalThis as unknown as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  let rejectSend!: (accepted: boolean) => void;
  const onSend = vi.fn(() => new Promise<boolean>((resolve) => { rejectSend = resolve; }));
  const noop = () => {};
  const props: ComponentProps<typeof Composer> = {
    busy: false, canStop: false, stopping: false, disabled: false, allowNetwork: false,
    queuedMessages: [], workboardVisible: false, workboardCount: 0, workboardCompleted: 0,
    subagents: [], subagentPopoverOpen: false, readOnlySubagentView: false,
    reasoningEffort: "", reasoningEffortOptions: [], onAllowNetworkChange: noop,
    onReasoningEffortChange: noop, onWorkboardToggle: noop, onSubagentPopoverOpenChange: noop,
    onOpenSubagent: noop, onStopSubagent: noop, onDestroySubagent: noop, onExitSubagent: noop,
    onSend, onStop: noop, onRemoveQueued: noop, onUpload: noop,
  };
  try {
    await act(async () => root.render(<Composer {...props} />));
    const textarea = host.querySelector("textarea")!;
    const change = async (value: string) => {
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(textarea, value);
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
      });
    };
    await change("原消息");
    await act(async () => textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
    expect(onSend).toHaveBeenCalledWith("原消息");
    expect(textarea.value).toBe("");
    await change("后续草稿");
    await act(async () => rejectSend(false));
    expect(textarea.value).toBe("原消息\n\n后续草稿");
  } finally {
    await act(async () => root.unmount());
    host.remove();
    delete (globalThis as unknown as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT;
  }
});
