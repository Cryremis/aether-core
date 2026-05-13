import type { PlatformRuntimeImageFormState } from "./types";

type PlatformRuntimeImagePanelProps = {
  platformName: string;
  runtimeImageForm: PlatformRuntimeImageFormState;
  runtimeImageError: string;
  runtimeImageBusy: boolean;
  onChange: (updater: (current: PlatformRuntimeImageFormState) => PlatformRuntimeImageFormState) => void;
  onSave: () => void;
  onReset: () => void;
  onUpload: (file: File | null) => void;
};

export function PlatformRuntimeImagePanel({
  platformName,
  runtimeImageForm,
  runtimeImageError,
  runtimeImageBusy,
  onChange,
  onSave,
  onReset,
  onUpload,
}: PlatformRuntimeImagePanelProps) {
  const guide = runtimeImageForm.guide;
  const hasCustomImage = Boolean(runtimeImageForm.image.trim());

  return (
    <div className="epic-llm-panel">
      <div className="manager-header__info">
        <h4>平台运行镜像</h4>
        <p>为 {platformName} 构建并发布当前 sandbox 运行镜像。上传成功后会自动启用并回收旧 runtime。</p>
      </div>
      {runtimeImageError ? <div className="admin-panel__error">{runtimeImageError}</div> : null}

      <div className="platform-runtime-image__hint">
        <strong>当前生效：</strong>
        <code>{runtimeImageForm.resolvedImage}</code>
      </div>
      {runtimeImageForm.recycledRuntimeCount !== null ? (
        <div className="platform-runtime-image__hint">
          <strong>最近一次回收：</strong>
          <span>{runtimeImageForm.recycledRuntimeCount} 个 runtime</span>
        </div>
      ) : null}

      {guide ? (
        <div className="platform-runtime-spec">
          <div className="platform-runtime-spec__grid">
            <div>
              <span>目标系统</span>
              <code>{guide.build_spec.target_os}</code>
            </div>
            <div>
              <span>目标架构</span>
              <code>{guide.build_spec.target_arch}</code>
            </div>
            <div>
              <span>镜像格式</span>
              <code>{guide.build_spec.image_format}</code>
            </div>
            <div>
              <span>Shell</span>
              <code>{guide.build_spec.shell}</code>
            </div>
          </div>

          <div className="platform-runtime-spec__section">
            <strong>构建要求</strong>
            {guide.build_spec.build_steps.map((item) => (
              <p key={item}>{item}</p>
            ))}
          </div>

          <div className="platform-runtime-spec__section">
            <strong>目录契约</strong>
            <div className="platform-runtime-spec__chips">
              {guide.build_spec.required_directories.map((item) => (
                <code key={item}>{item}</code>
              ))}
            </div>
          </div>

          <div className="platform-runtime-spec__section">
            <strong>环境变量契约</strong>
            <div className="platform-runtime-spec__chips">
              {guide.build_spec.required_env_vars.map((item) => (
                <code key={item}>{item}</code>
              ))}
            </div>
          </div>

          <div className="platform-runtime-spec__section">
            <strong>资源与运行约束</strong>
            {guide.build_spec.resource_limits.map((item) => (
              <p key={item}>{item}</p>
            ))}
          </div>

          <div className="platform-runtime-spec__section">
            <strong>示例 Dockerfile</strong>
            <pre className="platform-runtime-spec__code"><code>{guide.build_spec.sample_dockerfile}</code></pre>
          </div>
        </div>
      ) : null}

      <div className="platform-runtime-upload">
        <div className="platform-runtime-upload__content">
          <span className="platform-runtime-upload__eyebrow">镜像归档上传</span>
          <strong>上传后自动切换为当前镜像</strong>
          <p>支持 `.tar` / `.tgz` / `.tar.gz`，启用新镜像后旧镜像归档会由平台侧清理。</p>
        </div>
        <label className={`fm-btn primary platform-runtime-upload__button${runtimeImageBusy ? " is-disabled" : ""}`}>
          <span>{runtimeImageBusy ? "上传处理中..." : "选择镜像文件"}</span>
          <input
            type="file"
            accept=".tar,.tgz,.tar.gz"
            onChange={(e) => onUpload(e.target.files?.[0] ?? null)}
            disabled={runtimeImageBusy}
          />
        </label>
      </div>

      <label className="admin-panel__field">
        <span>手动指定镜像引用</span>
        <input
          value={runtimeImageForm.image}
          onChange={(e) => onChange((current) => ({ ...current, image: e.target.value }))}
          autoComplete="off"
          name="platform-runtime-image"
          placeholder="例如 registry.example.com/aether/platform:2026.05.12"
        />
      </label>

      <div className="admin-panel__actions">
        <button
          type="button"
          className="action-button primary"
          onClick={onSave}
          disabled={runtimeImageBusy || !hasCustomImage}
        >
          {runtimeImageBusy ? "处理中..." : "保存并回收旧 runtime"}
        </button>
        <button
          type="button"
          className="action-button action-button--ghost danger-button"
          onClick={onReset}
          disabled={runtimeImageBusy}
        >
          恢复全局默认
        </button>
      </div>
    </div>
  );
}
