import type { PlatformRuntimeImageFormState } from "./types";
import { useAppPreferences } from "../../i18n";

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
  const { t } = useAppPreferences();
  const guide = runtimeImageForm.guide;
  const hasCustomImage = Boolean(runtimeImageForm.image.trim());

  return (
    <div className="epic-llm-panel">
      <div className="manager-header__info">
        <h4>{t("runtimeImage.title")}</h4>
        <p>{t("runtimeImage.description").replace("{platform}", platformName)}</p>
      </div>
      {runtimeImageError ? <div className="admin-panel__error">{runtimeImageError}</div> : null}

      <div className="platform-runtime-image__hint">
        <strong>{t("runtimeImage.current")}</strong>
        <code>{runtimeImageForm.resolvedImage}</code>
      </div>
      {runtimeImageForm.recycledRuntimeCount !== null ? (
        <div className="platform-runtime-image__hint">
          <strong>{t("runtimeImage.lastRecycle")}</strong>
          <span>{t("runtimeImage.runtimeCount").replace("{count}", String(runtimeImageForm.recycledRuntimeCount))}</span>
        </div>
      ) : null}

      {guide ? (
        <div className="platform-runtime-spec">
          <div className="platform-runtime-spec__grid">
            <div>
              <span>{t("runtimeImage.targetOs")}</span>
              <code>{guide.build_spec.target_os}</code>
            </div>
            <div>
              <span>{t("runtimeImage.targetArch")}</span>
              <code>{guide.build_spec.target_arch}</code>
            </div>
            <div>
              <span>{t("runtimeImage.imageFormat")}</span>
              <code>{guide.build_spec.image_format}</code>
            </div>
            <div>
              <span>Shell</span>
              <code>{guide.build_spec.shell}</code>
            </div>
          </div>

          <div className="platform-runtime-spec__section">
            <strong>{t("runtimeImage.buildRequirements")}</strong>
            {guide.build_spec.build_steps.map((item) => (
              <p key={item}>{item}</p>
            ))}
          </div>

          <div className="platform-runtime-spec__section">
            <strong>{t("runtimeImage.directoryContract")}</strong>
            <div className="platform-runtime-spec__chips">
              {guide.build_spec.required_directories.map((item) => (
                <code key={item}>{item}</code>
              ))}
            </div>
          </div>

          <div className="platform-runtime-spec__section">
            <strong>{t("runtimeImage.envContract")}</strong>
            <div className="platform-runtime-spec__chips">
              {guide.build_spec.required_env_vars.map((item) => (
                <code key={item}>{item}</code>
              ))}
            </div>
          </div>

          <div className="platform-runtime-spec__section">
            <strong>{t("runtimeImage.resourceLimits")}</strong>
            {guide.build_spec.resource_limits.map((item) => (
              <p key={item}>{item}</p>
            ))}
          </div>

          <div className="platform-runtime-spec__section">
            <strong>{t("runtimeImage.sampleDockerfile")}</strong>
            <pre className="platform-runtime-spec__code"><code>{guide.build_spec.sample_dockerfile}</code></pre>
          </div>
        </div>
      ) : null}

      <div className="platform-runtime-upload">
        <div className="platform-runtime-upload__content">
          <span className="platform-runtime-upload__eyebrow">{t("runtimeImage.uploadEyebrow")}</span>
          <strong>{t("runtimeImage.uploadTitle")}</strong>
          <p>{t("runtimeImage.uploadHint")}</p>
        </div>
        <label className={`fm-btn primary platform-runtime-upload__button${runtimeImageBusy ? " is-disabled" : ""}`}>
          <span>{runtimeImageBusy ? t("runtimeImage.uploadBusy") : t("runtimeImage.chooseFile")}</span>
          <input
            type="file"
            accept=".tar,.tgz,.tar.gz"
            onChange={(e) => onUpload(e.target.files?.[0] ?? null)}
            disabled={runtimeImageBusy}
          />
        </label>
      </div>

      <label className="admin-panel__field">
        <span>{t("runtimeImage.manualRef")}</span>
        <input
          value={runtimeImageForm.image}
          onChange={(e) => onChange((current) => ({ ...current, image: e.target.value }))}
          autoComplete="off"
          name="platform-runtime-image"
          placeholder={t("runtimeImage.placeholder")}
        />
      </label>

      <div className="admin-panel__actions">
        <button
          type="button"
          className="action-button primary"
          onClick={onSave}
          disabled={runtimeImageBusy || !hasCustomImage}
        >
          {runtimeImageBusy ? t("runtimeImage.saveBusy") : t("runtimeImage.saveAndRecycle")}
        </button>
        <button
          type="button"
          className="action-button action-button--ghost danger-button"
          onClick={onReset}
          disabled={runtimeImageBusy}
        >
          {t("runtimeImage.resetGlobal")}
        </button>
      </div>
    </div>
  );
}
