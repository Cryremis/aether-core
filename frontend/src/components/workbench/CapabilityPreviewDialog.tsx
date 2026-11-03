import { useEffect, useMemo, useRef, useState } from "react";
import { Marked } from "marked";

import { getExtensionPreview, getSkillPreview, installExtension, type CapabilityPreview, type ExtensionEntry } from "../../api/client";
import type { SkillItem } from "../../pages/workbench/types";
import { WorkbenchIcons as Icons } from "./WorkbenchIcons";

export type PreviewTarget = { skill: SkillItem } | { extension: ExtensionEntry };

const sourceLabels: Record<string, string> = { built_in: "内置", host: "宿主注入", platform: "平台预置", user: "个人安装", upload: "会话上传" };
const previewMarkdown = new Marked({ gfm: true, renderer: {
  html: () => "",
  image: () => "",
  link({ tokens }) { return this.parser.parseInline(tokens); },
} });

type Tree = Map<string, { path: string; children?: Tree }>;

function FileTree({ nodes, selected, onSelect }: { nodes: Tree; selected: string; onSelect: (path: string) => void }) {
  return <ul className="skill-preview__tree">
    {[...nodes].map(([name, node]) => <li key={node.path}>
      {node.children ? <details open>
        <summary title={node.path}><Icons.ChevronRight /> <span>{name}</span></summary>
        <FileTree nodes={node.children} selected={selected} onSelect={onSelect} />
      </details> : <button type="button" title={node.path} aria-current={selected === node.path ? "true" : undefined} onClick={() => onSelect(node.path)}>
        <Icons.File /> <span>{name}</span>
      </button>}
    </li>)}
  </ul>;
}

