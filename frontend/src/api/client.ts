// frontend/src/api/client.ts
export type PasswordLoginPayload = {
  username: string;
  password: string;
};

export type CurrentUserProfile = {
  user_id: number;
  account_id: string;
  username?: string | null;
  full_name: string;
  email?: string | null;
  role: "system_admin" | "user";
  provider: string;
  managed_platform_ids: number[];
  managed_platform_count: number;
  can_manage_system: boolean;
  can_manage_platforms: boolean;
};

export type OAuthProviderConfig = {
  provider_key: string;
  display_name: string;
  authorize_url_template: string;
};

export type PlatformCreatePayload = {
  platform_key: string;
  display_name: string;
  host_type?: "embedded" | "standalone";
  description: string;
  owner_user_id?: number;
};

export type PlatformRuntimeImagePayload = {
  image: string;
};

export type PlatformRuntimeImageSummary = {
  platform_id: number;
  custom_image?: string | null;
  resolved_image: string;
  updated_at?: string | null;
  recycled_runtime_count?: number;
};

export type PlatformRuntimeImageBuildSpec = {
  target_os: string;
  target_arch: string;
  image_format: string;
  shell: string;
  recommended_base: string;
  entrypoint: string;
  expected_workspace_root: string;
  required_directories: string[];
  required_env_vars: string[];
  resource_limits: string[];
  build_steps: string[];
  sample_dockerfile: string;
  notes: string[];
};

export type PlatformRuntimeImageGuide = {
  platform_id: number;
  display_name: string;
  current_image: string;
  build_spec: PlatformRuntimeImageBuildSpec;
};

export type PlatformSandboxProxyPayload = {
  enabled: boolean;
  http_proxy: string;
  https_proxy: string;
  all_proxy: string;
  no_proxy: string;
  inherit_host_proxy: boolean;
};

export type PlatformSandboxProxySummary = {
  platform_id: number;
  enabled: boolean;
  http_proxy: string;
  https_proxy: string;
  all_proxy: string;
  no_proxy: string;
  inherit_host_proxy: boolean;
  updated_at?: string | null;
  recycled_runtime_count?: number;
};

export type PlatformIntegrationGuide = {
  platform_key: string;
  display_name: string;
  bind_api_path: string;
  frontend_script_path: string;
  frontend_script_url: string;
  recommended_mode_id: string;
  prerequisites: string[];
  capabilities: string[];
  placeholders: Array<{
    key: string;
    label: string;
    value: string;
    required: boolean;
    description: string;
  }>;
  notes: string[];
  modes: Array<{
    mode_id: string;
    title: string;
    summary: string;
    access_stage: "quick" | "production";
    identity_scenario: "authenticated_user" | "browser_guest" | "ephemeral";
    use_when: string;
    recommended: boolean;
    backend_requirement: string;
    identity_requirement: string;
    capabilities: string[];
    steps: string[];
    warnings: string[];
    snippets: Array<{
      snippet_id: string;
      title: string;
      language: string;
      summary: string;
      content: string;
    }>;
  }>;
  snippets: {
    frontend: string;
    backend_env: string;
    backend_fastapi: string;
  };
};

export type PlatformAdminRecord = {
  user_id: number;
  full_name: string;
  email?: string | null;
  role: string;
  assigned_at?: string | null;
  is_primary: boolean;
};

export type PlatformRegistrationRequestPayload = {
  platform_key: string;
  display_name: string;
  description: string;
  justification: string;
};

export type PlatformRegistrationReviewPayload = {
  review_comment: string;
};

export type PlatformRegistrationRequestSummary = {
  request_id: number;
  applicant_user_id: number;
  applicant_name: string;
  applicant_email?: string | null;
  platform_key: string;
  display_name: string;
  description: string;
  justification: string;
  status: "pending" | "approved" | "rejected" | "returned" | "cancelled";
  review_comment: string;
  reviewed_by?: number | null;
  reviewed_by_name?: string | null;
  reviewed_at?: string | null;
  approved_platform_id?: number | null;
  created_at: string;
  updated_at: string;
};

export type UserSummary = {
  user_id: number;
  account_id: string;
  username?: string | null;
  full_name: string;
  email?: string | null;
  role: "system_admin" | "user";
  provider: string;
  is_active: boolean;
  last_login_at?: string | null;
  created_at?: string | null;
  managed_platform_ids: number[];
};

export type UserRoleUpdatePayload = {
  role: "system_admin" | "user";
};

export type SystemNetworkAddress = {
  family: "ipv4" | "ipv6";
  address: string;
  prefix_length?: number | null;
  netmask?: string | null;
  broadcast?: string | null;
  scope?: string | null;
  label?: string | null;
  is_loopback: boolean;
  is_private: boolean;
  is_link_local: boolean;
  is_multicast: boolean;
  category: "public" | "private" | "loopback" | "link_local" | "multicast" | "unknown";
};

