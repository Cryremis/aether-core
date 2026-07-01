import { useState } from "react";

type SkillUploadModalProps = {
  visible: boolean;
  busy: boolean;
  error: string;
  platformName: string;
  onClose: () => void;
  onUpload: (file: File) => Promise<void>;
  onUploadFolder: (files: FileList | null) => Promise<void>;
};

export function SkillUploadModal({
  visible,
  busy,
  error,
  platformName,
  onClose,
  onUpload,
  onUploadFolder,
}: SkillUploadModalProps) {
  const [selectedFileName, setSelectedFileName] = useState("");
  const [selectedFolderSummary, setSelectedFolderSummary] = useState("");

  if (!visible) return null;

  const handleFileSelected = async (file: File | undefined) => {
    if (!file) return;
    setSelectedFileName(file.name);
    setSelectedFolderSummary("");
    await onUpload(file);
  };

  const handleFolderSelected = async (files: FileList | null) => {
    if (!files?.length) return;
    const names = Array.from(files)
      .map((file) => file.webkitRelativePath || file.name)
      .filter(Boolean);
    const rootName = names[0]?.split("/")[0] ?? "skill-folder";
    setSelectedFileName("");
    setSelectedFolderSummary(`${rootName} · ${files.length} 个文件`);
    await onUploadFolder(files);
  };

  return (
    <div className="skill-upload-modal-backdrop" onClick={onClose}>
      <section className="skill-upload-modal" onClick={(event) => event.stopPropagation()}>
        <header className="skill-upload-modal__header">
          <div>
            <h4>上传技能</h4>
            <p>当前平台：{platformName}</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        <div className="skill-upload-modal__body">
          <p className="skill-upload-modal__intro">
            支持三种导入方式：直接上传 `.zip` 技能包、上传单个 `SKILL.md`，或选择本地技能文件夹并自动整理为标准结构。
          </p>

          <div className="skill-upload-modal__notes">
            <p>技能格式规范：</p>
            <p>1. 推荐目录结构：`skill-name/SKILL.md` 或 `skills/skill-name/SKILL.md`</p>
            <p>2. `SKILL.md` 必须是 UTF-8 文本，建议包含 `name`、`description`</p>
            <p>3. 资源文件可放在同级子目录（如 `references/`），不能游离在技能目录之外</p>
          </div>

          <div className="skill-upload-modal__actions-grid">
            <label className={`fm-btn primary ${busy ? "is-disabled" : ""}`}>
              <span>{busy ? "上传中..." : "上传 zip / SKILL.md"}</span>
              <input
                type="file"
                accept=".zip,.md"
                disabled={busy}
                onChange={(event) => {
                  void handleFileSelected(event.target.files?.[0]);
                  event.currentTarget.value = "";
                }}
              />
            </label>

            <label className={`fm-btn outline ${busy ? "is-disabled" : ""}`}>
              <span>选择技能文件夹</span>
              <input
                type="file"
                multiple
                disabled={busy}
                onChange={(event) => {
                  void handleFolderSelected(event.target.files);
                  event.currentTarget.value = "";
                }}
                {...({ webkitdirectory: "true", directory: "" } as Record<string, string>)}
              />
            </label>
          </div>

          {selectedFileName ? <p className="skill-upload-modal__filename">已选择：{selectedFileName}</p> : null}
          {selectedFolderSummary ? <p className="skill-upload-modal__filename">已选择文件夹：{selectedFolderSummary}</p> : null}
          {error ? <p className="skill-upload-modal__error">{error}</p> : null}
        </div>
      </section>
    </div>
  );
}
