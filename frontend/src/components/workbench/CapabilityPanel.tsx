import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import JSZip from "jszip";

import {
  connectMcp, deleteMcp, deleteSessionSkill, deleteUserSkill, installExtension,
  listCapabilities, listExtensions, publishExtension, revokeMcpOAuth, saveMcp,
  startMcpOAuth, uploadUserSkill, type ExtensionEntry, type McpCapability,
} from "../../api/client";
import type { SkillItem } from "../../pages/workbench/types";

type Scope = "session" | "user";
type Dialog = "skill" | "mcp" | "store" | null;
type Props = {
  sessionId: string;
  skills: SkillItem[];
  isEmbedMode: boolean;
  onUploadSessionSkill: (file: File | undefined) => void;
  onRefresh: () => void;
};

const emptyMcpForm = {
  name: "", description: "", transport: "streamable_http", url: "", command: "",
  args: "", env: [{ name: "", value: "", secret: true }], headers: [{ name: "", value: "", secret: true }], auth: "none", oauthScopes: "", json: "",
};

function parseMcpJson(value: string): Record<string, unknown> {
  const parsed = JSON.parse(value);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("MCP 配置必须是 JSON 对象");
  const root = parsed as Record<string, unknown>;
  if (!root.mcpServers) return root;
  const entries = Object.entries(root.mcpServers as Record<string, Record<string, unknown>>);
  if (entries.length !== 1) throw new Error("一次只能添加一个 MCP server");
  const [name, server] = entries[0];
  return {
    name, ...server,
    command: typeof server.command === "string" ? [server.command] : server.command ?? [],
    transport: server.url ? "streamable_http" : "stdio",
  };
}