export type SystemNetworkInterface = {
  name: string;
  display_name?: string | null;
  state: string;
  is_up: boolean;
  mtu?: number | null;
  mac_address?: string | null;
  flags: string[];
  interface_type?: string | null;
  addresses: SystemNetworkAddress[];
};

export type SystemNetworkSnapshot = {
  hostname: string;
  fqdn?: string | null;
  platform: string;
  source: string;
  namespace_scope: "host" | "container" | "unknown";
  scope_note: string;
  collected_at: string;
  summary: {
    interface_count: number;
    up_interface_count: number;
    ipv4_count: number;
    ipv6_count: number;
    public_address_count: number;
  };
  interfaces: SystemNetworkInterface[];
  raw_text?: string | null;
};

export type AddRouteFor80NetworkResult = {
  gateway_ip: string;
  available_gateway_ips: string[];
  command: string;
  stdout: string;
  stderr: string;
  return_code: number;
  namespace_scope: "host" | "container" | "unknown";
};

export type PlatformBaselineFile = {
  name: string;
  relative_path: string;
  section: "skills" | "work" | "logs";
  size: number;
  media_type: string;
};

export type PlatformBaselineEntry = {
  name: string;
  relative_path: string;
  section: "skills" | "work" | "logs";
  kind: "file" | "directory";
  size: number;
  media_type: string;
};

export type PlatformBaselineSkill = {
  name: string;
  description: string;
  allowed_tools: string[];
  tags: string[];
  relative_path: string;
};

export type PlatformBaselineSummary = {
  platform_key: string;
  files: PlatformBaselineFile[];
  entries: PlatformBaselineEntry[];
  skills: PlatformBaselineSkill[];
};

export type PlatformBaselineFileContent = {
  relative_path: string;
  media_type: string;
  content: string;
  truncated: boolean;
};

export type LlmConfigPayload = {
  enabled: boolean;
  provider_kind?: "litellm";
  api_format?: "openai-compatible";
  base_url: string;
  model: string;
  api_key?: string | null;
  clear_api_key?: boolean;
  extra_headers?: Record<string, string>;
  extra_body?: Record<string, unknown>;
  network?: LlmNetworkConfigPayload;
};

export type PromptConfigPayload = {
  enabled: boolean;
  system_prompt: string;
};

export type LlmNetworkConfigPayload = {
  enabled: boolean;
  allowed_domains: string[];
  blocked_domains: string[];
  max_search_results: number;
  fetch_timeout_seconds: number;
};

export type LlmNetworkConfigSummary = LlmNetworkConfigPayload;

export type LlmConfigSummary = {
  enabled: boolean;
  provider_kind: string;
  api_format: string;
  base_url: string;
  model: string;
  has_api_key: boolean;
  extra_headers: Record<string, string>;
  extra_body: Record<string, unknown>;
  network: LlmNetworkConfigSummary;
  updated_at?: string | null;
};

export type ResolvedLlmConfig = LlmConfigSummary & {
  scope: "user" | "platform" | "global";
};

export type PromptConfigSummary = {
  enabled: boolean;
  system_prompt: string;
  updated_at?: string | null;
};

export type SessionRuntimeSummary = {
  session_id: string;
  conversation_id?: string | null;
  conversation_title?: string | null;
  conversation_host_name?: string | null;
  platform_id?: number | null;
  platform_display_name?: string | null;
  owner_user_id?: number | null;
  owner_user_name?: string | null;
  external_user_id?: string | null;
  container_name?: string | null;
  container_id?: string | null;
  image?: string | null;
  status: string;
  generation?: number;
  network_mode?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  last_started_at?: string | null;
  last_used_at?: string | null;
  idle_expires_at?: string | null;
  max_expires_at?: string | null;
  destroyed_at?: string | null;
  destroy_reason?: string | null;
  restart_count?: number;
  workspace_root?: string | null;
  home_root?: string | null;
  metadata?: Record<string, unknown>;
};

export type AuditConversationSummary = {
  conversation_id: string;
  session_id: string;
  title: string;
  host_name: string;
  platform_id?: number | null;
  platform_display_name?: string | null;
  owner_user_id?: number | null;
  owner_user_name?: string | null;
  external_user_id?: string | null;
  external_user_name?: string | null;
  external_org_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  last_message_at?: string | null;
  message_count: number;
  conversation_key?: string | null;
};

export type AuditSessionMessage = {
  role: "user" | "assistant" | "tool";
  content: string;
  blocks?: Array<Record<string, unknown>>;
};

export type TranscriptAssistantBlock =
  | { id: string; kind: "reasoning"; content: string }
  | { id: string; kind: "content"; content: string; status: "streaming" | "done" | "aborted" }
  | { id: string; kind: "runtime_notice"; eventType: "runtime_recreated"; title: string; detail?: string }
  | { id: string; kind: "elapsed"; elapsed_ms: number }
  | { id: string; kind: "tool"; title: string; meta: string; argumentsText: string; outputText: string; liveOutputText?: string; status: "running" | "done" | "aborted" };

