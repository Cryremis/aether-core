// frontend/src/pages/WorkbenchPage.tsx
import { marked } from "marked";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  bindHost,
  getDownloadUrl,
  listFiles,
  listSkills,
  streamChat,
  uploadFile,
  uploadSkill,
} from "../api/client";

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

type AssistantBlock =
  | { id: string; kind: "reasoning"; content: string }
  | { id: string; kind: "content"; content: string; status: "streaming" | "done" }
  | {
      id: string;
      kind: "tool";
      title: string;
      meta: string;
      argumentsText: string;
      outputText: string;
      status: "running" | "done";
    };

type AssistantSegment =
  | { id: string; kind: "bubble"; blocks: Array<Extract<AssistantBlock, { kind: "reasoning" | "content" }>> }
  | { id: string; kind: "tool"; block: Extract<AssistantBlock, { kind: "tool" }> };

type ChatMessage =
  | { id: string; role: "user"; content: string }
  | { id: string; role: "assistant"; blocks: AssistantBlock[] };

type SidebarView = "files" | "skills";

marked.setOptions({
  breaks: true,
  gfm: true,
});

export function WorkbenchPage() {
  const [sessionId, setSessionId] = useState("");
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [files, setFiles] = useState<FileItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [skillName, setSkillName] = useState("");
  const [skillDescription, setSkillDescription] = useState("");
  const [skillContent, setSkillContent] = useState("");
  const [sidebarView, setSidebarView] = useState<SidebarView>("files");
  const historyRef = useRef<HTMLDivElement | null>(null);

  const refreshSession = async (nextSessionId: string) => {
    const [skillResult, fileResult] = await Promise.all([listSkills(nextSessionId), listFiles(nextSessionId)]);
    setSkills((skillResult.data ?? []) as SkillItem[]);
    setFiles((fileResult.items ?? []) as FileItem[]);
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

  useEffect(() => {
    const node = historyRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [messages]);

  const canSend = useMemo(() => input.trim().length > 0 && !!sessionId && !busy, [busy, input, sessionId]);
  const canUploadSkill = useMemo(
    () => !!sessionId && !!skillName.trim() && !!skillDescription.trim() && !busy,
    [busy, sessionId, skillDescription, skillName],
  );

  const appendAssistantBlock = (messageId: string, block: AssistantBlock) => {
    setMessages((current) =>
      current.map((item) =>
        item.id === messageId && item.role === "assistant"
          ? { ...item, blocks: [...item.blocks, block] }
          : item,
      ),
    );
  };

  const updateAssistantBlock = (
    messageId: string,
    blockId: string,
    updater: (block: AssistantBlock) => AssistantBlock,
  ) => {
    setMessages((current) =>
      current.map((item) =>
        item.id === messageId && item.role === "assistant"
          ? { ...item, blocks: item.blocks.map((block) => (block.id === blockId ? updater(block) : block)) }
          : item,
      ),
    );
  };

  const getToolDisplay = (toolName: string, inputValue: Record<string, unknown>) => {
    if (toolName === "sandbox_shell") {
      const rawCommand = String(inputValue.command ?? "").trim();
      const firstToken = rawCommand.split(/\s+/)[0] || "shell";
      const shellName = String(inputValue.shell ?? "powershell");
      return { title: firstToken, meta: shellName };
    }
    return { title: toolName, meta: "tool" };
  };

  const handleSend = async () => {
    if (!canSend) return;
    const userText = input.trim();
    const assistantId = `assistant-${Date.now()}`;

    setBusy(true);
    setError("");
    setInput("");
    setMessages((current) => [
      ...current,
      { id: `user-${Date.now()}`, role: "user", content: userText },
      { id: assistantId, role: "assistant", blocks: [] },
    ]);

    let activeReasoningId = "";
    let activeContentId = "";
    let activeContentText = "";

    try {
      await streamChat(sessionId, userText, (event) => {
        const payload = (event.payload ?? {}) as Record<string, unknown>;

        if (event.type === "reasoning_delta") {
          if (!activeReasoningId) {
            activeReasoningId = `reasoning-${Date.now()}-${Math.random()}`;
            appendAssistantBlock(assistantId, {
              id: activeReasoningId,
              kind: "reasoning",
              content: String(payload.delta ?? ""),
            });
          } else {
            updateAssistantBlock(assistantId, activeReasoningId, (block) =>
              block.kind === "reasoning"
                ? { ...block, content: `${block.content}${String(payload.delta ?? "")}` }
                : block,
            );
          }
          return;
        }

        if (event.type === "content_delta") {
          activeContentText += String(payload.delta ?? "");
          if (!activeContentId) {
            activeContentId = `content-${Date.now()}-${Math.random()}`;
            appendAssistantBlock(assistantId, {
              id: activeContentId,
              kind: "content",
              content: activeContentText,
              status: "streaming",
            });
          } else {
            updateAssistantBlock(assistantId, activeContentId, (block) =>
              block.kind === "content"
                ? { ...block, content: activeContentText, status: "streaming" }
                : block,
            );
          }
          return;
        }

        if (event.type === "content_completed") {
          if (activeContentId && activeContentText) {
            updateAssistantBlock(assistantId, activeContentId, (block) =>
              block.kind === "content"
                ? { ...block, content: activeContentText, status: "done" }
                : block,
            );
          }
          activeReasoningId = "";
          return;
        }

        if (event.type === "tool_started") {
          activeReasoningId = "";
          activeContentId = "";
          activeContentText = "";
          const toolInput = (payload.input ?? {}) as Record<string, unknown>;
          const display = getToolDisplay(String(payload.tool_name ?? "tool"), toolInput);
          appendAssistantBlock(assistantId, {
            id: String(payload.id ?? `tool-${Date.now()}`),
            kind: "tool",
            title: display.title,
            meta: display.meta,
            argumentsText: JSON.stringify(toolInput ?? {}, null, 2),
            outputText: "",
            status: "running",
          });
          return;
        }

        if (event.type === "tool_finished") {
          updateAssistantBlock(assistantId, String(payload.id), (block) =>
            block.kind === "tool"
              ? { ...block, outputText: JSON.stringify(payload.output ?? {}, null, 2), status: "done" }
              : block,
          );
          void refreshSession(sessionId);
          return;
        }

        if (event.type === "message" && typeof payload.summary === "string") {
          activeContentText = payload.summary;
          if (activeContentId) {
            updateAssistantBlock(assistantId, activeContentId, (block) =>
              block.kind === "content"
                ? { ...block, content: payload.summary as string, status: "done" }
                : block,
            );
          } else {
            activeContentId = `content-${Date.now()}-${Math.random()}`;
            appendAssistantBlock(assistantId, {
              id: activeContentId,
              kind: "content",
              content: payload.summary,
              status: "done",
            });
          }
          return;
        }

        if (event.type === "artifact_created" || event.type === "completed") {
          void refreshSession(sessionId);
          return;
        }

        if (event.type === "result") {
          const subtype = String(payload.subtype ?? "");
          if (subtype && subtype !== "success") {
            const errors = Array.isArray(payload.errors) ? payload.errors.map((item) => String(item)) : [];
            if (errors.length > 0) {
              setError(errors[0] ?? "执行失败");
            }
          }
          return;
        }

        if (event.type === "error") {
          const traceText = typeof payload.traceback === "string" ? `\n\n${payload.traceback}` : "";
          appendAssistantBlock(assistantId, {
            id: `tool-error-${Date.now()}`,
            kind: "tool",
            title: "执行错误",
            meta: "error",
            argumentsText: "",
            outputText: `${String(payload.message ?? "执行失败")}${traceText}`,
            status: "done",
          });
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
      setSidebarView("files");
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
      setSidebarView("skills");
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "技能上传失败");
    }
  };

  const renderMarkdown = (value: string) => ({ __html: marked.parse(value) as string });

  const renderAssistantSegments = (blocks: AssistantBlock[]): AssistantSegment[] => {
    const segments: AssistantSegment[] = [];
    let currentBubble: Array<Extract<AssistantBlock, { kind: "reasoning" | "content" }>> = [];

    for (const block of blocks) {
      if (block.kind === "tool") {
        if (currentBubble.length > 0) {
          segments.push({ id: `bubble-${currentBubble[0].id}`, kind: "bubble", blocks: currentBubble });
          currentBubble = [];
        }
        segments.push({ id: `tool-${block.id}`, kind: "tool", block });
        continue;
      }
      currentBubble.push(block);
    }

    if (currentBubble.length > 0) {
      segments.push({ id: `bubble-${currentBubble[0].id}`, kind: "bubble", blocks: currentBubble });
    }

    return segments;
  };

  return (
    <main className="chat-workbench">
      <aside className="chat-sidebar">
        <div className="chat-sidebar__desktop">
          <section className="sidebar-card">
            <div className="sidebar-switcher">
              <button className={`sidebar-tab ${sidebarView === "files" ? "is-active" : ""}`} onClick={() => setSidebarView("files")}>文件</button>
              <button className={`sidebar-tab ${sidebarView === "skills" ? "is-active" : ""}`} onClick={() => setSidebarView("skills")}>技能</button>
            </div>

            {sidebarView === "files" ? (
              <>
                <div className="sidebar-card__header">
                  <div>
                    <h2>会话文件</h2>
                    <p>当前会话生命周期内上传与产出的所有文件</p>
                  </div>
                  <label className="sidebar-button">
                    <span>上传</span>
                    <input type="file" onChange={(event) => { const file = event.target.files?.[0]; void handleUpload(file); event.currentTarget.value = ""; }} />
                  </label>
                </div>
                <div className="sidebar-list">
                  {files.length === 0 ? <span className="empty-text">暂无文件</span> : null}
                  {files.map((item) => (
                    <article key={item.file_id} className="sidebar-item">
                      <strong>{item.name}</strong>
                      <p>{item.category} · {item.size} bytes</p>
                      <a className="download-link" href={getDownloadUrl(sessionId, item.file_id)} target="_blank" rel="noreferrer">下载</a>
                    </article>
                  ))}
                </div>
              </>
            ) : (
              <>
                <div className="sidebar-card__header">
                  <div>
                    <h2>技能管理</h2>
                    <p>内置技能、宿主技能与用户技能统一管理</p>
                  </div>
                </div>
                <div className="skill-form">
                  <input value={skillName} onChange={(event) => setSkillName(event.target.value)} placeholder="技能名称" />
                  <input value={skillDescription} onChange={(event) => setSkillDescription(event.target.value)} placeholder="技能描述" />
                  <textarea className="composer-textarea skill-textarea" value={skillContent} onChange={(event) => setSkillContent(event.target.value)} placeholder="输入 SKILL.md 主体内容" />
                  <button className="sidebar-button sidebar-button--solid" disabled={!canUploadSkill} onClick={handleUploadSkill}>上传技能</button>
                </div>
                <div className="sidebar-list">
                  {skills.length === 0 ? <span className="empty-text">暂无技能</span> : null}
                  {skills.map((item, index) => (
                    <article key={`${item.name}-${index}`} className="sidebar-item">
                      <strong>{item.name}</strong>
                      <p>{item.description}</p>
                      <span className="skill-badge">{item.source}</span>
                    </article>
                  ))}
                </div>
              </>
            )}
          </section>
        </div>

        <div className="chat-sidebar__mobile">
          <button className={`sidebar-tab ${sidebarView === "files" ? "is-active" : ""}`} onClick={() => setSidebarView("files")}>文件</button>
          <button className={`sidebar-tab ${sidebarView === "skills" ? "is-active" : ""}`} onClick={() => setSidebarView("skills")}>技能</button>
        </div>
      </aside>

      <section className="chat-main">
        {error ? <section className="error-banner">{error}</section> : null}

        <div ref={historyRef} className="chat-history">
          {messages.map((message) =>
            message.role === "user" ? (
              <article key={message.id} className="chat-row is-user">
                <div className="chat-bubble is-user user-bubble">
                  <div className="chat-bubble__content markdown-body" dangerouslySetInnerHTML={renderMarkdown(message.content)} />
                </div>
              </article>
            ) : (
              <article key={message.id} className="assistant-stack">
                {renderAssistantSegments(message.blocks).map((segment) =>
                  segment.kind === "tool" ? (
                    <article key={segment.id} className="chat-row is-assistant">
                      <details className="chat-bubble is-assistant is-tool">
                        <summary className="tool-summary">
                          <span className="tool-summary__title">{segment.block.title}</span>
                          <span className="tool-summary__meta">{segment.block.meta} · {segment.block.status === "running" ? "执行中" : "详情"}</span>
                        </summary>
                        {segment.block.argumentsText ? <pre>{segment.block.argumentsText}</pre> : null}
                        {segment.block.outputText ? <pre>{segment.block.outputText}</pre> : null}
                      </details>
                    </article>
                  ) : (
                    <article key={segment.id} className="chat-row is-assistant">
                      <div className="chat-bubble is-assistant">
                        {segment.blocks.map((block) =>
                          block.kind === "reasoning" ? (
                            <div key={block.id} className="bubble-reasoning">
                              <div className="bubble-reasoning__label">Thinking</div>
                              <div className="markdown-body" dangerouslySetInnerHTML={renderMarkdown(block.content)} />
                            </div>
                          ) : (
                            <div key={block.id} className="chat-bubble__content markdown-body" dangerouslySetInnerHTML={renderMarkdown(block.content)} />
                          ),
                        )}
                      </div>
                    </article>
                  ),
                )}
              </article>
            ),
          )}
        </div>

        <footer className="chat-composer">
          <div className="session-chip">会话 ID: {sessionId || "初始化中"}</div>
          <textarea
            className="composer-textarea"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void handleSend();
              }
            }}
            placeholder="输入任务，例如：读取上传文件，执行脚本，并生成可下载结果。"
          />
          <div className="composer-actions">
            <span className="composer-tip">{busy ? "正在处理，请稍候" : "Enter 发送，Shift + Enter 换行"}</span>
            <button className="send-button" disabled={!canSend} onClick={handleSend}>{busy ? "处理中" : "发送"}</button>
          </div>
        </footer>
      </section>

      <div className="chat-sidebar__mobile-panel">
        <section className="sidebar-card">
          {sidebarView === "files" ? (
            <>
              <div className="sidebar-card__header">
                <h2>会话文件</h2>
                <label className="sidebar-button">
                  <span>上传</span>
                  <input type="file" onChange={(event) => { const file = event.target.files?.[0]; void handleUpload(file); event.currentTarget.value = ""; }} />
                </label>
              </div>
              <div className="sidebar-list">
                {files.length === 0 ? <span className="empty-text">暂无文件</span> : null}
                {files.map((item) => (
                  <article key={item.file_id} className="sidebar-item">
                    <strong>{item.name}</strong>
                    <p>{item.category} · {item.size} bytes</p>
                    <a className="download-link" href={getDownloadUrl(sessionId, item.file_id)} target="_blank" rel="noreferrer">下载</a>
                  </article>
                ))}
              </div>
            </>
          ) : (
            <>
              <div className="sidebar-card__header"><h2>技能管理</h2></div>
              <div className="skill-form">
                <input value={skillName} onChange={(event) => setSkillName(event.target.value)} placeholder="技能名称" />
                <input value={skillDescription} onChange={(event) => setSkillDescription(event.target.value)} placeholder="技能描述" />
                <textarea className="composer-textarea skill-textarea" value={skillContent} onChange={(event) => setSkillContent(event.target.value)} placeholder="输入 SKILL.md 主体内容" />
                <button className="sidebar-button sidebar-button--solid" disabled={!canUploadSkill} onClick={handleUploadSkill}>上传技能</button>
              </div>
              <div className="sidebar-list">
                {skills.length === 0 ? <span className="empty-text">暂无技能</span> : null}
                {skills.map((item, index) => (
                  <article key={`${item.name}-${index}`} className="sidebar-item">
                    <strong>{item.name}</strong>
                    <p>{item.description}</p>
                    <span className="skill-badge">{item.source}</span>
                  </article>
                ))}
              </div>
            </>
          )}
        </section>
      </div>
    </main>
  );
}