export function CapabilityPanel({ sessionId, skills, isEmbedMode, onUploadSessionSkill, onRefresh }: Props) {
  const [preferenceKey, setPreferenceKey] = useState(`aethercore-capabilities:${isEmbedMode ? "embed" : "user"}`);
  const [disabled, setDisabled] = useState<string[]>([]);
  const [mcp, setMcp] = useState<McpCapability[]>([]);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [scope, setScope] = useState<Scope>("user");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [extensions, setExtensions] = useState<ExtensionEntry[]>([]);
  const [mcpForm, setMcpForm] = useState(emptyMcpForm);
  const [publishForm, setPublishForm] = useState<{ kind: "skill" | "mcp"; name: string; description: string; version: string; artifact?: File }>({ kind: "skill", name: "", description: "", version: "1.0.0" });

  const refresh = async () => {
    const result = await listCapabilities(sessionId);
    setMcp(result.data.mcp || []);
    setPreferenceKey(result.data.preference_namespace);
    localStorage.setItem("aethercore-capabilities:active", result.data.preference_namespace);
    try { setDisabled(JSON.parse(localStorage.getItem(result.data.preference_namespace) || "[]")); }
    catch { setDisabled([]); }
    onRefresh();
  };

  useEffect(() => { void refresh().catch(() => undefined); }, [sessionId]);
  useEffect(() => {
    const complete = (event: MessageEvent) => {
      if (event.data?.type === "aethercore:mcp-oauth-complete") void refresh();
    };
    window.addEventListener("message", complete);
    return () => window.removeEventListener("message", complete);
  }, [sessionId]);

  const toggle = (key: string) => setDisabled((current) => {
    const next = current.includes(key) ? current.filter((item) => item !== key) : [...current, key];
    localStorage.setItem(preferenceKey, JSON.stringify(next));
    return next;
  });

  const submitSkill = async (file?: File) => {
    if (!file) return;
    setBusy(true); setError("");
    try {
      if (scope === "user") await uploadUserSkill(file); else onUploadSessionSkill(file);
      setDialog(null); await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "添加失败"); }
    finally { setBusy(false); }
  };

  const submitSkillFolder = async (files: FileList | null) => {
    if (!files?.length) return;
    const archive = new JSZip();
    for (const file of Array.from(files)) archive.file(file.webkitRelativePath || file.name, file);
    const blob = await archive.generateAsync({ type: "blob" });
    await submitSkill(new File([blob], "skill-folder.zip", { type: "application/zip" }));
  };

  const submitMcp = async () => {
    setBusy(true); setError("");
    try {
      const payload = mcpForm.json.trim() ? parseMcpJson(mcpForm.json) : {
        name: mcpForm.name, description: mcpForm.description, transport: mcpForm.transport,
        url: mcpForm.transport === "streamable_http" ? mcpForm.url : null,
        command: mcpForm.transport === "stdio" ? mcpForm.command.trim().split(/\s+/).filter(Boolean) : [],
        args: mcpForm.args.trim().split(/\s+/).filter(Boolean),
            env: Object.fromEntries(mcpForm.env.filter((item) => item.name.trim()).map((item) => [item.name.trim(), item.value])),
            headers: Object.fromEntries(mcpForm.headers.filter((item) => item.name.trim()).map((item) => [item.name.trim(), item.value])),
        auth: mcpForm.auth, oauth_scopes: mcpForm.oauthScopes,
      };
      await saveMcp(payload, scope, { sessionId });
      setMcpForm(emptyMcpForm); setDialog(null); await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "添加失败"); }
    finally { setBusy(false); }
  };

  const openStore = async () => {
    setDialog("store"); setError("");
    try { setExtensions((await listExtensions()).data || []); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "加载失败"); }
  };

  const authorize = async (item: McpCapability) => {
    setError("");
    const popup = window.open("about:blank", "aethercore-mcp-oauth", "popup,width=640,height=760");
    try {
      const result = await startMcpOAuth(item.name, sessionId);
      if (!popup) throw new Error("浏览器阻止了授权窗口，请允许本站打开弹窗后重试");
      popup.location.href = result.data.authorization_url;
    } catch (reason) {
      popup?.close();
      setError(reason instanceof Error ? reason.message : "授权失败");
    }
  };

  return <div className="capability-panel">
    <div className="pane-header"><h3>能力</h3></div>

    <div className="capability-section-title"><h3 className="sub-title">Skill ({skills.length})</h3><button className="capability-add-icon" aria-label="添加 Skill" title="添加 Skill" onClick={() => { setScope("user"); setDialog("skill"); }}>+</button></div>
    <div className="item-list">{skills.map((item) => {
      const key = `skill:${item.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}`;
      return <article key={`${item.source}:${item.name}`} className="resource-card block">
        <div className="flex-row"><strong>{item.name}</strong><span className="badge">{item.source}</span><input type="checkbox" checked={!disabled.includes(key)} onChange={() => toggle(key)} aria-label={`启用 ${item.name}`} /></div>
        <p className="desc">{item.description}</p>
        {item.source === "user" || item.source === "upload" ? <button className="action-button small" onClick={() => void (item.source === "user" ? deleteUserSkill(item.name) : deleteSessionSkill(sessionId, item.name)).then(refresh).catch((reason) => setError(reason.message))}>删除</button> : null}
      </article>;
    })}</div>

    <div className="capability-section-title"><h3 className="sub-title">MCP ({mcp.length})</h3><button className="capability-add-icon" aria-label="添加 MCP" title="添加 MCP" onClick={() => { setScope("user"); setDialog("mcp"); }}>+</button></div>
    <div className="item-list">{mcp.map((item) => {
      const key = `mcp:${item.name}`;
      return <article key={item.id} className="resource-card block">
        <div className="flex-row"><strong>{item.name}</strong><span className="badge">{item.scope} · {item.transport}</span><input type="checkbox" checked={!disabled.includes(key)} onChange={() => toggle(key)} aria-label={`启用 ${item.name}`} /></div>
        <p className="desc">{item.description || item.url || item.command?.join(" ")}</p>
        <div className="flex-row capability-actions">
          {item.auth === "oauth" ? <button className="action-button small" onClick={() => void authorize(item)}>{item.oauth_connected ? "重新授权" : "授权"}</button> : null}
          <button className="action-button small" onClick={() => void connectMcp(item.name, sessionId).then(refresh).catch((reason) => setError(reason.message))}>连接</button>
          {item.oauth_connected ? <button className="action-button small" onClick={() => void revokeMcpOAuth(item.name, sessionId).then(refresh).catch((reason) => setError(reason.message))}>删除凭据</button> : null}
          {item.scope !== "platform" ? <button className="action-button small" onClick={() => void deleteMcp(item.name, item.scope === "session" ? "session" : "user", { sessionId }).then(refresh).catch((reason) => setError(reason.message))}>删除</button> : null}
        </div>
      </article>;
    })}</div>

    {dialog ? createPortal(<div className="skill-upload-modal-backdrop" onClick={() => setDialog(null)}>
      <section className={`skill-upload-modal capability-dialog ${dialog === "store" ? "capability-dialog--market" : ""}`} onClick={(event) => event.stopPropagation()}>
        <header className="skill-upload-modal__header"><h4>{dialog === "skill" ? "添加 Skill" : dialog === "mcp" ? "添加 MCP" : "拓展市场"}</h4><button className="icon-button" aria-label="关闭" onClick={() => setDialog(null)}>×</button></header>
        <div className="skill-upload-modal__body">
          {dialog !== "store" ? <div className="segment-control"><button className={`segment-btn ${scope === "user" ? "active" : ""}`} onClick={() => setScope("user")}>个人永久</button><button className={`segment-btn ${scope === "session" ? "active" : ""}`} onClick={() => setScope("session")}>当前会话</button></div> : null}
          {dialog === "skill" ? <div className="skill-upload-modal__actions-grid">
            <label className="fm-btn primary"><span>{busy ? "上传中..." : "上传 zip / SKILL.md"}</span><input type="file" accept=".zip,.md" disabled={busy} onChange={(event) => void submitSkill(event.target.files?.[0])} /></label>
            <label className="fm-btn outline"><span>选择技能文件夹</span><input type="file" multiple disabled={busy} onChange={(event) => void submitSkillFolder(event.target.files)} {...({ webkitdirectory: "true", directory: "" } as Record<string, string>)} /></label>
          </div> : null}
          {dialog === "mcp" ? <McpForm form={mcpForm} setForm={setMcpForm} busy={busy} onSubmit={submitMcp} /> : null}
          {dialog === "store" ? <StorePanel sessionId={sessionId} extensions={extensions} publishForm={publishForm} setPublishForm={setPublishForm} openStore={openStore} refresh={refresh} setError={setError} /> : null}
          {error ? <p className="skill-upload-modal__error">{error}</p> : null}
        </div>
      </section>
    </div>, document.body) : null}
    <button className="capability-market-entry" onClick={() => void openStore()}><span>拓展市场</span><span aria-hidden="true">›</span></button>
  </div>;
}

