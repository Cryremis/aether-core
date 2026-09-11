import { useMemo } from "react";
import { createPortal } from "react-dom";
import { useParams } from "react-router-dom";

import { BaselineContextMenu } from "../../components/admin/BaselineContextMenu";
import { BaselineManager } from "../../components/admin/BaselineManager";
import { BaselineMoveModal } from "../../components/admin/BaselineMoveModal";
import { SkillUploadModal } from "../../components/admin/SkillUploadModal";
import { useAppPreferences } from "../../i18n";
import { TabPageShell, usePlatformDetail } from "./TabPageShell";
import { usePlatformBaseline } from "./hooks";

export default function PlatformBaselinePage() {
  const { t } = useAppPreferences();
  const params = useParams();
  const platformId = Number(params.platformId);
  const { platform } = usePlatformDetail();
  const baseline = usePlatformBaseline(Number.isFinite(platformId) ? platformId : null);

  // 面包屑解析
  const breadcrumbs = useMemo(() => {
    if (!baseline.currentDirectory) return [];
    const parts = baseline.currentDirectory.split("/");
    return parts.map((part, index) => ({ name: part, path: parts.slice(0, index + 1).join("/") }));
  }, [baseline.currentDirectory]);

  // 当前目录内容（仅一层）
  const currentDirectoryChildren = useMemo(() => {
    const prefix = baseline.currentDirectory ? `${baseline.currentDirectory}/` : "";
    return baseline.entries.filter((item) => {
      if (!baseline.currentDirectory) return !item.relative_path.includes("/");
      if (item.relative_path === baseline.currentDirectory) return false;
      if (!item.relative_path.startsWith(prefix)) return false;
      const rest = item.relative_path.slice(prefix.length);
      return !rest.includes("/");
    }).sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === "directory" ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  }, [baseline.entries, baseline.currentDirectory]);

  if (!platform) return <TabPageShell><div className="platform-settings-loading">{t("common.loading")}</div></TabPageShell>;

  return (
    <>
      <TabPageShell>
        <BaselineManager
          activePlatform={platform}
          baselineError={baseline.error}
          fileManagerRef={baseline.fileManagerRef}
          breadcrumbs={breadcrumbs}
          currentDirectoryChildren={currentDirectoryChildren}
          currentBaselineDirectory={baseline.currentDirectory}
          selectedBaselinePath={baseline.selectedPath}
          selectedBaselineContent={baseline.selectedContent}
          selectedBaselineMediaType={baseline.selectedMediaType}
          selectedBaselineTruncated={baseline.selectedTruncated}
          baselineDirty={baseline.dirty}
          onGoHome={() => { baseline.setCurrentDirectory(""); baseline.setSelectedPath(""); }}
          onGoBreadcrumb={(path) => { baseline.setCurrentDirectory(path); baseline.setSelectedPath(""); }}
          onCreateDirectory={() => void baseline.handleCreateDirectory()}
          onCreateFile={() => void baseline.handleCreateFile()}
          onUploadFile={(file) => void baseline.handleUploadFile(file)}
          onUploadFolder={(files) => void baseline.handleUploadFolder(files)}
          onOpenSkillUpload={() => { baseline.setSkillUploadError(""); baseline.setSkillUploadVisible(true); }}
          onSelectFile={(item) => void baseline.handleSelectFile(item)}
          onDoubleClickItem={baseline.handleDoubleClickItem}
          onContextMenu={baseline.handleContextMenu}
          onContentChange={baseline.handleContentChange}
          onSaveText={() => void baseline.handleSaveText()}
          onClosePreview={() => { baseline.setSelectedPath(""); }}
        />
      </TabPageShell>

      {/* 弹窗与右键菜单挂到 body：玻璃卡的 backdrop-filter 会困住 fixed 定位的后代 */}
      {createPortal(
        <>
          <BaselineContextMenu
            contextMenu={baseline.contextMenu}
            onOpenDirectory={() => { baseline.handleDoubleClickItem(baseline.contextMenu.item!); }}
            onEditFile={() => { void baseline.handleSelectFile(baseline.contextMenu.item!); }}
            onDownloadFile={() => { void baseline.handleDownload(baseline.contextMenu.item!.relative_path, baseline.contextMenu.item!.name); }}
            onMove={() => { baseline.openMoveModal(baseline.contextMenu.item!.relative_path, "move"); }}
            onRename={() => { baseline.openMoveModal(baseline.contextMenu.item!.relative_path, "rename"); }}
            onDelete={() => { void baseline.handleDelete(baseline.contextMenu.item!.relative_path); }}
          />
          <SkillUploadModal
            visible={baseline.skillUploadVisible}
            busy={baseline.skillUploadBusy}
            error={baseline.skillUploadError}
            platformName={platform.display_name}
            onClose={() => baseline.setSkillUploadVisible(false)}
            onUpload={(file) => baseline.handleSkillUpload(file)}
            onUploadFolder={(files) => baseline.handleSkillFolderUpload(files)}
          />
          <BaselineMoveModal
            visible={Boolean(baseline.moveState.sourcePath)}
            busy={baseline.moveBusy}
            error={baseline.moveError}
            mode={baseline.moveState.mode}
            sourcePath={baseline.moveState.sourcePath}
            targetPath={baseline.moveState.targetPath}
            onTargetPathChange={(value) => baseline.setMoveState({ ...baseline.moveState, targetPath: value })}
            onClose={baseline.closeMoveModal}
            onSubmit={() => void baseline.handleMove()}
          />
        </>,
        document.body,
      )}
    </>
  );
}
