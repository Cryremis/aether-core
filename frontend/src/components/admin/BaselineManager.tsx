import type { RefObject } from "react";

import { AdminIcons as Icons } from "./AdminIcons";
import type { PlatformBaselineEntryItem, PlatformItem } from "./types";

type BaselineManagerProps = {
  activePlatform: PlatformItem;
  baselineError: string;
  fileManagerRef: RefObject<HTMLDivElement | null>;
  breadcrumbs: Array<{ name: string; path: string }>;
  currentDirectoryChildren: PlatformBaselineEntryItem[];
  currentBaselineDirectory: string;
  selectedBaselinePath: string;
  selectedBaselineContent: string;
  selectedBaselineMediaType: string;
  selectedBaselineTruncated: boolean;
  baselineDirty: boolean;
  onGoHome: () => void;
  onGoBreadcrumb: (path: string) => void;
  onCreateDirectory: () => void;
  onCreateFile: () => void;
  onUploadFile: (file: File | undefined) => void;
  onSelectFile: (item: PlatformBaselineEntryItem) => void;
  onDoubleClickItem: (item: PlatformBaselineEntryItem) => void;
  onContextMenu: (event: React.MouseEvent, item: PlatformBaselineEntryItem) => void;
  onContentChange: (value: string) => void;
  onSaveText: () => void;
  onClosePreview: () => void;
};

export function BaselineManager(props: BaselineManagerProps) {
  return (
    <>
      <div className="manager-header">
        <div className="manager-header__info">
          <h4>基线资源管理器</h4>
          <p>当前平台：{props.activePlatform.display_name}。预置文件将在新会话创建时注入到沙箱。</p>
        </div>
        {props.baselineError ? <div className="baseline-error-toast">{props.baselineError}</div> : null}
      </div>

      <div className="file-manager-container" ref={props.fileManagerRef}>
        <div className="fm-toolbar">
          <div className="fm-breadcrumbs">
            <button className="crumb-btn home-crumb" onClick={props.onGoHome}>
              {props.activePlatform.platform_key}
            </button>
            {props.breadcrumbs.map((crumb) => (
              <div key={crumb.path} className="crumb-item">
                <Icons.ChevronRight />
                <button className="crumb-btn" onClick={() => props.onGoBreadcrumb(crumb.path)}>
                  {crumb.name}
                </button>
              </div>
            ))}
          </div>

          <div className="fm-actions">
            <button className="fm-btn outline" onClick={props.onCreateDirectory} title="新建文件夹">
              <Icons.FolderPlus /> <span>新建目录</span>
            </button>
            <button className="fm-btn outline" onClick={props.onCreateFile} title="新建文本文件">
              <Icons.FilePlus /> <span>新建文件</span>
            </button>
            <label className="fm-btn primary" title="上传文件到当前目录">
              <Icons.Upload /> <span>上传</span>
              <input type="file" onChange={(e) => { props.onUploadFile(e.target.files?.[0]); e.currentTarget.value = ""; }} />
            </label>
          </div>
        </div>

        <div className="fm-split-view">
          <div className="fm-explorer" onContextMenu={(e) => e.preventDefault()}>
            {props.currentDirectoryChildren.length === 0 ? (
              <div className="fm-empty-state">当前目录为空</div>
            ) : (
              <div className="fm-grid">
                {props.currentDirectoryChildren.map((item) => (
                  <div
                    key={item.relative_path}
                    className={`fm-item ${props.selectedBaselinePath === item.relative_path ? "is-selected" : ""}`}
                    onClick={() => item.kind === "file" && props.onSelectFile(item)}
                    onDoubleClick={() => props.onDoubleClickItem(item)}
                    onContextMenu={(e) => props.onContextMenu(e, item)}
                  >
                    <div className="fm-item__icon">
                      {item.kind === "directory" ? <Icons.Folder /> : <Icons.File />}
                    </div>
                    <span className="fm-item__name" title={item.name}>{item.name}</span>
                    {item.kind === "file" ? <span className="fm-item__meta">{(item.size / 1024).toFixed(1)} KB</span> : null}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className={`fm-editor-drawer ${props.selectedBaselinePath ? "is-open" : ""}`}>
            {props.selectedBaselinePath ? (
              <>
                <div className="fm-editor__header">
                  <div className="fm-editor__title">
                    <strong>{props.selectedBaselinePath.split("/").pop()}</strong>
                    <span>{props.selectedBaselineMediaType}{props.selectedBaselineTruncated ? " (已截断)" : ""}</span>
                  </div>
                  <div className="fm-editor__actions">
                    <button className="fm-btn primary small" onClick={props.onSaveText} disabled={!props.baselineDirty}>
                      保存修改
                    </button>
                    <button className="fm-btn outline small icon-only" onClick={props.onClosePreview} title="关闭预览">
                      &times;
                    </button>
                  </div>
                </div>
                <textarea
                  className="fm-editor__textarea"
                  value={props.selectedBaselineContent}
                  onChange={(e) => props.onContentChange(e.target.value)}
                  spellCheck={false}
                />
              </>
            ) : (
              <div className="fm-editor__placeholder">
                <Icons.File />
                <p>选择一个文件进行预览或编辑</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
