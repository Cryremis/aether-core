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

export type PlatformIntegrationGuide = {
  platform_key: string;
  display_name: string;
  bind_api_path: string;
  frontend_script_path: string;
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

export type PlatformBaselineFile = {
  name: string;
  relative_path: string;
  section: "input" | "skills" | "work" | "output" | "logs";
  size: number;
  media_type: string;
};

export type PlatformBaselineEntry = {
  name: string;
  relative_path: string;
  section: "input" | "skills" | "work" | "output" | "logs";
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
  return `${API_BASE}/agent/files/${encodeURIComponent(fileId)}/download?session_id=${encodeURIComponent(sessionId)}&access_token=${token}`;
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

export async function streamChat(
  sessionId: string,
  message: string,
  allowNetwork: boolean,
  onEvent: (event: Record<string, unknown>) => void,
  abortSignal?: AbortSignal,
) {
  return streamSse(
    "/agent/chat",
    {
      session_id: sessionId,
      message,
      allow_network: allowNetwork,
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
) {
  return streamSse(
    `/agent/${encodeURIComponent(sessionId)}/elicitation/${encodeURIComponent(requestId)}/respond`,
    { responses },
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

  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    signal: abortSignal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`对话请求失败: ${response.status}`);
  }

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
}