export type TranscriptChatMessage =
  | { id: string; role: "user"; content: string }
  | {
      id: string;
      role: "elicitation_response";
      request_id: string;
      title: string;
      summary: string;
      answers: Array<{ id: string; header: string; value: string }>;
    }
  | {
      id: string;
      role: "assistant";
      blocks: TranscriptAssistantBlock[];
      elapsedMs: number | null;
      streaming: boolean;
      responseStartedAt?: string | null;
    };

export type TimelineRerunResponse = {
  success: boolean;
  rerun_prompt: string;
  source_message_id: string;
  anchor_message_id: string;
  rerun_message_id: string;
  truncated_count: number;
};

export type TimelineForkResponse = {
  success: boolean;
  session_id: string;
  conversation_id: string;
  source_message_id: string;
};

export type ActiveRunSummary = {
  run_id: string;
  session_id: string;
  status: "running" | "completed";
  started_at: string;
  updated_at: string;
  assistant: {
    id: string;
    role: "assistant";
    blocks: TranscriptAssistantBlock[];
    elapsedMs: number | null;
    streaming: boolean;
    response_started_at?: string | null;
  };
};

export type CommittedChatMessage =
  | { id: string; role: "user"; content: string }
  | {
      id: string;
      role: "elicitation_response";
      request_id: string;
      title: string;
      summary: string;
      answers: Array<{ id: string; header: string; value: string }>;
    };

export type AuditConversationDetail = {
  session_id: string;
  conversation_id?: string | null;
  title: string;
  host_name: string;
  message_count: number;
  allow_network: boolean;
  created_at: string;
  messages: AuditSessionMessage[];
  transcript?: TranscriptChatMessage[];
  runtime?: SessionRuntimeSummary | null;
  audit: AuditConversationSummary;
};

export type PlatformAuditOverviewItem = {
  platform_id: number;
  platform_key: string;
  display_name: string;
  owner_user_id: number;
  owner_name: string;
  admin_count: number;
  hosted_user_count: number;
  conversation_count: number;
  message_count: number;
  active_runtime_count: number;
  runtime_count: number;
  last_activity_at?: string | null;
};

export type SystemAuditOverview = {
  platform_count: number;
  platforms_with_traffic_count: number;
  hosted_user_count: number;
  internal_user_count: number;
  platform_admin_assignment_count: number;
  pending_registration_request_count: number;
  conversation_count: number;
  message_count: number;
  active_runtime_count: number;
  runtime_count: number;
  platforms: PlatformAuditOverviewItem[];
};

export type WorkItemStatus = "pending" | "in_progress" | "completed" | "blocked" | "cancelled";
export type WorkboardStatus = "idle" | "active" | "completed" | "blocked";

export type WorkItem = {
  id: string;
  title: string;
  active_form?: string | null;
  status: WorkItemStatus;
  priority: "low" | "medium" | "high";
  owner: string;
  depends_on: string[];
  blocked_by: string[];
  notes?: string | null;
  source: string;
  evidence_refs: string[];
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
};

export type WorkboardState = {
  session_id: string;
  revision: number;
  status: WorkboardStatus;
  items: WorkItem[];
  updated_at: string;
};

export type WorkboardOperation =
  | {
      op: "add_item";
      id?: string;
      title: string;
      notes?: string | null;
      priority?: "low" | "medium" | "high";
      status?: WorkItemStatus;
      source?: string;
      owner?: string;
    }
  | {
      op: "update_item";
      id: string;
      title?: string;
      notes?: string | null;
      priority?: "low" | "medium" | "high";
      status?: WorkItemStatus;
      source?: string;
      owner?: string;
    }
  | {
      op: "remove_item";
      id: string;
    }
  | {
      op: "reorder_items";
      ordered_ids: string[];
    }
  | {
      op: "replace_all";
      items: Array<Record<string, unknown>>;
    };

export type WorkboardUpdatePayload = {
  items?: Array<Record<string, unknown>>;
  ops?: WorkboardOperation[];
  archive_completed?: boolean;
  status?: WorkboardStatus;
};

export type ElicitationOption = {
  label: string;
  description?: string | null;
};

export type ElicitationQuestion = {
  id: string;
  header: string;
  question: string;
  options: ElicitationOption[];
  multi_select: boolean;
  allow_other: boolean;
  allow_notes: boolean;
};

export type ElicitationAnswer = {
  question_id: string;
  selected_options: string[];
  other_text?: string | null;
  notes?: string | null;
  rendered_answer: string;
};

