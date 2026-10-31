import type { LlmSamplingParams } from "./client";

/** 平台与个人设置共用的高级采样解析，避免两套表单产生不同请求。 */
export function parseAdvancedSampling(form: Record<string, unknown>) {
  const number = (key: string) => {
    const text = String(form[key] ?? "").trim();
    if (!text) return null;
    const value = Number(text);
    if (!Number.isFinite(value)) throw new Error(`${key} 必须为有效数字`);
    return value;
  };
  const stopText = String(form.sampling_stop_text ?? "").trim();
  let stop: string | string[] | null = null;
  if (stopText) {
    if (/^[\["{]/.test(stopText)) stop = JSON.parse(stopText);
    else stop = stopText;
    if (typeof stop !== "string" && (!Array.isArray(stop) || stop.some((item) => typeof item !== "string"))) throw new Error("stop 必须为字符串或字符串数组");
  }
  const seed = number("sampling_seed");
  if (seed != null && !Number.isSafeInteger(seed)) throw new Error("seed 必须为安全整数");
  const profileText = String(form.deployment_profile_text ?? "").trim();
  const deployment = profileText ? JSON.parse(profileText) : null;
  if (deployment != null && (typeof deployment !== "object" || Array.isArray(deployment))) throw new Error("部署能力必须为 JSON 对象");
  return {
    stop, seed, deployment_profile: deployment as Record<string, unknown> | null,
    parallel_tool_calls: form.parallel_tool_calls as boolean | undefined,
    compaction_max_output_tokens: number("compaction_max_output_tokens"),
    compaction_max_calls: number("compaction_max_calls"),
  };
}

export function samplingFromForm(form: Record<string, unknown>): LlmSamplingParams {
  const number = (name: string) => {
    const text = String(form[name] ?? "").trim();
    if (!text) return null;
    const value = Number(text);
    if (!Number.isFinite(value)) throw new Error(`${name} 必须为有效数字`);
    return value;
  };
  return {
    ...parseAdvancedSampling(form),
    context_window: number("sampling_context_window"), max_output_tokens: number("sampling_max_output_tokens"),
    temperature: number("sampling_temperature"), frequency_penalty: number("sampling_frequency_penalty"),
    presence_penalty: number("sampling_presence_penalty"), top_p: number("sampling_top_p"), repetition_penalty: number("sampling_repetition_penalty"),
    reasoning_effort: String(form.sampling_reasoning_effort ?? "").trim() || null,
    model_defaults: (form.model_defaults as string[] | undefined) ?? [],
    model_profile: form.model_profile as LlmSamplingParams["model_profile"],
    deployment_revision: String(form.deployment_revision ?? "").trim() || null,
    native_compaction: form.native_compaction as boolean | undefined,
    stream_usage: form.stream_usage as boolean | undefined,
    reasoning_replay: form.reasoning_replay as LlmSamplingParams["reasoning_replay"],
    reasoning_field: form.reasoning_field as LlmSamplingParams["reasoning_field"],
  };
}

export function loadAdvancedSampling(sampling?: Record<string, unknown> | null) {
  return {
    sampling_stop_text: sampling?.stop != null ? JSON.stringify(sampling.stop) : "",
    sampling_seed: sampling?.seed != null ? String(sampling.seed) : "",
    deployment_profile_text: sampling?.deployment_profile ? JSON.stringify(sampling.deployment_profile, null, 2) : "",
    parallel_tool_calls: sampling?.parallel_tool_calls as boolean | undefined,
    compaction_max_output_tokens: sampling?.compaction_max_output_tokens != null ? String(sampling.compaction_max_output_tokens) : "",
    compaction_max_calls: sampling?.compaction_max_calls != null ? String(sampling.compaction_max_calls) : "",
  };
}
