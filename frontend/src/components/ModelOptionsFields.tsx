import { useState } from "react";
import { validateLlmConfigContract } from "../api/client";
import { samplingFromForm } from "../api/llmOptions";

export type ModelOptionsState = {
  api_format?: "openai-compatible" | "openai-responses" | "anthropic-messages";
  sampling_context_window?: string;
  sampling_max_output_tokens?: string;
  sampling_reasoning_effort?: string;
  model_defaults?: string[];
  native_compaction?: boolean;
  reasoning_replay?: "auto" | "disabled";
  reasoning_field?: "reasoning_content" | "reasoning";
  sampling_sources?: Record<string, string>;
  model_profile?: "auto" | "generic" | "deepseek-thinking";
  deployment_revision?: string;
  deployment_profile_text?: string;
  sampling_stop_text?: string;
  sampling_seed?: string;
  parallel_tool_calls?: boolean;
  compaction_max_output_tokens?: string;
  compaction_max_calls?: string;
  stream_usage?: boolean;
};

export function ModelOptionsFields({ value, onChange }: {
  value: ModelOptionsState;
  onChange: (patch: ModelOptionsState & Record<string, unknown>) => void;
}) {
  const parameters = ["temperature", "frequency_penalty", "presence_penalty", "top_p", "repetition_penalty", "reasoning_effort", "stop", "seed", "parallel_tool_calls"];
  const [validation, setValidation] = useState("");
  const [validating, setValidating] = useState(false);
  const validate = async () => {
    setValidating(true);
    try {
      const form = value as unknown as Record<string, unknown>;
      const result = await validateLlmConfigContract({ enabled: true, api_format: value.api_format ?? "openai-compatible",
        base_url: String(form.base_url ?? ""), model: String(form.model ?? ""),
        extra_headers: JSON.parse(String(form.extra_headers_text || "{}")), extra_body: JSON.parse(String(form.extra_body_text || "{}")),
        sampling: samplingFromForm(form) });
      setValidation(JSON.stringify(result.data, null, 2));
    } catch (error) { setValidation(error instanceof Error ? error.message : "离线检查失败"); }
    finally { setValidating(false); }
  };
  return <div className="llm-sampling-grid">
    <label className="admin-panel__field"><span>接口协议</span><select value={value.api_format ?? "openai-compatible"} onChange={(e) => onChange({ api_format: e.target.value as ModelOptionsState["api_format"], ...(e.target.value !== "openai-responses" ? { native_compaction: false } : {}) })}>
      <option value="openai-compatible">Chat Completions / vLLM</option>
      <option value="openai-responses">OpenAI Responses</option>
      <option value="anthropic-messages">Claude Messages</option>
    </select></label>
    <label className="admin-panel__field"><span>模型协议配置</span><select value={value.model_profile ?? "auto"} onChange={(e) => onChange({ model_profile: e.target.value as ModelOptionsState["model_profile"] })}>
      <option value="auto">自动识别（DeepSeek V3.2／V4／Flash 或官方地址）</option>
      <option value="deepseek-thinking">DeepSeek 思考模式：工具请求必须回传全部历史思考</option>
      <option value="generic">通用：遵循服务声明的回传选项</option>
    </select></label>
    <label className="admin-panel__field"><span>部署上下文上限（tokens）</span><input type="number" min="1024" value={value.sampling_context_window ?? ""} placeholder="继承模型目录；内网请填写实际部署上限" onChange={(e) => onChange({ sampling_context_window: e.target.value })} /></label>
    <label className="admin-panel__field"><span>输出上限（含协议规定的思考额度）</span><input type="number" min="1" value={value.sampling_max_output_tokens ?? ""} placeholder="继承" onChange={(e) => onChange({ sampling_max_output_tokens: e.target.value })} /></label>
    <label className="admin-panel__field"><span>思考力度（服务支持的名称或 1–100）</span><input value={value.sampling_reasoning_effort ?? ""} placeholder="模型默认" onChange={(e) => onChange({ sampling_reasoning_effort: e.target.value, model_defaults: value.model_defaults?.filter((key) => key !== "reasoning_effort") })} /></label>
    <label className="admin-panel__field"><span>Chat 思考历史回传</span><select disabled={!!value.api_format && value.api_format !== "openai-compatible"} value={value.reasoning_replay ?? "auto"} onChange={(e) => onChange({ reasoning_replay: e.target.value as ModelOptionsState["reasoning_replay"] })}><option value="auto">保留模型返回的思考</option><option value="disabled">服务不支持文本思考回传（仍保留归档）</option></select></label>
    <label className="admin-panel__field"><span>Chat 思考回传字段</span><select value={value.reasoning_field ?? "reasoning_content"} onChange={(e) => onChange({ reasoning_field: e.target.value as ModelOptionsState["reasoning_field"] })}><option value="reasoning_content">reasoning_content（DeepSeek / 常见 vLLM）</option><option value="reasoning">reasoning（其他兼容服务）</option></select></label>
    {value.api_format === "openai-responses" && <label><input type="checkbox" checked={value.native_compaction ?? false} onChange={(e) => onChange({ native_compaction: e.target.checked })} /> 使用服务端原生压缩（须部署支持）</label>}
    <label className="admin-panel__field"><span>部署修订（权重／模板／解析器版本）</span><input value={value.deployment_revision ?? ""} placeholder="部署内容变化时更新，以重建专用上下文状态" onChange={(e) => onChange({ deployment_revision: e.target.value })} /></label>
    <label><input type="checkbox" disabled={!!value.api_format && value.api_format !== "openai-compatible"} checked={value.stream_usage !== false} onChange={(e) => onChange({ stream_usage: e.target.checked })} /> Chat 流请求返回 usage（旧服务不支持时关闭）</label>
    <label className="admin-panel__field"><span>停止序列（字符串或 JSON 数组）</span><input value={value.sampling_stop_text ?? ""} placeholder={'继承；例如 ["END"]'} onChange={(e) => onChange({ sampling_stop_text: e.target.value, model_defaults: value.model_defaults?.filter((key) => key !== "stop") })} /></label>
    <label className="admin-panel__field"><span>Seed（服务支持时使用）</span><input type="number" step="1" value={value.sampling_seed ?? ""} placeholder="模型默认" onChange={(e) => onChange({ sampling_seed: e.target.value, model_defaults: value.model_defaults?.filter((key) => key !== "seed") })} /></label>
    <label className="admin-panel__field"><span>并行工具调用</span><select value={value.parallel_tool_calls == null ? "inherit" : String(value.parallel_tool_calls)} onChange={(e) => onChange({ parallel_tool_calls: e.target.value === "inherit" ? undefined : e.target.value === "true", model_defaults: value.model_defaults?.filter((key) => key !== "parallel_tool_calls") })}><option value="inherit">继承／服务默认</option><option value="true">允许</option><option value="false">禁用</option></select></label>
    <label className="admin-panel__field"><span>摘要输出额度（含思考）</span><input type="number" min="128" value={value.compaction_max_output_tokens ?? ""} placeholder="继承主请求输出额度" onChange={(e) => onChange({ compaction_max_output_tokens: e.target.value })} /></label>
    <label className="admin-panel__field"><span>每次压缩最多模型调用数</span><input type="number" min="1" max="64" value={value.compaction_max_calls ?? ""} placeholder="32" onChange={(e) => onChange({ compaction_max_calls: e.target.value })} /></label>
    <label className="admin-panel__field"><span>部署能力 JSON（声明，需真实服务验证）</span><textarea value={value.deployment_profile_text ?? ""} placeholder={'{"service_kind":"vllm","service_version":"...","context_window":65536,"reasoning_parser":"...","tool_parser":"..."}'} onChange={(e) => onChange({ deployment_profile_text: e.target.value })} /></label>
    <div className="llm-advanced-panel__hint">留空继承；勾选下方项目会停止继承该项，使用模型默认。明确填写 0 会保留为 0。</div>
    {parameters.map((key) => <label key={key}><input type="checkbox" checked={value.model_defaults?.includes(key) ?? false} onChange={(e) => onChange({ model_defaults: e.target.checked ? [...(value.model_defaults ?? []).filter((item) => item !== key), key] : (value.model_defaults ?? []).filter((item) => item !== key), ...(e.target.checked ? { [key === "stop" ? "sampling_stop_text" : key === "parallel_tool_calls" ? key : `sampling_${key}`]: key === "parallel_tool_calls" ? undefined : "" } : {}) })} /> {key} 使用模型默认</label>)}
    {value.sampling_sources && <div className="llm-advanced-panel__hint">生效来源：{parameters.map((key) => `${key}: ${value.sampling_sources?.[key] ?? "模型默认"}`).join("；")}</div>}
    <button type="button" className="ghost-btn" disabled={validating} onClick={() => void validate()}>{validating ? "检查中…" : "离线协议检查（不请求模型）"}</button>
    {validation && <pre className="llm-advanced-panel__hint">{validation}</pre>}
  </div>;
}