export type ElicitationRequest = {
  id: string;
  kind: "clarification" | "confirmation" | "decision" | "missing_info" | "approval";
  title: string;
  blocking: boolean;
  source_agent?: string | null;
  related_work_items: string[];
  questions: ElicitationQuestion[];
  preview_text?: string | null;
  status: "pending" | "resolved" | "cancelled" | "expired";
  created_at: string;
  updated_at: string;
  resolved_at?: string | null;
  cancelled_at?: string | null;
  answers: ElicitationAnswer[];
};

export type ElicitationState = {
  session_id: string;
  revision: number;
  pending: ElicitationRequest | null;
  history: ElicitationRequest[];
  updated_at: string;
};

export type ElicitationResponseItem = {
  question_id: string;
  selected_options: string[];
  other_text?: string | null;
  notes?: string | null;
};

const API_BASE = "/api/v1";
const ACCESS_TOKEN_KEY = "aethercore_access_token";

let accessToken = "";

export function loadStoredAccessToken() {
  accessToken = window.localStorage.getItem(ACCESS_TOKEN_KEY) ?? "";
  return accessToken;
}

export function setAccessToken(token: string, persist = true) {
  accessToken = token;
  if (persist) {
    window.localStorage.setItem(ACCESS_TOKEN_KEY, token);
  }
}

export function clearAccessToken() {
  accessToken = "";
  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
}

export function getAccessToken() {
  return accessToken || loadStoredAccessToken();
}

async function apiFetch(input: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers ?? {});
  const token = getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE}${input}`, {
    ...init,
    headers,
  });

  return response;
}

async function readErrorMessage(response: Response, fallback: string) {
  try {
    const payload = await response.json();
    if (payload && typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail;
    }
  } catch {
    // Ignore JSON parsing failures and fall back to the generic message.
  }
  return fallback;
}

export async function loginWithPassword(payload: PasswordLoginPayload) {
  const response = await fetch(`${API_BASE}/auth/login/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(await readErrorMessage(response, `账号登录失败: ${response.status}`));
  }

  return response.json();
}

export async function loginWithOAuthCallback(providerKey: string, code: string, redirectUri: string) {
  const response = await fetch(`${API_BASE}/auth/login/oauth/${encodeURIComponent(providerKey)}/callback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      code,
      redirect_uri: redirectUri,
    }),
  });

  if (!response.ok) {
    throw new Error(await readErrorMessage(response, `OAuth 登录失败: ${response.status}`));
  }

  return response.json();
}

export async function listOAuthProviders() {
  const response = await fetch(`${API_BASE}/auth/oauth/providers`);
  if (!response.ok) {
    throw new Error(`Failed to load OAuth providers: ${response.status}`);
  }
  return response.json();
}

export async function getCurrentUser() {
  const response = await apiFetch("/auth/me");
  if (!response.ok) {
    throw new Error(`获取当前用户失败: ${response.status}`);
  }
  return (await response.json()) as CurrentUserProfile;
}

export async function listPlatforms() {
  const response = await apiFetch("/platforms");
  if (!response.ok) {
    throw new Error(`获取平台列表失败: ${response.status}`);
  }
  return response.json();
}

export async function createPlatform(payload: PlatformCreatePayload) {
  const response = await apiFetch("/platforms", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`平台注册失败: ${response.status}`);
  }
  return response.json();
}

export async function getPlatformIntegrationGuide(platformId: number) {
  const response = await apiFetch(`/platforms/${platformId}/integration-guide`);
  if (!response.ok) {
    throw new Error(`获取平台接入教程失败: ${response.status}`);
  }
  return response.json();
}

export async function getPlatformRuntimeImage(platformId: number) {
  const response = await apiFetch(`/platform-runtime-images/platform/${platformId}`);
  if (!response.ok) {
    throw new Error(`获取平台运行镜像失败: ${response.status}`);
  }
  return response.json();
}

export async function getPlatformRuntimeImageGuide(platformId: number) {
  const response = await apiFetch(`/platform-runtime-images/platform/${platformId}/guide`);
  if (!response.ok) {
    throw new Error(`获取平台运行镜像构建规范失败: ${response.status}`);
  }
  return response.json();
}

export async function updatePlatformRuntimeImage(platformId: number, payload: PlatformRuntimeImagePayload) {
  const response = await apiFetch(`/platform-runtime-images/platform/${platformId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`更新平台运行镜像失败: ${response.status}`);
  }
  return response.json();
}

export async function uploadPlatformRuntimeImage(platformId: number, file: File) {
  const formData = new FormData();
  formData.append("image_file", file);
  const response = await apiFetch(`/platform-runtime-images/platform/${platformId}/upload`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(`上传平台运行镜像失败: ${response.status}`);
  }
  return response.json();
}

