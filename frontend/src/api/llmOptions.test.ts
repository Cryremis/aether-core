import { describe, expect, it } from "vitest";
import { loadAdvancedSampling, parseAdvancedSampling, samplingFromForm } from "./llmOptions";

describe("shared model settings", () => {
  it("preserves zero, false, exact stop text and deployment metadata through reload", () => {
    const form = loadAdvancedSampling({ seed: 0, parallel_tool_calls: false, stop: ["\nEND  ", "192.0.2.10"], deployment_profile: { service_kind: "vllm", context_window: 65536 }, compaction_max_calls: 16 });
    expect(parseAdvancedSampling(form)).toMatchObject({ seed: 0, parallel_tool_calls: false, stop: ["\nEND  ", "192.0.2.10"], deployment_profile: { service_kind: "vllm", context_window: 65536 }, compaction_max_calls: 16 });
    expect(samplingFromForm({ ...form, sampling_temperature: "0", model_profile: "deepseek-thinking", stream_usage: false }))
      .toMatchObject({ temperature: 0, model_profile: "deepseek-thinking", stream_usage: false, seed: 0 });
  });
  it("rejects invalid JSON, non-integral seeds and non-finite numbers before saving", () => {
    expect(() => parseAdvancedSampling({ deployment_profile_text: "[]" })).toThrow();
    expect(() => parseAdvancedSampling({ sampling_stop_text: "[12]" })).toThrow();
    expect(() => parseAdvancedSampling({ sampling_seed: "1.5" })).toThrow();
    expect(() => samplingFromForm({ sampling_temperature: "NaN" })).toThrow();
  });
  it("keeps inheritance distinct from explicit model defaults", () => {
    expect(samplingFromForm({ model_defaults: ["seed", "stop"], sampling_seed: "", sampling_stop_text: "" }))
      .toMatchObject({ seed: null, stop: null, model_defaults: ["seed", "stop"] });
  });
});