function McpForm({ form, setForm, busy, onSubmit }: { form: typeof emptyMcpForm; setForm: (form: typeof emptyMcpForm) => void; busy: boolean; onSubmit: () => Promise<void> }) {
  const updateRow = (key: "env" | "headers", index: number, field: "name" | "value" | "secret", value: string | boolean) => {
    const rows = form[key].map((row, rowIndex) => rowIndex === index ? { ...row, [field]: value } : row);
    setForm({ ...form, [key]: rows });
  };
  const addRow = (key: "env" | "headers") => setForm({ ...form, [key]: [...form[key], { name: "", value: "", secret: true }] });
  return <div className="form-grid capability-form">
    <label className="full-width">标准 MCP JSON（可选）<textarea rows={5} placeholder={'{"mcpServers":{"docs":{"url":"https://example.com/mcp"}}}'} value={form.json} onChange={(event) => setForm({ ...form, json: event.target.value })} /></label>
    {!form.json.trim() ? <>
      <label>名称<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label><label>说明<input value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></label>
      <label>传输<select value={form.transport} onChange={(event) => setForm({ ...form, transport: event.target.value })}><option value="streamable_http">Streamable HTTP</option><option value="stdio">STDIO</option></select></label>
      {form.transport === "stdio" ? <label>命令<input value={form.command} onChange={(event) => setForm({ ...form, command: event.target.value })} /></label> : <label>HTTPS URL<input value={form.url} onChange={(event) => setForm({ ...form, url: event.target.value })} /></label>}
      <label>参数<input value={form.args} onChange={(event) => setForm({ ...form, args: event.target.value })} /></label><label>认证<select value={form.auth} onChange={(event) => setForm({ ...form, auth: event.target.value })}><option value="none">无 / 请求头</option><option value="oauth">OAuth 2.1</option></select></label>
      {form.auth === "oauth" ? <label>OAuth scopes<input value={form.oauthScopes} onChange={(event) => setForm({ ...form, oauthScopes: event.target.value })} /></label> : null}
      <SecretRows label="环境变量" rows={form.env} onChange={(index, field, value) => updateRow("env", index, field, value)} onAdd={() => addRow("env")} />
      <SecretRows label="请求头" rows={form.headers} onChange={(index, field, value) => updateRow("headers", index, field, value)} onAdd={() => addRow("headers")} />
    </> : null}
    <button className="fm-btn primary" disabled={busy} onClick={() => void onSubmit()}>保存</button>
  </div>;
}