export async function deletePlatformRuntimeImage(platformId: number) {
  const response = await apiFetch(`/platform-runtime-images/platform/${platformId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`清除平台运行镜像失败: ${response.status}`);
  }
  return response.json();
}

export async function getPlatformSandboxProxyConfig(platformId: number) {
  const response = await apiFetch(`/platform-sandbox-proxy/platform/${platformId}`);
  if (!response.ok) {
    throw new Error(`获取平台 sandbox 代理配置失败: ${response.status}`);
  }
  return response.json();
}

export async function updatePlatformSandboxProxyConfig(platformId: number, payload: PlatformSandboxProxyPayload) {
  const response = await apiFetch(`/platform-sandbox-proxy/platform/${platformId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`更新平台 sandbox 代理配置失败: ${response.status}`);
  }
  return response.json();
}

export async function deletePlatformSandboxProxyConfig(platformId: number) {
  const response = await apiFetch(`/platform-sandbox-proxy/platform/${platformId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`删除平台 sandbox 代理配置失败: ${response.status}`);
  }
  return response.json();
}

export async function getUserLlmConfig() {
  const response = await apiFetch("/llm/user");
  if (!response.ok) {
    throw new Error(`获取用户 LLM 配置失败: ${response.status}`);
  }
  return response.json();
}

export async function updateUserLlmConfig(payload: LlmConfigPayload) {
  const response = await apiFetch("/llm/user", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`更新用户 LLM 配置失败: ${response.status}`);
  }
  return response.json();
}

export async function deleteUserLlmConfig() {
  const response = await apiFetch("/llm/user", { method: "DELETE" });
  if (!response.ok) {
    throw new Error(`删除用户 LLM 配置失败: ${response.status}`);
  }
  return response.json();
}

export async function getPlatformLlmConfig(platformId: number) {
  const response = await apiFetch(`/llm/platform/${platformId}`);
  if (!response.ok) {
    throw new Error(`获取平台 LLM 配置失败: ${response.status}`);
  }
  return response.json();
}

export async function updatePlatformLlmConfig(platformId: number, payload: LlmConfigPayload) {
  const response = await apiFetch(`/llm/platform/${platformId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`更新平台 LLM 配置失败: ${response.status}`);
  }
  return response.json();
}

export async function deletePlatformLlmConfig(platformId: number) {
  const response = await apiFetch(`/llm/platform/${platformId}`, { method: "DELETE" });
  if (!response.ok) {
    throw new Error(`删除平台 LLM 配置失败: ${response.status}`);
  }
  return response.json();
}

export async function getPlatformPromptConfig(platformId: number) {
  const response = await apiFetch(`/prompts/platform/${platformId}`);
  if (!response.ok) {
    throw new Error(`获取平台提示词配置失败: ${response.status}`);
  }
  return response.json();
}

