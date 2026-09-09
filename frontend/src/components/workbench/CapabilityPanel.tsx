import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import JSZip from "jszip";

import {
  connectMcp, deleteMcp, deleteSessionSkill, deleteUserSkill, installExtension,
  listCapabilities, listExtensions, publishExtension, revokeMcpOAuth, saveMcp,
  startMcpOAuth, uploadUserSkill, type ExtensionEntry, type McpCapability,
} from "../../api/client";
import type { SkillItem } from "../../pages/workbench/types";
import { WorkbenchIcons as Icons } from "./WorkbenchIcons";

type Scope = "session" | "user";
type Dialog = "skill" | "mcp" | "store" | null;
type PublishForm = { kind: "skill" | "mcp"; name: string; description: string; version: string; artifact?: File };
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
const emptyPublishForm: PublishForm = { kind: "skill", name: "", description: "", version: "1.0.0" };

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

// McpForm 表单字段 → 后端 _validate_mcp 期望的扁平配置对象
function buildMcpConfig(form: typeof emptyMcpForm) {
  if (form.json.trim()) return parseMcpJson(form.json);
  return {
    name: form.name, description: form.description, transport: form.transport,
    url: form.transport === "streamable_http" ? form.url : null,
    command: form.transport === "stdio" ? form.command.trim().split(/\s+/).filter(Boolean) : [],
    args: form.args.trim().split(/\s+/).filter(Boolean),
    auth: form.auth, oauth_scopes: form.oauthScopes,
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
  const [storeLoading, setStoreLoading] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [mcpForm, setMcpForm] = useState(emptyMcpForm);
  const [publishForm, setPublishForm] = useState<PublishForm>(emptyPublishForm);

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

  // Escape 逐层关闭：先收起发布悬浮窗，再关当前弹窗
  useEffect(() => {
    if (!dialog) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (publishOpen) setPublishOpen(false);
      else setDialog(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [dialog, publishOpen]);

  // 市场窗关闭时同步收起发布窗，避免下次打开残留
  useEffect(() => {
    if (dialog !== "store") setPublishOpen(false);
  }, [dialog]);

  // 成功提示自动消失
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 3000);
    return () => window.clearTimeout(timer);
  }, [notice]);

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
      const config = buildMcpConfig(mcpForm);
      await saveMcp({ ...config, env: Object.fromEntries(mcpForm.env.filter((item) => item.name.trim()).map((item) => [item.name.trim(), item.value])), headers: Object.fromEntries(mcpForm.headers.filter((item) => item.name.trim()).map((item) => [item.name.trim(), item.value])) }, scope, { sessionId });
      setMcpForm(emptyMcpForm); setDialog(null); await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "添加失败"); }
    finally { setBusy(false); }
  };

  const loadExtensions = async () => {
    setStoreLoading(true);
    try { setExtensions((await listExtensions()).data || []); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "加载失败"); }
    finally { setStoreLoading(false); }
  };

  const openStore = async () => {
    setDialog("store"); setError("");
    await loadExtensions();
  };

  // 发布成功：收起悬浮窗，在市场窗内提示并刷新列表
  const handlePublished = async () => {
    setPublishOpen(false);
    setNotice("发布成功");
    await loadExtensions();
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

    {/* 拓展市场入口：位于 Skill 区上方 */}
    <button type="button" className="capability-market-entry" onClick={() => void openStore()}>
      <span className="capability-market-entry__icon"><Icons.Store /></span>
      <span className="capability-market-entry__label"><strong>拓展市场</strong><small>浏览、安装和发布共享拓展</small></span>
      <Icons.ChevronRight />
    </button>

    <div className="capability-scroll">
      <div className="capability-section-title">
        <h3 className="sub-title">Skill<span className="capability-count">{skills.length}</span></h3>
        <button type="button" className="capability-add-icon" aria-label="添加 Skill" title="添加 Skill" onClick={() => { setScope("user"); setDialog("skill"); }}><Icons.Plus /></button>
      </div>
      <div className="item-list">
        {skills.length === 0 ? <div className="empty-state">暂无 Skill</div> : null}
        {skills.map((item) => {
        const key = `skill:${item.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}`;
        return <article key={`${item.source}:${item.name}`} className="resource-card block">
          <div className="flex-row"><strong>{item.name}</strong><span className="badge">{item.source}</span><input type="checkbox" checked={!disabled.includes(key)} onChange={() => toggle(key)} aria-label={`启用 ${item.name}`} /></div>
          <p className="desc">{item.description}</p>
          {item.source === "user" || item.source === "upload" ? <button className="action-button small" onClick={() => void (item.source === "user" ? deleteUserSkill(item.name) : deleteSessionSkill(sessionId, item.name)).then(refresh).catch((reason) => setError(reason.message))}>删除</button> : null}
        </article>;
      })}</div>

      <div className="capability-section-title">
        <h3 className="sub-title">MCP<span className="capability-count">{mcp.length}</span></h3>
        <button type="button" className="capability-add-icon" aria-label="添加 MCP" title="添加 MCP" onClick={() => { setScope("user"); setDialog("mcp"); }}><Icons.Plus /></button>
      </div>
      <div className="item-list">
        {mcp.length === 0 ? <div className="empty-state">暂无 MCP</div> : null}
        {mcp.map((item) => {
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

      {error && !dialog ? <p className="capability-panel__error">{error}</p> : null}
    </div>

    {dialog === "store" ? createPortal(
      <StoreDialog sessionId={sessionId} extensions={extensions} loading={storeLoading} error={error} notice={notice} onPublish={() => setPublishOpen(true)} onClose={() => setDialog(null)} setError={setError} refresh={refresh} />,
      document.body,
    ) : null}

    {/* 发布拓展：独立悬浮窗，叠加在市场窗之上 */}
    {dialog === "store" && publishOpen ? createPortal(
      <PublishDialog publishForm={publishForm} setPublishForm={setPublishForm} error={error} onClose={() => setPublishOpen(false)} onPublished={handlePublished} setError={setError} />,
      document.body,
    ) : null}

    {/* 添加 Skill / MCP */}
    {dialog === "skill" || dialog === "mcp" ? createPortal(
      <AddDialog kind={dialog} scope={scope} busy={busy} error={error} mcpForm={mcpForm} setMcpForm={setMcpForm} onScopeChange={setScope} onClose={() => setDialog(null)} onSubmitSkill={submitSkill} onSubmitSkillFolder={submitSkillFolder} onSubmitMcp={submitMcp} />,
      document.body,
    ) : null}
  </div>;
}

function McpForm({ form, setForm, allowSecrets = true }: { form: typeof emptyMcpForm; setForm: (form: typeof emptyMcpForm) => void; allowSecrets?: boolean }) {
  const updateRow = (key: "env" | "headers", index: number, field: "name" | "value" | "secret", value: string | boolean) => {
    const rows = form[key].map((row, rowIndex) => rowIndex === index ? { ...row, [field]: value } : row);
    setForm({ ...form, [key]: rows });
  };
  const addRow = (key: "env" | "headers") => setForm({ ...form, [key]: [...form[key], { name: "", value: "", secret: true }] });
  return <div className="form-grid capability-form">
    <label className="full-width">标准 MCP JSON（可选）<textarea rows={5} placeholder={'{"mcpServers":{"docs":{"url":"https://example.com/mcp"}}}'} value={form.json} onChange={(event) => setForm({ ...form, json: event.target.value })} /></label>
    {!form.json.trim() ? <>
      <label>名称<input placeholder="server 名称" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label><label>说明<input placeholder="一句话说明用途" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></label>
      <label>传输<select value={form.transport} onChange={(event) => setForm({ ...form, transport: event.target.value })}><option value="streamable_http">Streamable HTTP</option><option value="stdio">STDIO</option></select></label>
      {form.transport === "stdio" ? <label>命令<input placeholder="npx -y server" value={form.command} onChange={(event) => setForm({ ...form, command: event.target.value })} /></label> : <label>HTTPS URL<input placeholder="https://example.com/mcp" value={form.url} onChange={(event) => setForm({ ...form, url: event.target.value })} /></label>}
      <label>参数<input placeholder="空格分隔，可留空" value={form.args} onChange={(event) => setForm({ ...form, args: event.target.value })} /></label><label>认证<select value={form.auth} onChange={(event) => setForm({ ...form, auth: event.target.value })}><option value="none">无 / 请求头</option><option value="oauth">OAuth 2.1</option></select></label>
      {form.auth === "oauth" ? <label>OAuth scopes<input placeholder="逗号分隔，可留空" value={form.oauthScopes} onChange={(event) => setForm({ ...form, oauthScopes: event.target.value })} /></label> : null}
      {allowSecrets ? <>
        <SecretRows label="环境变量" rows={form.env} onChange={(index, field, value) => updateRow("env", index, field, value)} onAdd={() => addRow("env")} />
        <SecretRows label="请求头" rows={form.headers} onChange={(index, field, value) => updateRow("headers", index, field, value)} onAdd={() => addRow("headers")} />
      </> : null}
    </> : null}
  </div>;
}

function SecretRows({ label, rows, onChange, onAdd }: { label: string; rows: { name: string; value: string; secret: boolean }[]; onChange: (index: number, field: "name" | "value" | "secret", value: string | boolean) => void; onAdd: () => void }) {
  return <div className="secret-rows full-width"><div className="secret-rows__header"><strong>{label}</strong><button type="button" className="text-button" onClick={onAdd}>添加一行</button></div>{rows.map((row, index) => <div className="secret-row" key={`${label}-${index}`}><input aria-label={`${label}名称`} placeholder="名称" value={row.name} onChange={(event) => onChange(index, "name", event.target.value)} /><input aria-label={`${label}值`} type={row.secret ? "password" : "text"} placeholder={row.secret ? "由用户配置的密钥" : "固定值"} value={row.value} onChange={(event) => onChange(index, "value", event.target.value)} /><label className="secret-row__toggle"><input type="checkbox" checked={row.secret} onChange={(event) => onChange(index, "secret", event.target.checked)} />密钥</label></div>)}</div>;
}

/* 添加 Skill / MCP 弹窗：与市场窗统一的设计语言 */
function AddDialog({ kind, scope, busy, error, mcpForm, setMcpForm, onScopeChange, onClose, onSubmitSkill, onSubmitSkillFolder, onSubmitMcp }: {
  kind: "skill" | "mcp";
  scope: Scope;
  busy: boolean;
  error: string;
  mcpForm: typeof emptyMcpForm;
  setMcpForm: (form: typeof emptyMcpForm) => void;
  onScopeChange: (scope: Scope) => void;
  onClose: () => void;
  onSubmitSkill: (file?: File) => Promise<void>;
  onSubmitSkillFolder: (files: FileList | null) => Promise<void>;
  onSubmitMcp: () => Promise<void>;
}) {
  const isSkill = kind === "skill";
  return <div className="capability-add-dialog__backdrop" onClick={onClose}>
    <section className="capability-add-dialog" role="dialog" aria-modal="true" aria-label={isSkill ? "添加 Skill" : "添加 MCP"} onClick={(event) => event.stopPropagation()}>
      <header className="capability-add-dialog__header">
        <div className="capability-add-dialog__heading">
          <span className={`capability-add-dialog__icon capability-add-dialog__icon--${kind}`}>{isSkill ? <Icons.Sparkles /> : <Icons.Braces />}</span>
          <div>
            <h4>{isSkill ? "添加 Skill" : "添加 MCP"}</h4>
            <p>{isSkill ? "上传技能包，立即可在会话中使用" : "接入外部 MCP 工具服务"}</p>
          </div>
        </div>
        <button type="button" className="icon-button" aria-label="关闭" onClick={onClose}><Icons.Close /></button>
      </header>

      <div className="capability-add-dialog__body">
        <div className="scope-picker" role="group" aria-label="生效范围">
          <button type="button" data-active={scope === "user"} onClick={() => onScopeChange("user")}><strong>个人永久</strong><small>保存到个人空间，所有会话可用</small></button>
          <button type="button" data-active={scope === "session"} onClick={() => onScopeChange("session")}><strong>当前会话</strong><small>仅本次会话生效，结束即失效</small></button>
        </div>

        {isSkill ? <div className="add-skill-drops">
          <label className="add-drop">
            <span className="add-drop__icon"><Icons.Upload /></span>
            <strong>{busy ? "上传中..." : "上传技能包"}</strong>
            <small>.zip 技能包或 SKILL.md 文件</small>
            <input type="file" accept=".zip,.md" disabled={busy} onChange={(event) => void onSubmitSkill(event.target.files?.[0])} />
          </label>
          <label className="add-drop">
            <span className="add-drop__icon"><Icons.Folder /></span>
            <strong>选择技能文件夹</strong>
            <small>自动打包文件夹上传</small>
            <input type="file" multiple disabled={busy} onChange={(event) => void onSubmitSkillFolder(event.target.files)} {...({ webkitdirectory: "true", directory: "" } as Record<string, string>)} />
          </label>
        </div> : <>
          <McpForm form={mcpForm} setForm={setMcpForm} />
          <button type="button" className="capability-add-dialog__submit" disabled={busy} onClick={() => void onSubmitMcp()}>{busy ? "保存中..." : "保存"}</button>
        </>}

        {error ? <p className="capability-add-dialog__error">{error}</p> : null}
      </div>
    </section>
  </div>;
}

function StoreDialog({ sessionId, extensions, loading, error, notice, onPublish, refresh, onClose, setError }: {
  sessionId: string;
  extensions: ExtensionEntry[];
  loading: boolean;
  error: string;
  notice: string;
  onPublish: () => void;
  refresh: () => Promise<void>;
  onClose: () => void;
  setError: (value: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState<"all" | "skill" | "mcp">("all");
  const [installingId, setInstallingId] = useState<string | null>(null);
  const [installedIds, setInstalledIds] = useState<string[]>([]);
  const [selectedScopes, setSelectedScopes] = useState<Record<string, Scope>>({});

  const keyword = query.trim().toLowerCase();
  const hasFilter = Boolean(keyword) || kindFilter !== "all";
  const filtered = extensions
    .filter((item) => (kindFilter === "all" || item.kind === kindFilter)
      && (!keyword || item.name.toLowerCase().includes(keyword) || item.description.toLowerCase().includes(keyword)))
    .sort((a, b) => b.usage_count - a.usage_count || a.name.localeCompare(b.name));

  const selectScope = (entryId: string, scope: Scope) => setSelectedScopes((current) => ({ ...current, [entryId]: scope }));

  const install = async (item: ExtensionEntry) => {
    if (installingId) return;
    const scope = selectedScopes[item.entry_id] || "session";
    setInstallingId(item.entry_id); setError("");
    try {
      await installExtension(item.entry_id, scope, { sessionId });
      await refresh();
      setInstalledIds((current) => [...current, item.entry_id]);
      window.setTimeout(() => setInstalledIds((current) => current.filter((id) => id !== item.entry_id)), 2400);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "安装失败"); }
    finally { setInstallingId(null); }
  };

  return <div className="market-dialog__backdrop" onClick={onClose}>
    <section className="market-dialog" role="dialog" aria-modal="true" aria-label="拓展市场" onClick={(event) => event.stopPropagation()}>
      <header className="market-dialog__header">
        <div className="market-dialog__heading">
          <span className="market-dialog__icon"><Icons.Store /></span>
          <div>
            <h4>拓展市场</h4>
            <p>{loading ? "正在加载拓展…" : extensions.length > 0 ? `${extensions.length} 个拓展 · 可安装到会话或个人空间` : "暂无拓展，快来发布第一个"}</p>
          </div>
        </div>
        <button type="button" className="icon-button" aria-label="关闭" onClick={onClose}><Icons.Close /></button>
      </header>

      <div className="market-dialog__toolbar">
        <label className="market-search">
          <Icons.Search />
          <input type="text" placeholder="搜索拓展名称或介绍…" value={query} onChange={(event) => setQuery(event.target.value)} />
          {query ? <button type="button" className="market-search__clear" aria-label="清空搜索" onClick={() => setQuery("")}><Icons.Close /></button> : null}
        </label>
        <div className="market-filter" role="group" aria-label="筛选拓展类型">
          <button type="button" data-active={kindFilter === "all"} onClick={() => setKindFilter("all")}>全部</button>
          <button type="button" data-active={kindFilter === "skill"} onClick={() => setKindFilter("skill")}>Skill</button>
          <button type="button" data-active={kindFilter === "mcp"} onClick={() => setKindFilter("mcp")}>MCP</button>
        </div>
        <button type="button" className="market-publish-toggle" onClick={onPublish}>
          <Icons.Upload />
          <span>发布拓展</span>
        </button>
      </div>

      <div className="market-dialog__body">
        {error ? <p className="market-dialog__error">{error}</p> : null}
        {notice ? <p className="market-dialog__notice">{notice}</p> : null}
        {loading ? <div className="market-grid">
          {[0, 1, 2, 3].map((index) => <div className="market-card market-card--skeleton" key={index} />)}
        </div> : filtered.length === 0 ? <div className="market-empty">
          <span className="market-empty__icon"><Icons.Store /></span>
          <strong>{hasFilter ? "没有匹配的拓展" : "市场还是空的"}</strong>
          <p>{hasFilter ? "换个关键词或筛选条件试试" : "发布第一个拓展，让大家用起来"}</p>
        </div> : <div className="market-grid">
          {filtered.map((item) => {
            const scope = selectedScopes[item.entry_id] || "session";
            const installing = installingId === item.entry_id;
            const installed = installedIds.includes(item.entry_id);
            return <article className={`market-card market-card--${item.kind}`} key={item.entry_id}>
              <div className="market-card__head">
                <span className="market-card__icon">{item.kind === "skill" ? <Icons.Sparkles /> : <Icons.Braces />}</span>
                <strong className="market-card__name" title={item.name}>{item.name}</strong>
                <span className="market-card__version">v{item.current_version}</span>
              </div>
              <p className="market-card__desc">{item.description || "暂无介绍"}</p>
              <div className="market-card__meta">
                <span title="提交人"><Icons.User />{item.submitter_name}</span>
                <span title="使用次数"><Icons.Zap />{item.usage_count} 次使用</span>
                <span>{new Date(item.updated_at).toLocaleDateString()}</span>
              </div>
              <div className="market-card__foot">
                <div className="market-scope" role="group" aria-label={`安装范围：${item.name}`}>
                  <button type="button" title="仅当前会话可用" data-active={scope === "session"} onClick={() => selectScope(item.entry_id, "session")}>临时</button>
                  <button type="button" title="安装到个人空间，长期可用" data-active={scope === "user"} onClick={() => selectScope(item.entry_id, "user")}>个人</button>
                </div>
                <button type="button" className="market-install" disabled={installing} data-done={installed} onClick={() => void install(item)}>
                  {installed ? "已安装" : installing ? "安装中..." : "安装"}
                </button>
              </div>
            </article>;
          })}
        </div>}
      </div>
    </section>
  </div>;
}

function PublishDialog({ publishForm, setPublishForm, error, onClose, onPublished, setError }: {
  publishForm: PublishForm;
  setPublishForm: (value: PublishForm) => void;
  error: string;
  onClose: () => void;
  onPublished: () => Promise<void>;
  setError: (value: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [mcpForm, setMcpForm] = useState(emptyMcpForm);
  const isSkill = publishForm.kind === "skill";

  // 切换类型时清空已选文件，MCP 与 Skill 的内容区随之联动
  const switchKind = (kind: "skill" | "mcp") => {
    if (publishForm.kind === kind) return;
    setPublishForm({ ...publishForm, kind, artifact: undefined });
  };

  const canSubmit = isSkill
    ? Boolean(publishForm.artifact && publishForm.name.trim())
    : Boolean(mcpForm.json.trim() || mcpForm.name.trim());

  const submit = async () => {
    if (busy || !canSubmit) return;
    setBusy(true); setError("");
    try {
      if (isSkill) {
        if (!publishForm.artifact) return;
        await publishExtension({ kind: "skill", name: publishForm.name, description: publishForm.description, version: publishForm.version, artifact: publishForm.artifact });
        setPublishForm({ ...emptyPublishForm });
      } else {
        // MCP：表单字段与“添加 MCP”一致，提交时序列化为规范 JSON 制品
        const config = buildMcpConfig(mcpForm);
        const name = String(config.name || "").trim();
        if (!name) throw new Error("请填写 MCP 名称");
        const artifact = new File([JSON.stringify(config, null, 2)], `${name}.json`, { type: "application/json" });
        await publishExtension({ kind: "mcp", name, description: String(config.description || ""), version: publishForm.version, artifact });
        setMcpForm(emptyMcpForm);
        setPublishForm({ ...emptyPublishForm });
      }
      await onPublished();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "发布失败"); }
    finally { setBusy(false); }
  };

  return <div className="market-publish-dialog__backdrop" onClick={onClose}>
    <section className="market-publish-dialog" role="dialog" aria-modal="true" aria-label="发布拓展" onClick={(event) => event.stopPropagation()}>
      <header className="market-publish-dialog__header">
        <div className="market-publish-dialog__heading">
          <span className="market-publish-dialog__icon"><Icons.Upload /></span>
          <div>
            <h4>发布拓展</h4>
            <p>将能力发布到拓展市场，供他人安装使用</p>
          </div>
        </div>
        <button type="button" className="icon-button" aria-label="关闭" onClick={onClose}><Icons.Close /></button>
      </header>

      <div className="market-publish-dialog__body">
        {/* 类型 tab 切换：Skill / MCP */}
        <div className="publish-kind-tabs" role="tablist" aria-label="拓展类型">
          <button type="button" role="tab" aria-selected={isSkill} className="publish-kind-tab" data-active={isSkill} onClick={() => switchKind("skill")}>
            <span className="publish-kind-tab__icon publish-kind-tab__icon--skill"><Icons.Sparkles /></span>
            <span className="publish-kind-tab__meta"><strong>Skill</strong><small>可复用的技能包</small></span>
          </button>
          <button type="button" role="tab" aria-selected={!isSkill} className="publish-kind-tab" data-active={!isSkill} onClick={() => switchKind("mcp")}>
            <span className="publish-kind-tab__icon publish-kind-tab__icon--mcp"><Icons.Braces /></span>
            <span className="publish-kind-tab__meta"><strong>MCP</strong><small>外部工具服务</small></span>
          </button>
        </div>

        {isSkill ? <>
          {/* 文件选择区：内容随类型切换 */}
          <label className={`publish-dropzone ${publishForm.artifact ? "publish-dropzone--filled" : ""}`}>
            {publishForm.artifact ? <>
              <span className="publish-dropzone__file"><Icons.Check /><span className="publish-dropzone__filename">{publishForm.artifact.name}</span></span>
              <small>点击重新选择</small>
            </> : <>
              <span className="publish-dropzone__icon"><Icons.CloudArrow /></span>
              <strong>选择技能包文件</strong>
              <small>支持 .zip 技能包或 SKILL.md 文件</small>
            </>}
            <input type="file" accept=".zip,.md" onChange={(event) => setPublishForm({ ...publishForm, artifact: event.target.files?.[0] })} />
          </label>

          <div className="publish-fields">
            <label>名称<input placeholder="拓展名称" value={publishForm.name} onChange={(event) => setPublishForm({ ...publishForm, name: event.target.value })} /></label>
            <label>版本<input placeholder="1.0.0" value={publishForm.version} onChange={(event) => setPublishForm({ ...publishForm, version: event.target.value })} /></label>
            <label className="full-width">介绍<textarea rows={3} placeholder="这个技能能做什么？什么时候会用到？" value={publishForm.description} onChange={(event) => setPublishForm({ ...publishForm, description: event.target.value })} /></label>
          </div>
        </> : <>
          {/* MCP 发布：与“添加 MCP”完全一致的表单（凭据由安装者配置，不随制品发布） */}
          <McpForm form={mcpForm} setForm={setMcpForm} allowSecrets={false} />
          <p className="publish-hint">商店拓展不能包含环境变量、请求头等凭据，安装者安装后可自行配置。</p>
        </>}

        {error ? <p className="market-dialog__error">{error}</p> : null}
      </div>

      <footer className="market-publish-dialog__footer">
        {isSkill
          ? <p>发布后将对市场中的所有用户可见，请勿包含敏感信息。</p>
          : <p>发布后将对市场中的所有用户可见，凭据由安装者自行配置。</p>}
        {isSkill ? <button type="button" className="market-publish-dialog__submit" disabled={!canSubmit || busy} onClick={() => void submit()}>{busy ? "发布中..." : "发布"}</button> : <div className="publish-footer__actions">
          <label className="publish-version">版本<input placeholder="1.0.0" value={publishForm.version} onChange={(event) => setPublishForm({ ...publishForm, version: event.target.value })} /></label>
          <button type="button" className="market-publish-dialog__submit" disabled={!canSubmit || busy} onClick={() => void submit()}>{busy ? "发布中..." : "发布"}</button>
        </div>}
      </footer>
    </section>
  </div>;
}
