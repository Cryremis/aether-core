// frontend/src/pages/WorkbenchPage.tsx
import { useEffect, useMemo, useState } from "react";

import {
  bindHost,
  getDownloadUrl,
  getSessionSummary,
  listFiles,
  listSkills,
  streamChat,
  uploadFile,
  uploadSkill,
} from "../api/client";
import { PanelCard } from "../components/PanelCard";

type TimelineEvent = {
  id: string;
  type: string;
  text: string;
};

type FileItem = {
  file_id: string;
  name: string;
  category: string;
  size: number;
};

type SkillItem = {
  name: string;
  description: string;
  source: string;
};

export function WorkbenchPage() {
  const [sessionId, setSessionId] = useState("");
  const [input, setInput] = useState("");
  const [answer, setAnswer] = useState("");
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [files, setFiles] = useState<FileItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [skillName, setSkillName] = useState("");
  const [skillDescription, setSkillDescription] = useState("");
  const [skillContent, setSkillContent] = useState("");

  const refreshSession = async (nextSessionId: string) => {
    const [skillResult, fileResult, summaryResult] = await Promise.all([
      listSkills(nextSessionId),
      listFiles(nextSessionId),
      getSessionSummary(nextSessionId),
    ]);
    setSkills((skillResult.data ?? []) as SkillItem[]);
    setFiles((fileResult.items ?? []) as FileItem[]);
    const summary = (summaryResult.data ?? {}) as Record<string, unknown>;
    if (typeof summary.host_name === "string" && summary.host_name) {
      setTimeline((current) => {
        if (current.some((item) => item.id === "session-summary")) return current;
        return [
          {
            id: "session-summary",
            type: "session",
            text: `当前宿主: ${summary.host_name}，已累计消息 ${String(summary.message_count ?? 0)} 条。`,
          },
          ...current,
        ];
      });
    }
  };

  useEffect(() => {
    const boot = async () => {
      try {
        const result = await bindHost({
          host_name: "standalone-workbench",
          host_type: "custom",
          context: {
            user: { display_name: "本地工作台用户" },
            page: { name: "workbench" },
            extras: {},
          },
        });
        const nextSessionId = result.data.session_id as string;
        setSessionId(nextSessionId);
        await refreshSession(nextSessionId);
      } catch (bootError) {
        setError(bootError instanceof Error ? bootError.message : "初始化失败");
      }
    };
    void boot();
  }, []);

  const canSend = useMemo(() => input.trim().length > 0 && !!sessionId && !busy, [busy, input, sessionId]);
  const canUploadSkill = useMemo(
    () => !!sessionId && !!skillName.trim() && !!skillDescription.trim() && !busy,
    [busy, sessionId, skillDescription, skillName],
  );

  const handleSend = async () => {
    if (!canSend) return;
    setBusy(true);
    setError("");
    setAnswer("");
    setTimeline([]);

    try {
      await streamChat(sessionId, input.trim(), (event) => {
        const payload = (event.payload ?? {}) as Record<string, unknown>;
        const text =
          typeof payload.delta === "string"
            ? payload.delta
            : typeof payload.summary === "string"
              ? payload.summary
              : JSON.stringify(payload);

        setTimeline((current) => [
          ...current,
          {
            id: `${String(event.type)}-${current.length + 1}`,
            type: String(event.type),
            text,
          },
        ]);

        if (event.type === "content_delta") {
          setAnswer((current) => current + String(payload.delta ?? ""));
        }
        if (event.type === "message" && typeof payload.summary === "string") {
          setAnswer(payload.summary);
        }
        if (event.type === "artifact_created" || event.type === "completed") {
          void refreshSession(sessionId);
        }
        if (event.type === "error") {
          setError(typeof payload.message === "string" ? payload.message : "执行失败");
        }
      });
    } catch (chatError) {
      setError(chatError instanceof Error ? chatError.message : "对话执行失败");
    } finally {
      setBusy(false);
    }
  };

  const handleUpload = async (file: File | undefined) => {
    if (!file || !sessionId) return;
    try {
      setError("");
      await uploadFile(sessionId, file);
      await refreshSession(sessionId);
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "文件上传失败");
    }
  };

  const handleUploadSkill = async () => {
    if (!canUploadSkill) return;
    try {
      setError("");
      await uploadSkill(sessionId, {
        name: skillName.trim(),
        description: skillDescription.trim(),
        content: skillContent.trim(),
      });
      setSkillName("");
      setSkillDescription("");
      setSkillContent("");
      await refreshSession(sessionId);
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "技能上传失败");
    }
  };

  return (
    <main className="workbench-page">
      <section className="hero">
        <div>
          <span className="hero__badge">AetherCore</span>
          <h1>独立 Agent 工作台</h1>
          <p>
            当前界面面向独立 Agent Runtime 平台，Dash 这类业务平台通过宿主注入协议接入，文件、技能、脚本执行全部在受限沙箱中完成。
          </p>
        </div>
        <div className="hero__meta">
          <span>会话 ID</span>
          <strong>{sessionId || "初始化中"}</strong>
          <span className="hero__status">{busy ? "正在执行" : "空闲"}</span>
        </div>
      </section>

      {error ? <section className="error-banner">{error}</section> : null}

      <section className="grid">
        <PanelCard
          title="对话"
          subtitle="统一工作台模式，展示最终回答与任务输入入口。"
          action={<button disabled={!canSend} onClick={handleSend}>{busy ? "处理中" : "发送"}</button>}
        >
          <textarea
            className="workbench-input"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="输入任务，例如：请读取上传的 Excel，生成汇总说明，并把结果写成可下载文件。"
          />
          <div className="answer-box">{answer || "等待执行..."}</div>
        </PanelCard>

        <PanelCard title="时间线" subtitle="展示推理、工具调用、产物生成与错误事件。">
          <div className="timeline-list">
            {timeline.length === 0 ? <span className="empty-text">暂无事件</span> : null}
            {timeline.map((item) => (
              <article key={item.id} className="timeline-item">
                <strong>{item.type}</strong>
                <p>{item.text}</p>
              </article>
            ))}
          </div>
        </PanelCard>

        <PanelCard title="文件" subtitle="统一展示上传文件与输出产物，并提供下载入口。">
          <label className="upload-button">
            <span>上传文件</span>
            <input
              type="file"
              onChange={(event) => {
                const file = event.target.files?.[0];
                void handleUpload(file);
                event.currentTarget.value = "";
              }}
            />
          </label>
          <div className="list-panel">
            {files.length === 0 ? <span className="empty-text">暂无文件</span> : null}
            {files.map((item) => (
              <article key={item.file_id} className="list-item">
                <strong>{item.name}</strong>
                <p>{item.category} · {item.size} bytes</p>
                <a className="download-link" href={getDownloadUrl(sessionId, item.file_id)} target="_blank" rel="noreferrer">
                  下载
                </a>
              </article>
            ))}
          </div>
        </PanelCard>

        <PanelCard title="技能" subtitle="内置技能、宿主注入技能与用户上传技能统一管理。">
          <div className="skill-form">
            <input value={skillName} onChange={(event) => setSkillName(event.target.value)} placeholder="技能名称" />
            <input value={skillDescription} onChange={(event) => setSkillDescription(event.target.value)} placeholder="技能描述" />
            <textarea
              className="workbench-input skill-textarea"
              value={skillContent}
              onChange={(event) => setSkillContent(event.target.value)}
              placeholder="输入 SKILL.md 主体内容，例如该技能如何读取文件、何时调用脚本。"
            />
            <button disabled={!canUploadSkill} onClick={handleUploadSkill}>上传技能</button>
          </div>
          <div className="list-panel">
            {skills.length === 0 ? <span className="empty-text">暂无技能</span> : null}
            {skills.map((item, index) => (
              <article key={`${item.name}-${index}`} className="list-item">
                <strong>{item.name}</strong>
                <p>{item.description}</p>
                <span className="skill-source">{item.source}</span>
              </article>
            ))}
          </div>
        </PanelCard>
      </section>
    </main>
  );
}
