import { useEffect, useState } from "react";

import { deleteMcp, installExtension, listExtensions, listPlatformCapabilities, saveMcp, type ExtensionEntry, type McpCapability } from "../../api/client";

export function PlatformMcpManager({ platformId }: { platformId: number }) {
  const [items, setItems] = useState<McpCapability[]>([]);
  const [open, setOpen] = useState(false);
  const [storeOpen, setStoreOpen] = useState(false);
  const [extensions, setExtensions] = useState<ExtensionEntry[]>([]);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ name: "", description: "", transport: "streamable_http", url: "", command: "" });
  const refresh = async () => setItems((await listPlatformCapabilities(platformId)).data.mcp || []);
  useEffect(() => { void refresh().catch(() => undefined); }, [platformId]);
  const submit = async () => {
    setError("");
    try {
      await saveMcp(form.transport === "stdio" ? { ...form, command: form.command.trim().split(/\s+/), url: null } : { ...form, command: [] }, "platform", { platformId });
      setOpen(false); await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败"); }
  };
  const openStore = async () => {
    setError("");
    try { setExtensions((await listExtensions()).data || []); setStoreOpen(true); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "商店加载失败"); }
  };
  return <section className="platform-mcp-manager"><div className="manager-header"><div className="manager-header__info"><h4>平台 MCP</h4><p>平台级 MCP 会提供给该平台下的所有会话，用户仍可在能力页关闭。</p></div><div className="flex-row"><button className="fm-btn outline" onClick={() => void openStore()}>拓展商店</button><button className="fm-btn outline" onClick={() => setOpen(true)}>添加 MCP</button></div></div>{items.length ? <div className="item-list">{items.map((item) => <div className="resource-card" key={item.id}><strong>{item.name}</strong><span className="badge">{item.transport}</span><span>{item.description || item.url}</span><button className="fm-btn outline small" onClick={() => void deleteMcp(item.name, "platform", { platformId }).then(refresh).catch((reason) => setError(reason.message))}>删除</button></div>)}</div> : null}{open ? <div className="skill-upload-modal-backdrop" onClick={() => setOpen(false)}><section className="skill-upload-modal" onClick={(event) => event.stopPropagation()}><header className="skill-upload-modal__header"><h4>添加平台 MCP</h4><button className="icon-button" aria-label="关闭" onClick={() => setOpen(false)}>×</button></header><div className="skill-upload-modal__body form-grid"><label>名称<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label><label>说明<input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label><label>传输<select value={form.transport} onChange={(e) => setForm({ ...form, transport: e.target.value })}><option value="streamable_http">Streamable HTTP</option><option value="stdio">STDIO</option></select></label>{form.transport === "stdio" ? <label>命令<input value={form.command} onChange={(e) => setForm({ ...form, command: e.target.value })} /></label> : <label>HTTPS URL<input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} /></label>}<button className="fm-btn primary" onClick={() => void submit()}>保存</button>{error ? <p className="skill-upload-modal__error">{error}</p> : null}</div></section></div> : null}{storeOpen ? <div className="skill-upload-modal-backdrop" onClick={() => setStoreOpen(false)}><section className="skill-upload-modal capability-dialog" onClick={(event) => event.stopPropagation()}><header className="skill-upload-modal__header"><h4>加载到平台基线</h4><button className="icon-button" aria-label="关闭" onClick={() => setStoreOpen(false)}>×</button></header><div className="skill-upload-modal__body"><div className="item-list store-list">{extensions.map((item) => <article className="resource-card block" key={item.entry_id}><div className="flex-row"><strong>{item.name}</strong><span className="badge">{item.kind}</span></div><p>{item.description}</p><small>v{item.current_version} · {item.submitter_name} · 使用 {item.usage_count}</small><button className="fm-btn primary small" onClick={() => void installExtension(item.entry_id, "platform", { platformId }).then(async () => { await refresh(); setStoreOpen(false); }).catch((reason) => setError(reason.message))}>加载到平台</button></article>)}</div>{error ? <p className="skill-upload-modal__error">{error}</p> : null}</div></section></div> : null}</section>;
}
