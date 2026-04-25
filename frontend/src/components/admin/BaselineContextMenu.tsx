import { AdminIcons as Icons } from "./AdminIcons";
import type { PlatformBaselineEntryItem } from "./types";

type BaselineContextMenuProps = {
  contextMenu: {
    visible: boolean;
    x: number;
    y: number;
    item: PlatformBaselineEntryItem | null;
  };
  onOpenDirectory: () => void;
  onEditFile: () => void;
  onDownloadFile: () => void;
  onRename: () => void;
  onDelete: () => void;
};

export function BaselineContextMenu({
  contextMenu,
  onOpenDirectory,
  onEditFile,
  onDownloadFile,
  onRename,
  onDelete,
}: BaselineContextMenuProps) {
  if (!(contextMenu.visible && contextMenu.item)) return null;

  return (
    <div className="fm-context-menu" style={{ top: contextMenu.y, left: contextMenu.x }} onClick={(e) => e.stopPropagation()}>
      <div className="context-menu__header">{contextMenu.item.name}</div>
      {contextMenu.item.kind === "directory" ? (
        <button className="context-menu__item" onClick={onOpenDirectory}>
          <Icons.Folder /> 打开目录
        </button>
      ) : (
        <>
          <button className="context-menu__item" onClick={onEditFile}>
            <Icons.Edit2 /> 预览 / 编辑
          </button>
          <button className="context-menu__item" onClick={onDownloadFile}>
            <Icons.Download /> 下载文件
          </button>
        </>
      )}
      <div className="context-menu__divider" />
      <button className="context-menu__item" onClick={onRename}>
        <Icons.Edit2 /> 重命名
      </button>
      <button className="context-menu__item danger" onClick={onDelete}>
        <Icons.Trash2 /> 删除
      </button>
    </div>
  );
}
