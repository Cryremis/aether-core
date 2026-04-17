// frontend/src/api/client.ts
export type PasswordLoginPayload = {
  username: string;
  password: string;
};

export type AdminWhitelistPayload = {
  provider: "w3" | "password";
  provider_user_id: string;
  full_name?: string;
  email?: string;
  role: "system_admin" | "platform_admin" | "debug";
};

export type PlatformCreatePayload = {
  platform_key: string;
  display_name: string;
  host_type: "embedded" | "standalone";
  description: string;
  owner_user_id?: number;
};

export type PlatformBaselineFile = {
  name: string;
  relative_path: string;
  section: "input" | "work";
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
  skills: PlatformBaselineSkill[];
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

export async function loginWithPassword(payload: PasswordLoginPayload) {
  const response = await fetch(`${API_BASE}/auth/login/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`账号登录失败: ${response.status}`);
  }

  return response.json();
}

export async function loginWithW3Callback(code: string, redirectUri: string) {
  const response = await fetch(`${API_BASE}/auth/login/w3/callback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      code,
      redirect_uri: redirectUri,
    }),
  });

  if (!response.ok) {
    throw new Error(`W3 登录失败: ${response.status}`);
  }

  return response.json();
}

export async function getW3Config() {
  const response = await fetch(`${API_BASE}/auth/w3/config`);
  if (!response.ok) {
    throw new Error(`获取 W3 配置失败: ${response.status}`);
  }
  return response.json();
}

export async function getCurrentUser() {
  const response = await apiFetch("/auth/me");
  if (!response.ok) {
    throw new Error(`获取当前用户失败: ${response.status}`);
  }
  return response.json();
}

export async function listAdminWhitelist() {
  const response = await apiFetch("/auth/admin-whitelist");
  if (!response.ok) {
    throw new Error(`获取管理员白名单失败: ${response.status}`);
  }
  return response.json();
}

export async function createAdminWhitelist(payload: AdminWhitelistPayload) {
  const response = await apiFetch("/auth/admin-whitelist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`新增管理员白名单失败: ${response.status}`);
  }
  return response.json();
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

export async function getPlatformBaseline(platformId: number) {
  const response = await apiFetch(`/platforms/${platformId}/baseline`);
  if (!response.ok) {
    throw new Error(`获取平台基线环境失败: ${response.status}`);
  }
  return response.json();
}

export async function uploadPlatformBaselineFile(
  platformId: number,
  section: "input" | "work",
  file: File,
) {
  const formData = new FormData();
  formData.append("upload_file", file);
  const response = await apiFetch(
    `/platforms/${platformId}/baseline/files?section=${encodeURIComponent(section)}`,
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

export async function uploadPlatformBaselineSkill(platformId: number, file: File) {
  const formData = new FormData();
  formData.append("skill_file", file);
  const response = await apiFetch(`/platforms/${platformId}/baseline/skills`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(`上传平台基线技能失败: ${response.status}`);
  }
  return response.json();
}

export async function deletePlatformBaselineSkill(platformId: number, skillName: string) {
  const response = await apiFetch(`/platforms/${platformId}/baseline/skills/${encodeURIComponent(skillName)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`删除平台基线技能失败: ${response.status}`);
  }
  return response.json();
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

export async function streamChat(
  sessionId: string,
  message: string,
  onEvent: (event: Record<string, unknown>) => void,
) {
  const headers = new Headers({ "Content-Type": "application/json" });
  const token = getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE}/agent/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      session_id: sessionId,
      message,
    }),
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