function SecretRows({ label, rows, onChange, onAdd }: { label: string; rows: { name: string; value: string; secret: boolean }[]; onChange: (index: number, field: "name" | "value" | "secret", value: string | boolean) => void; onAdd: () => void }) {
  return <div className="secret-rows full-width"><div className="secret-rows__header"><strong>{label}</strong><button type="button" className="text-button" onClick={onAdd}>添加一行</button></div>{rows.map((row, index) => <div className="secret-row" key={`${label}-${index}`}><input aria-label={`${label}名称`} placeholder="名称" value={row.name} onChange={(event) => onChange(index, "name", event.target.value)} /><input aria-label={`${label}值`} type={row.secret ? "password" : "text"} placeholder={row.secret ? "由用户配置的密钥" : "固定值"} value={row.value} onChange={(event) => onChange(index, "value", event.target.value)} /><label className="secret-row__toggle"><input type="checkbox" checked={row.secret} onChange={(event) => onChange(index, "secret", event.target.checked)} />密钥</label></div>)}</div>;
}

function StorePanel({ sessionId, extensions, publishForm, setPublishForm, openStore, refresh, setError }: { sessionId: string; extensions: ExtensionEntry[]; publishForm: { kind: "skill" | "mcp"; name: string; description: string; version: string; artifact?: File }; setPublishForm: (value: typeof publishForm) => void; openStore: () => Promise<void>; refresh: () => Promise<void>; setError: (value: string) => void }) {
  const [selectedScopes, setSelectedScopes] = useState<Record<string, Scope>>({});
  const getScope = (entryId: string) => selectedScopes[entryId] || "session";
  return <><details><summary>发布拓展</summary><div className="form-grid capability-form">
    <label>类型<select value={publishForm.kind} onChange={(event) => setPublishForm({ ...publishForm, kind: event.target.value as "skill" | "mcp" })}><option value="skill">Skill</option><option value="mcp">MCP</option></select></label><label>名称<input value={publishForm.name} onChange={(event) => setPublishForm({ ...publishForm, name: event.target.value })} /></label><label>介绍<input value={publishForm.description} onChange={(event) => setPublishForm({ ...publishForm, description: event.target.value })} /></label><label>版本<input value={publishForm.version} onChange={(event) => setPublishForm({ ...publishForm, version: event.target.value })} /></label>
    <input type="file" accept={publishForm.kind === "skill" ? ".zip,.md" : ".json"} onChange={(event) => setPublishForm({ ...publishForm, artifact: event.target.files?.[0] })} /><button className="fm-btn primary" disabled={!publishForm.artifact} onClick={() => publishForm.artifact && void publishExtension({ ...publishForm, artifact: publishForm.artifact }).then(openStore).catch((reason) => setError(reason.message))}>发布</button>
  </div></details><div className="item-list store-list">{extensions.map((item) => { const selectedScope = getScope(item.entry_id); return <article className="resource-card block" key={item.entry_id}><div className="flex-row"><strong>{item.name}</strong><span className="badge">{item.kind}</span></div><p className="desc">{item.description || "暂无介绍"}</p><small>v{item.current_version} · {item.submitter_name} · 使用 {item.usage_count} · {new Date(item.updated_at).toLocaleDateString()}</small><div className="market-apply"><label><input type="radio" name={`market-scope-${item.entry_id}`} checked={selectedScope === "session"} onChange={() => setSelectedScopes({ ...selectedScopes, [item.entry_id]: "session" })} />临时</label><label><input type="radio" name={`market-scope-${item.entry_id}`} checked={selectedScope === "user"} onChange={() => setSelectedScopes({ ...selectedScopes, [item.entry_id]: "user" })} />个人</label><button className="fm-btn primary small" onClick={() => void installExtension(item.entry_id, selectedScope, { sessionId }).then(refresh).catch((reason) => setError(reason.message))}>应用</button></div></article>; })}</div></>;
}