export function CapabilityPreviewDialog({ target, sessionId, platformId, onClose, onInstalled }: {
  target: PreviewTarget;
  sessionId?: string;
  platformId?: number;
  onClose: () => void;
  onInstalled: () => Promise<void>;
}) {
  const extension = "extension" in target ? target.extension : null;
  const skill = "skill" in target ? target.skill : null;
  const item = extension ?? skill!;
  const [version, setVersion] = useState(extension?.current_version ?? "");
  const [scope, setScope] = useState<"session" | "user">("session");
  const [detail, setDetail] = useState<CapabilityPreview | null>(null);
  const [path, setPath] = useState("");
  const [file, setFile] = useState<CapabilityPreview["file"]>();
  const [raw, setRaw] = useState(false);
  const [error, setError] = useState("");
  const [fileError, setFileError] = useState("");
  const [retry, setRetry] = useState(0);
  const [fileRetry, setFileRetry] = useState(0);
  const [installing, setInstalling] = useState(false);
  const [installMessage, setInstallMessage] = useState("");
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);

  const request = (signal: AbortSignal, filePath?: string) => extension
    ? getExtensionPreview(extension.entry_id, { sessionId, platformId, version, filePath }, signal)
    : getSkillPreview(sessionId!, skill!.name, skill!.source, filePath, signal);

  useEffect(() => {
    const controller = new AbortController();
    setDetail(null); setPath(""); setFile(undefined); setError(""); setFileError("");
    void request(controller.signal).then(({ data }) => {
      if (controller.signal.aborted) return;
      setDetail(data);
      setPath(data.file?.path ?? "");
      setFile(data.file);
    }).catch((reason) => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [sessionId, platformId, extension?.entry_id, skill?.name, skill?.source, version, retry]);

  useEffect(() => {
    if (!path || path === file?.path || (extension && detail?.version !== version)) return;
    const controller = new AbortController();
    void request(controller.signal, path).then(({ data }) => {
      if (!controller.signal.aborted) setFile(data.file);
    }).catch((reason) => { if (!controller.signal.aborted) setFileError(reason.message); });
    return () => controller.abort();
  }, [path, version, fileRetry]);

  const tree = useMemo(() => {
    const root: Tree = new Map();
    for (const entry of detail?.files ?? []) {
      const parts = entry.path.split("/");
      let nodes = root;
      parts.forEach((name, index) => {
        if (index === parts.length - 1) nodes.set(name, { path: entry.path });
        else {
          const children = nodes.get(name)?.children ?? new Map();
          nodes.set(name, { path: parts.slice(0, index + 1).join("/"), children });
          nodes = children;
        }
      });
    }
    return root;
  }, [detail]);

  const currentFile = file?.path === path ? file : undefined;
  const markdown = /\.md$/i.test(path);
  const html = useMemo(() => markdown && !raw && currentFile?.text
    ? previewMarkdown.parse(currentFile.text.replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, "")) as string : "",
  [currentFile?.text, markdown, raw]);
  const versions = detail?.versions ?? [];
  const author = detail?.submitter_name || extension?.submitter_name;
  const account = detail?.submitter_account || extension?.submitter_account;
  const install = async () => {
    if (!extension || !detail?.version || detail.version !== version || installing) return;
    setInstalling(true); setInstallMessage("");
    try {
      await installExtension(extension.entry_id, platformId ? "platform" : scope, { sessionId, platformId, version });
      setInstallMessage(`已安装 v${version}`);
      await onInstalled();
    } catch (reason) { setInstallMessage(reason instanceof Error ? reason.message : "安装失败"); }
    finally { setInstalling(false); }
  };

  return <dialog ref={dialogRef} className={`market-dialog skill-preview${extension?.kind === "mcp" ? " skill-preview--mcp" : ""}`} aria-label={`${item.name} 详情`}
    onCancel={(event) => { event.preventDefault(); event.stopPropagation(); onClose(); }}
    onClick={(event) => { if (event.target === event.currentTarget) {
      const bounds = event.currentTarget.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose();
    } }}>
    <header className="market-dialog__header">
      <div className="market-dialog__heading">
        <span className="market-dialog__icon">{extension?.kind === "mcp" ? <Icons.Braces /> : <Icons.Sparkles />}</span>
        <div><h4>{item.name}</h4><p>{skill ? sourceLabels[skill.source] ?? skill.source : `${extension!.kind === "skill" ? "Skill" : "MCP"} · ${author ?? "平台用户"}${account && account !== author ? `（${account}）` : ""}`}</p></div>
      </div>
      <button type="button" className="icon-button" aria-label="关闭详情" onClick={onClose} autoFocus><Icons.Close /></button>
    </header>
    <div className="skill-preview__meta">
      <p>{item.description || "暂无介绍"}</p>
      {extension ? <label>版本<select value={version} disabled={installing} onChange={(event) => {
        setRaw(false); setInstallMessage(""); setVersion(event.target.value);
      }}>
        {[...new Set([extension.current_version, version, ...versions.map((entry) => entry.version)])].map((value) => <option key={value} value={value}>v{value}</option>)}
      </select></label> : null}
    </div>
    {error ? <div className="skill-preview__state" role="alert"><p>{error}</p><button type="button" className="action-button small" onClick={() => setRetry(retry + 1)}>重试</button></div>
      : !detail ? <div className="skill-preview__state" role="status">正在加载详情…</div>
      : detail.mcp ? <dl className="skill-preview__mcp">
        <dt>连接方式</dt><dd>{detail.mcp.transport}</dd>
        <dt>授权</dt><dd>{detail.mcp.auth === "oauth" ? "OAuth" : "无"}</dd>
        {detail.mcp.hostname ? <><dt>服务器</dt><dd>{detail.mcp.hostname}</dd></> : null}
      </dl> : <div className="skill-preview__body">
        <nav className="skill-preview__files" aria-label="技能文件">
          <strong>包内文件 <small>{detail.files?.length ?? 0}</small></strong>
          <FileTree nodes={tree} selected={path} onSelect={(value) => { setRaw(false); setFileError(""); setPath(value); }} />
        </nav>
        <div className="skill-preview__viewer">
          <div className="skill-preview__filebar"><span title={path}>{path}</span>{markdown ? <div className="market-scope" role="group" aria-label="显示方式">
            <button type="button" data-active={!raw} onClick={() => setRaw(false)}>预览</button>
            <button type="button" data-active={raw} onClick={() => setRaw(true)}>原文</button>
          </div> : null}</div>
          <div className="skill-preview__content" key={path}>
            {fileError ? <div role="alert" className="skill-preview__state"><p>{fileError}</p><button type="button" className="action-button small" onClick={() => { setFileError(""); setFileRetry(fileRetry + 1); }}>重试</button></div>
              : !currentFile ? <div role="status" className="skill-preview__state">正在读取文件…</div>
              : currentFile.text === null ? <div className="skill-preview__state">此文件为二进制或非 UTF-8 文本。</div>
              : <>{currentFile.truncated ? <p className="publish-hint">只展示前 128 KiB</p> : null}
                {markdown && !raw ? <div className="markdown-body" dangerouslySetInnerHTML={{ __html: html }} /> : <pre>{currentFile.text}</pre>}
              </>}
          </div>
        </div>
      </div>}
    <footer className="skill-preview__footer">
      <span role="status">{extension ? installMessage || (extension.maintenance_policy === "platform_members" ? "共同维护" : "作者维护")
        : detail?.allowed_tools?.length ? `声明工具 · ${detail.allowed_tools.join("、")}` : "技能预览"}</span>
      {extension ? <div className="skill-preview__install">
        {platformId ? <span>平台预置</span> : <div className="market-scope" role="group" aria-label="安装范围">
          <button type="button" data-active={scope === "session"} disabled={installing} onClick={() => setScope("session")}>当前会话</button>
          <button type="button" data-active={scope === "user"} disabled={installing} onClick={() => setScope("user")}>个人空间</button>
        </div>}
        <button type="button" className="market-install" disabled={installing || detail?.version !== version} onClick={() => void install()}>{installing ? "安装中…" : `安装 v${version}`}</button>
      </div> : null}
    </footer>
  </dialog>;
}
