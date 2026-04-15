// frontend/src/api/client.ts
export type HostBindPayload = {
  host_name: string;
  host_type: "dash" | "poc" | "custom";
  session_id?: string;
  context?: Record<string, unknown>;
  tools?: Array<Record<string, unknown>>;
  skills?: Array<Record<string, unknown>>;
  apis?: Array<Record<string, unknown>>;
};

const API_BASE = "/api/v1";

export async function bindHost(payload: HostBindPayload) {
  const response = await fetch(`${API_BASE}/host/bind`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`宿主绑定失败: ${response.status}`);
  }
  return response.json();
}

export async function listSkills(sessionId: string) {
  const response = await fetch(`${API_BASE}/agent/skills?session_id=${encodeURIComponent(sessionId)}`);
  if (!response.ok) {
    throw new Error(`技能列表获取失败: ${response.status}`);
  }
  return response.json();
}

export async function uploadSkill(
  sessionId: string,
  payload: { name: string; description: string; content?: string; skillFile?: File },
) {
  const formData = new FormData();
  formData.append("name", payload.name);
  formData.append("description", payload.description);
  if (payload.content) {
    formData.append("content", payload.content);
  }
  if (payload.skillFile) {
    formData.append("skill_file", payload.skillFile);
  }
  const response = await fetch(`${API_BASE}/agent/skills/upload?session_id=${encodeURIComponent(sessionId)}`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(`技能上传失败: ${response.status}`);
  }
  return response.json();
}

export async function listFiles(sessionId: string) {
  const response = await fetch(`${API_BASE}/agent/files?session_id=${encodeURIComponent(sessionId)}`);
  if (!response.ok) {
    throw new Error(`文件列表获取失败: ${response.status}`);
  }
  return response.json();
}

export async function uploadFile(sessionId: string, file: File) {
  const formData = new FormData();
  formData.append("upload_file", file);
  const response = await fetch(`${API_BASE}/agent/files/upload?session_id=${encodeURIComponent(sessionId)}`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(`文件上传失败: ${response.status}`);
  }
  return response.json();
}

export async function getSessionSummary(sessionId: string) {
  const response = await fetch(`${API_BASE}/agent/sessions/${encodeURIComponent(sessionId)}`);
  if (!response.ok) {
    throw new Error(`会话摘要获取失败: ${response.status}`);
  }
  return response.json();
}

export function getDownloadUrl(sessionId: string, fileId: string) {
  return `${API_BASE}/agent/files/${encodeURIComponent(fileId)}/download?session_id=${encodeURIComponent(sessionId)}`;
}

export async function streamChat(
  sessionId: string,
  message: string,
  onEvent: (event: Record<string, unknown>) => void,
) {
  const response = await fetch(`${API_BASE}/agent/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
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
      const line = chunk
        .split(/\r?\n/)
        .find((item) => item.startsWith("data:"));
      if (line) {
        onEvent(JSON.parse(line.slice(5).trim()));
      }
      match = buffer.match(/([\s\S]*?)(\r?\n){2}/);
    }
  }
}
