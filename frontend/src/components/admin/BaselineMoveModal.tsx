type BaselineMoveModalProps = {
  visible: boolean;
  busy: boolean;
  error: string;
  mode: "move" | "rename";
  sourcePath: string;
  targetPath: string;
  onTargetPathChange: (value: string) => void;
  onClose: () => void;
  onSubmit: () => void;
};

export function BaselineMoveModal({
  visible,
  busy,
  error,
  mode,
  sourcePath,
  targetPath,
  onTargetPathChange,
  onClose,
  onSubmit,
}: BaselineMoveModalProps) {
  if (!visible) return null;

  const isRename = mode === "rename";

  return (
    <div className="skill-upload-modal-backdrop" onClick={onClose}>
      <section className="skill-upload-modal baseline-move-modal" onClick={(event) => event.stopPropagation()}>
        <header className="skill-upload-modal__header">
          <div>
            <h4>{isRename ? "重命名资源" : "移动资源"}</h4>
            <p>{isRename ? "修改当前文件或目录名称，底层仍使用同一条路径移动能力。" : "将当前文件或目录移动到新的目标路径，保持 section 不变。"}</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        <div className="skill-upload-modal__body">
          <div className="baseline-move-modal__field">
            <span>当前路径</span>
            <code>{sourcePath}</code>
          </div>

          <label className="baseline-move-modal__field">
            <span>{isRename ? "新的完整路径" : "目标路径"}</span>
            <input
              type="text"
              value={targetPath}
              onChange={(event) => onTargetPathChange(event.target.value)}
              placeholder={isRename ? "例如 work/docs/readme-v2.md" : "例如 work/docs/archive/readme.md"}
              disabled={busy}
            />
          </label>

          <p className="skill-upload-modal__intro">
            {isRename
              ? "支持直接输入新的完整路径。若只想改名，保持前级目录不变即可。"
              : "支持移动并同时改名，但暂不支持跨 `skills`、`work`、`logs` 三个根区域。"}
          </p>

          {error ? <p className="skill-upload-modal__error">{error}</p> : null}

          <div className="baseline-move-modal__actions">
            <button type="button" className="fm-btn outline" onClick={onClose} disabled={busy}>
              取消
            </button>
            <button type="button" className="fm-btn primary" onClick={onSubmit} disabled={busy || !targetPath.trim()}>
              {busy ? (isRename ? "重命名中..." : "移动中...") : (isRename ? "确认重命名" : "确认移动")}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
