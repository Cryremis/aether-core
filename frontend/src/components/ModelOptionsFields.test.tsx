import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { ModelOptionsFields } from "./ModelOptionsFields";
import { validateLlmConfigContract } from "../api/client";

vi.mock("../api/client", () => ({ validateLlmConfigContract: vi.fn(async () => ({ data: { validation_state: "offline_contract_passed", real_service_verified: false } })) }));

it("sends the same form sampling to offline validation without credentials or model invocation", async () => {
  (globalThis as unknown as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement("div");
  const root = createRoot(host);
  const form = { model_profile: "deepseek-thinking" as const, base_url: "http://local/v1", model: "alias", api_key: "private-key", sampling_seed: "0", sampling_temperature: "0", deployment_profile_text: '{"context_window":65536}', model_defaults: ["frequency_penalty"] };
  try {
    await act(async () => { root.render(<ModelOptionsFields value={form} onChange={() => undefined} />); });
    const button = Array.from(host.querySelectorAll("button")).find((item) => item.textContent?.includes("离线协议检查"));
    expect(button).toBeDefined();
    await act(async () => { button!.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(validateLlmConfigContract).toHaveBeenCalledWith(expect.objectContaining({ model: "alias", sampling: expect.objectContaining({ seed: 0, temperature: 0, deployment_profile: { context_window: 65536 }, model_profile: "deepseek-thinking" }) }));
    expect(JSON.stringify(vi.mocked(validateLlmConfigContract).mock.calls[0])).not.toContain("private-key");
    expect(host.textContent).toContain("offline_contract_passed");
    expect(host.textContent).toContain("real_service_verified");
  } finally {
    await act(async () => { root.unmount(); });
    delete (globalThis as unknown as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT;
  }
});