export async function updatePlatformPromptConfig(platformId: number, payload: PromptConfigPayload) {
  const response = await apiFetch(`/prompts/platform/${platformId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`更新平台提示词配置失败: ${response.status}`);
  }
  return response.json();
}

export async function deletePlatformPromptConfig(platformId: number) {
  const response = await apiFetch(`/prompts/platform/${platformId}`, { method: "DELETE" });
  if (!response.ok) {
    throw new Error(`删除平台提示词配置失败: ${response.status}`);
  }
  return response.json();
}

export async function getPlatformBaseline(platformId: number) {
  const response = await apiFetch(`/platforms/${platformId}/baseline`);
  if (!response.ok) {
    throw new Error(`获取平台基线环境失败: ${response.status}`);
  }
  return response.json();
}

export async function uploadPlatformBaselineFile(
  platformId: number,
  targetRelativeDir: string,
  file: File,
) {
  const formData = new FormData();
  formData.append("upload_file", file);
  const response = await apiFetch(
    `/platforms/${platformId}/baseline/files?target_relative_dir=${encodeURIComponent(targetRelativeDir)}`,
    {
      method: "POST",
      body: formData,
    },
  );
  if (!response.ok) {
    throw new Error(`上传平台基线文件失败: ${response.status}`);
  }
  return response.json();
}

export async function getPlatformBaselineFileContent(platformId: number, relativePath: string) {
  const response = await apiFetch(
    `/platforms/${platformId}/baseline/files/content?relative_path=${encodeURIComponent(relativePath)}`,
  );
  if (!response.ok) {
    throw new Error(`获取平台基线文件内容失败: ${response.status}`);
  }
  return response.json();
}

export async function savePlatformBaselineTextFile(
  platformId: number,
  relativePath: string,
  content: string,
) {
  const response = await apiFetch(`/platforms/${platformId}/baseline/files/text`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      relative_path: relativePath,
      content,
    }),
  });
  if (!response.ok) {
    throw new Error(`保存平台基线文件失败: ${response.status}`);
  }
  return response.json();
}

export async function createPlatformBaselineDirectory(platformId: number, relativePath: string) {
  const response = await apiFetch(`/platforms/${platformId}/baseline/directories`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      relative_path: relativePath,
    }),
  });
  if (!response.ok) {
    throw new Error(`创建平台基线目录失败: ${response.status}`);
  }
  return response.json();
}

export async function movePlatformBaselinePath(
  platformId: number,
  sourceRelativePath: string,
  targetRelativePath: string,
) {
  const response = await apiFetch(`/platforms/${platformId}/baseline/paths`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source_relative_path: sourceRelativePath,
      target_relative_path: targetRelativePath,
    }),
  });
  if (!response.ok) {
    throw new Error(`重命名平台基线路径失败: ${response.status}`);
  }
  return response.json();
}

export async function deletePlatformBaselineFile(platformId: number, relativePath: string) {
  const response = await apiFetch(
    `/platforms/${platformId}/baseline/files?relative_path=${encodeURIComponent(relativePath)}`,
    {
      method: "DELETE",
    },
  );
  if (!response.ok) {
    throw new Error(`删除平台基线文件失败: ${response.status}`);
  }
  return response.json();
}

export async function downloadPlatformBaselineFile(platformId: number, relativePath: string) {
  const response = await apiFetch(
    `/platforms/${platformId}/baseline/files/download?relative_path=${encodeURIComponent(relativePath)}`,
  );
  if (!response.ok) {
    throw new Error(`下载平台基线文件失败: ${response.status}`);
  }
  return response.blob();
}

export async function bootstrapAdminSession(sessionId?: string) {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  const response = await apiFetch(`/agent/sessions/bootstrap${query}`, {
    method: "POST",
  });

  if (!response.ok) {
    throw new Error(`初始化工作台失败: ${response.status}`);
  }

  return response.json();
}

export async function listConversations() {
  const response = await apiFetch("/agent/sessions");
  if (!response.ok) {
    throw new Error(`获取历史会话失败: ${response.status}`);
  }
  return response.json();
}

export async function getSessionSummary(sessionId: string) {
  const response = await apiFetch(`/agent/sessions/${encodeURIComponent(sessionId)}`);
  if (!response.ok) {
    throw new Error(`获取会话摘要失败: ${response.status}`);
  }
  return response.json();
}

export async function streamRunEvents(
  sessionId: string,
  runId: string,
  onEvent: (event: Record<string, unknown>) => void,
  abortSignal?: AbortSignal,
) {
  const headers = new Headers();
  const token = getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  let streamOpened = false;

  try {
    const response = await fetch(`${API_BASE}/agent/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/events`, {
      method: "GET",
      headers,
      signal: abortSignal,
    });
    if (!response.ok || !response.body) {
      throw new Error(`恢复运行订阅失败: ${response.status}`);
    }

    streamOpened = true;
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        buffer += decoder.decode();
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      let match = buffer.match(/([\s\S]*?)(\r?\n){2}/);
      while (match) {
        const chunk = match[1];
        buffer = buffer.slice(match[0].length);
        const line = chunk.split(/\r?\n/).find((item) => item.startsWith("data:"));
        if (line) {
          onEvent(JSON.parse(line.slice(5).trim()));
        }
        match = buffer.match(/([\s\S]*?)(\r?\n){2}/);
      }
    }
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw error;
    }
    const detail = error instanceof Error && error.message ? error.message : "unknown error";
    if (streamOpened) {
      throw new Error(`运行订阅已中断: ${detail}`);
    }
    throw new Error(`恢复运行订阅失败: ${detail}`);
  }
}

export async function uploadPlatformBaselineSkill(platformId: number, skillFile: File) {
  const formData = new FormData();
  formData.append("skill_file", skillFile);
  const response = await apiFetch(`/platforms/${platformId}/baseline/skills`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(`上传平台技能失败: ${response.status}`);
  }
  return response.json();
}

export async function listPlatformAdmins(platformId: number) {
  const response = await apiFetch(`/platforms/${platformId}/admins`);
  if (!response.ok) {
    throw new Error(`获取平台负责人失败: ${response.status}`);
  }
  return response.json();
}

export async function assignPlatformAdmin(platformId: number, userId: number) {
  const response = await apiFetch(`/platforms/${platformId}/admins`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
  if (!response.ok) {
    throw new Error(`更新平台负责人失败: ${response.status}`);
  }
  return response.json();
}

export async function removePlatformAdmin(platformId: number, userId: number) {
  const response = await apiFetch(`/platforms/${platformId}/admins/${userId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`移除平台负责人失败: ${response.status}`);
  }
  return response.json();
}

