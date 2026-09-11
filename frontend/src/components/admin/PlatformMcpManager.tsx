import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { deleteMcp, installExtension, listExtensions, listPlatformCapabilities, saveMcp, type ExtensionEntry, type McpCapability } from "../../api/client";
import { WorkbenchIcons as Icons } from "../workbench/WorkbenchIcons";

const emptyForm = { name: "", description: "", transport: "streamable_http", url: "", command: "" };

export function PlatformMcpManager({ platformId }: { platformId: number }) {
  const [items, setItems] = useState<McpCapability[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [storeOpen, setStoreOpen] = useState(false);
  const [storeLoading, setStoreLoading] = useState(false);
  const [extensions, setExtensions] = useState<ExtensionEntry[]>([]);
  const [installingId, setInstallingId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [form, setForm] = useState(emptyForm);
  const [busy, setBusy] = useState(false);

  const refresh = async () => setItems((await listPlatformCapabilities(platformId)).data.mcp || []);

  useEffect(() => { void refresh().catch(() => undefined); }, [platformId]);

  const openAdd = () => {
    setError("");
    setForm(emptyForm);
    setAddOpen(true);
  };

  const submit = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await saveMcp(form.transport === "stdio"
        ? { ...form, command: form.command.trim().split(/\s+/).filter(Boolean), url: null }
        : { ...form, command: [], url: form.url.trim() }, "platform", { platformId });
      setAddOpen(false);
      setForm(emptyForm);
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败"); }
    finally { setBusy(false); }
  };

  const openStore = async () => {
    setError("");
    setStoreLoading(true);
    setStoreOpen(true);
    try {
      setExtensions((await listExtensions()).data || []);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "商店加载失败");
      setStoreOpen(false);
    } finally {
      setStoreLoading(false);
    }
  };

  const install = async (item: ExtensionEntry) => {
    if (installingId) return;
    setInstallingId(item.entry_id);
    setError("");
    try {
      await installExtension(item.entry_id, "platform", { platformId });
      await refresh();
      setStoreOpen(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "安装失败"); }
    finally { setInstallingId(null); }
  };

  return <section className="platform-mcp-manager">
    <div className="manager-header">
      <div className="manager-header__info">
        <h4>平台 MCP</h4>
        <p>平台级 MCP 会提供给该平台下的所有会话，用户仍可在能力页自行关闭。</p>
      </div>
      <div className="flex-row">
        <button type="button" className="fm-btn outline" onClick={() => void openStore()}><Icons.Store /><span>拓展商店</span></button>
        <button type="button" className="fm-btn primary" onClick={openAdd}><Icons.Plus /><span>添加 MCP</span></button>
      </div>
    </div>

    {error && !addOpen && !storeOpen ? <div className="baseline-error-toast">{error}</div> : null}

    {items.length === 0 ? <div className="empty-state">暂无平台 MCP，可从拓展商店加载或手动添加</div> : null}
    {items.length ? <div className="item-list">{items.map((item) => (
      <article key={item.id} className="resource-card block">
        <div className="flex-row">
          <strong>{item.name}</strong>
          <span className="badge">{item.transport}</span>
          <button type="button" className="action-button small" onClick={() => void deleteMcp(item.name, "platform", { platformId }).then(refresh).catch((reason) => setError(reason.message))}>删除</button>
        </div>
        <p className="desc">{item.description || item.url || (item.command || []).join(" ")}</p>
      </article>
    ))}</div> : null}

    {/* 添加 MCP：与聊天界面能力弹窗同构（portal 到 body，避免被玻璃卡遮挡） */}
    {addOpen ? createPortal(
      <div className="capability-add-dialog__backdrop" onClick={() => setAddOpen(false)}>
        <section className="capability-add-dialog" role="dialog" aria-modal="true" aria-label="添加平台 MCP" onClick={(event) => event.stopPropagation()}>
          <header className="capability-add-dialog__header">
            <div className="capability-add-dialog__heading">
              <span className="capability-add-dialog__icon capability-add-dialog__icon--mcp"><Icons.Braces /></span>
              <div>
                <h4>添加平台 MCP</h4>
                <p>接入外部 MCP 工具服务，对该平台所有会话生效</p>
              </div>
            </div>
            <button type="button" className="icon-button" aria-label="关闭" onClick={() => setAddOpen(false)}><Icons.Close /></button>
          </header>
          <div className="capability-add-dialog__body">
            <div className="form-grid capability-form">
              <label>名称<input placeholder="server 名称" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
              <label>说明<input placeholder="一句话说明用途" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></label>
              <label>传输
                <select value={form.transport} onChange={(event) => setForm({ ...form, transport: event.target.value })}>
                  <option value="streamable_http">Streamable HTTP</option>
                  <option value="stdio">STDIO</option>
                </select>
              </label>
              {form.transport === "stdio"
                ? <label>命令<input placeholder="npx -y server" value={form.command} onChange={(event) => setForm({ ...form, command: event.target.value })} /></label>
                : <label>HTTPS URL<input placeholder="https://example.com/mcp" value={form.url} onChange={(event) => setForm({ ...form, url: event.target.value })} /></label>}
            </div>
            {error ? <p className="capability-add-dialog__error">{error}</p> : null}
          </div>
          <footer className="capability-add-dialog__footer">
            <p>保存后可在下方列表管理，删除即时生效。</p>
            <button type="button" className="capability-add-dialog__submit" disabled={busy || !form.name.trim()} onClick={() => void submit()}>{busy ? "保存中..." : "保存"}</button>
          </footer>
        </section>
      </div>, document.body) : null}

    {/* 拓展商店：与聊天界面市场弹窗同构 */}
    {storeOpen ? createPortal(
      <div className="market-dialog__backdrop" onClick={() => setStoreOpen(false)}>
        <section className="market-dialog" role="dialog" aria-modal="true" aria-label="拓展商店" onClick={(event) => event.stopPropagation()}>
          <header className="market-dialog__header">
            <div className="market-dialog__heading">
              <span className="market-dialog__icon"><Icons.Store /></span>
              <div>
                <h4>拓展商店</h4>
                <p>{storeLoading ? "正在加载拓展…" : extensions.length > 0 ? `${extensions.length} 个拓展 · 加载后对本平台所有会话生效` : "暂无可加载的拓展"}</p>
              </div>
            </div>
            <button type="button" className="icon-button" aria-label="关闭" onClick={() => setStoreOpen(false)}><Icons.Close /></button>
          </header>
          <div className="market-dialog__body">
            {error ? <p className="market-dialog__error">{error}</p> : null}
            {storeLoading ? <div className="market-grid">
              {[0, 1, 2, 3].map((index) => <div className="market-card market-card--skeleton" key={index} />)}
            </div> : extensions.length === 0 ? <div className="market-empty">
              <span className="market-empty__icon"><Icons.Store /></span>
              <strong>商店还是空的</strong>
              <p>去聊天界面的拓展市场发布第一个拓展吧</p>
            </div> : <div className="market-grid">
              {extensions.map((item) => (
                <article className={`market-card market-card--${item.kind}`} key={item.entry_id}>
                  <div className="market-card__head">
                    <span className="market-card__icon">{item.kind === "skill" ? <Icons.Sparkles /> : <Icons.Braces />}</span>
                    <strong className="market-card__name" title={item.name}>{item.name}</strong>
                    <span className="market-card__version">v{item.current_version}</span>
                  </div>
                  <p className="market-card__desc">{item.description || "暂无介绍"}</p>
                  <div className="market-card__meta">
                    <span title="提交人"><Icons.User />{item.submitter_name}</span>
                    <span title="使用次数"><Icons.Zap />{item.usage_count} 次使用</span>
                  </div>
                  <div className="market-card__foot">
                    <span className="badge">{item.kind}</span>
                    <button type="button" className="market-install" disabled={installingId === item.entry_id} onClick={() => void install(item)}>
                      {installingId === item.entry_id ? "加载中..." : "加载到平台"}
                    </button>
                  </div>
                </article>
              ))}
            </div>}
          </div>
        </section>
      </div>, document.body) : null}
  </section>;
}
