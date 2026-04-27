import { useState } from "react";

type SkillUploadModalProps = {
  visible: boolean;
  busy: boolean;
  error: string;
  platformName: string;
  onClose: () => void;
  onUpload: (file: File) => Promise<void>;
};

export function SkillUploadModal({
  visible,
  busy,
  error,
  platformName,
  onClose,
  onUpload,
}: SkillUploadModalProps) {
  const [selectedFileName, setSelectedFileName] = useState("");

  if (!visible) return null;

  const handleFileSelected = async (file: File | undefined) => {
    if (!file) return;
    setSelectedFileName(file.name);
    await onUpload(file);
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
            这里支持上传 `.zip` 或 `SKILL.md`。上传 zip 后会自动解压，并写入平台基线的 `skills` 目录。
          </p>

          <div className="skill-upload-modal__notes">
            <p>技能格式规范：</p>
            <p>1. 推荐目录结构：`skills/&lt;skill-name&gt;/SKILL.md`</p>
            <p>2. `SKILL.md` 需要包含技能说明，建议有 name、description</p>
            <p>3. 资源文件可放在技能目录下的子目录（如 `references/`）</p>
          </div>

          <label className={`fm-btn primary ${busy ? "is-disabled" : ""}`}>
            <span>{busy ? "上传中..." : "选择技能文件并上传"}</span>
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

          {selectedFileName ? <p className="skill-upload-modal__filename">已选择：{selectedFileName}</p> : null}
          {error ? <p className="skill-upload-modal__error">{error}</p> : null}
        </div>
      </section>
    </div>
  );
}