export async function updatePlatformOwner(platformId: number, userId: number) {
  const response = await apiFetch(`/platforms/${platformId}/owner`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
  if (!response.ok) {
    throw new Error(`更新平台主负责人失败: ${response.status}`);
  }
  return response.json();
}

export async function createPlatformRegistrationRequest(payload: PlatformRegistrationRequestPayload) {
  const response = await apiFetch("/platforms/registration-requests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`提交平台注册申请失败: ${response.status}`);
  }
  return response.json();
}

export async function listMyPlatformRegistrationRequests() {
  const response = await apiFetch("/platforms/registration-requests/mine");
  if (!response.ok) {
    throw new Error(`获取我的平台注册申请失败: ${response.status}`);
  }
  return response.json();
}

export async function listPlatformRegistrationRequests() {
  const response = await apiFetch("/platforms/registration-requests");
  if (!response.ok) {
    throw new Error(`获取平台注册申请列表失败: ${response.status}`);
  }
  return response.json();
}

export async function approvePlatformRegistrationRequest(
  requestId: number,
  payload: PlatformRegistrationReviewPayload,
) {
  const response = await apiFetch(`/platforms/registration-requests/${requestId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`审批平台注册申请失败: ${response.status}`);
  }
  return response.json();
}

export async function rejectPlatformRegistrationRequest(
  requestId: number,
  payload: PlatformRegistrationReviewPayload,
) {
  const response = await apiFetch(`/platforms/registration-requests/${requestId}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`驳回平台注册申请失败: ${response.status}`);
  }
  return response.json();
}

export async function listUsers() {
  const response = await apiFetch("/auth/users");
  if (!response.ok) {
    throw new Error(`获取用户列表失败: ${response.status}`);
  }
  return response.json();
}

export async function updateUserRole(userId: number, payload: UserRoleUpdatePayload) {
  const response = await apiFetch(`/auth/users/${userId}/role`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`更新用户角色失败: ${response.status}`);
  }
  return response.json();
}

export async function listAdminRuntimes() {
  const response = await apiFetch("/admin/runtimes");
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, `获取 runtime 列表失败: ${response.status}`));
  }
  return response.json();
}

export async function listAdminRuntimesHistory() {
  const response = await apiFetch("/admin/runtimes?include_inactive=true");
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, `获取 runtime 历史失败: ${response.status}`));
  }
  return response.json();
}

export async function collectAdminRuntime(sessionId: string) {
  const response = await apiFetch(`/admin/runtimes/${encodeURIComponent(sessionId)}/collect`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, `回收 runtime 失败: ${response.status}`));
  }
  return response.json();
}

export async function getSystemNetworkSnapshot() {
  const response = await apiFetch("/admin/ips");
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, `获取服务器 IP 信息失败: ${response.status}`));
  }
  return response.json();
}

export async function addRouteFor80Network(gatewayIp?: string) {
  const query = gatewayIp ? `?gateway_ip=${encodeURIComponent(gatewayIp)}` : "";
  const response = await apiFetch(`/admin/ips/routes/80${query}`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, `执行 80 网段路由失败: ${response.status}`));
  }
  return response.json();
}

export async function listAdminConversations(platformId?: number) {
  const query = platformId ? `?platform_id=${platformId}` : "";
  const response = await apiFetch(`/admin/conversations${query}`);
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, `获取审计会话失败: ${response.status}`));
  }
  return response.json();
}

export async function getAdminConversationDetail(sessionId: string) {
  const response = await apiFetch(`/admin/conversations/${encodeURIComponent(sessionId)}`);
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, `获取审计会话详情失败: ${response.status}`));
  }
  return response.json();
}

export async function getSystemAuditOverview() {
  const response = await apiFetch("/admin/audit/overview");
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, `获取系统审计概览失败: ${response.status}`));
  }
  return response.json();
}

export async function getSessionWorkboard(sessionId: string) {
  const response = await apiFetch(`/agent/sessions/${encodeURIComponent(sessionId)}/workboard`);
  if (!response.ok) {
    throw new Error(`获取任务清单失败: ${response.status}`);
  }
  return response.json();
}

export async function updateSessionWorkboard(sessionId: string, payload: WorkboardUpdatePayload) {
  const response = await apiFetch(`/agent/sessions/${encodeURIComponent(sessionId)}/workboard`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`更新任务清单失败: ${response.status}`);
  }
  return response.json();
}

export async function deleteSession(sessionId: string) {
  const response = await apiFetch(`/agent/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`删除会话失败: ${response.status}`);
  }
  return response.json();
}

export async function renameSession(sessionId: string, title: string) {
  const response = await apiFetch(
    `/agent/sessions/${encodeURIComponent(sessionId)}/title?title=${encodeURIComponent(title)}`,
    { method: "PATCH" },
  );
  if (!response.ok) {
    throw new Error(`重命名会话失败: ${response.status}`);
  }
  return response.json();
}

export async function listSkills(sessionId: string) {
  const response = await apiFetch(`/agent/skills?session_id=${encodeURIComponent(sessionId)}`);
  if (!response.ok) {
    throw new Error(`获取技能列表失败: ${response.status}`);
  }
  return response.json();
}

export async function uploadSkill(sessionId: string, skillFile: File) {
  const formData = new FormData();
  formData.append("skill_file", skillFile);

  const response = await apiFetch(`/agent/skills/upload?session_id=${encodeURIComponent(sessionId)}`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    throw new Error(`上传技能失败: ${response.status}`);
  }

  return response.json();
}

export async function listFiles(sessionId: string) {
  const response = await apiFetch(`/agent/files?session_id=${encodeURIComponent(sessionId)}`);
  if (!response.ok) {
    throw new Error(`获取文件列表失败: ${response.status}`);
  }
  return response.json();
}

export async function readFileContent(sessionId: string, fileId: string) {
  const response = await apiFetch(`/agent/files/${fileId}/content?session_id=${encodeURIComponent(sessionId)}`);
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, `读取文件失败: ${response.status}`));
  }
  return response.json();
}

export async function updateFileContent(sessionId: string, fileId: string, content: string) {
  const response = await apiFetch(`/agent/files/${fileId}/content?session_id=${encodeURIComponent(sessionId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, `保存文件失败: ${response.status}`));
  }
  return response.json();
}

export async function uploadFile(sessionId: string, file: File) {
  const formData = new FormData();
  formData.append("upload_file", file);

  const response = await apiFetch(`/agent/files/upload?session_id=${encodeURIComponent(sessionId)}`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    throw new Error(`上传文件失败: ${response.status}`);
  }

  return response.json();
}

export function getDownloadUrl(sessionId: string, fileId: string) {
  const token = encodeURIComponent(getAccessToken());
  return `${API_BASE}/agent/files/${fileId}/download?session_id=${encodeURIComponent(sessionId)}&access_token=${token}`;
}

export async function abortSession(sessionId: string) {
  const response = await apiFetch(`/agent/${encodeURIComponent(sessionId)}/abort`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(`中断会话失败: ${response.status}`);
  }
  return response.json();
}

export async function forkSessionTimeline(sessionId: string, messageId: string) {
  const response = await apiFetch(`/agent/${encodeURIComponent(sessionId)}/timeline/fork`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: messageId }),
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, `分叉会话失败: ${response.status}`));
  }
  return (await response.json()) as TimelineForkResponse;
}

export async function rerunSessionTimeline(sessionId: string, messageId: string) {
  const response = await apiFetch(`/agent/${encodeURIComponent(sessionId)}/timeline/rerun`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: messageId }),
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, `重新生成失败: ${response.status}`));
  }
  return (await response.json()) as TimelineRerunResponse;
}

export async function editSessionTimeline(sessionId: string, messageId: string, content: string) {
  const response = await apiFetch(`/agent/${encodeURIComponent(sessionId)}/timeline/edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: messageId, content }),
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, `编辑并重生成失败: ${response.status}`));
  }
  return (await response.json()) as TimelineRerunResponse;
}

export async function streamChat(
  sessionId: string,
  message: string,
  allowNetwork: boolean,
  onEvent: (event: Record<string, unknown>) => void,
  abortSignal?: AbortSignal,
  options?: { replaceLastUserMessage?: boolean; clientMessageId?: string },
) {
  return streamSse(
    "/agent/chat",
    {
      session_id: sessionId,
      message,
      allow_network: allowNetwork,
      replace_last_user_message: Boolean(options?.replaceLastUserMessage),
      client_message_id: options?.clientMessageId ?? null,
    },
    onEvent,
    abortSignal,
  );
}

export async function streamElicitationResponse(
  sessionId: string,
  requestId: string,
  responses: ElicitationResponseItem[],
  onEvent: (event: Record<string, unknown>) => void,
  abortSignal?: AbortSignal,
  options?: { clientMessageId?: string },
) {
  return streamSse(
    `/agent/${encodeURIComponent(sessionId)}/elicitation/${encodeURIComponent(requestId)}/respond`,
    { responses, client_message_id: options?.clientMessageId ?? null },
    onEvent,
    abortSignal,
  );
}

async function streamSse(
  path: string,
  payload: Record<string, unknown>,
  onEvent: (event: Record<string, unknown>) => void,
  abortSignal?: AbortSignal,
) {
  const headers = new Headers({ "Content-Type": "application/json" });
  const token = getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  let streamOpened = false;

  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      signal: abortSignal,
    });

    if (!response.ok || !response.body) {
      throw new Error(`对话请求失败: ${response.status}`);
    }

    streamOpened = true;
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        buffer += decoder.decode();
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      let match = buffer.match(/([\s\S]*?)(\r?\n){2}/);
      while (match) {
        const chunk = match[1];
        buffer = buffer.slice(match[0].length);
        const line = chunk.split(/\r?\n/).find((item) => item.startsWith("data:"));
        if (line) {
          onEvent(JSON.parse(line.slice(5).trim()));
        }
        match = buffer.match(/([\s\S]*?)(\r?\n){2}/);
      }
    }
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw error;
    }

    const detail = error instanceof Error && error.message ? error.message : "unknown error";
    if (streamOpened) {
      throw new Error(`对话流已中断，通常是代理超时、连接被重置，或长时间没有新事件: ${detail}`);
    }
    throw new Error(`对话请求失败: ${detail}`);
  }
}
