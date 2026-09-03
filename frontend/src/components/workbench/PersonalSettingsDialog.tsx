import { useAppPreferences, type AppLanguage, type AppTheme } from "../../i18n";

type PersonalSettingsDialogProps = {
  open: boolean;
  onClose: () => void;
  onLogout?: () => void;
};

const themeOptions: AppTheme[] = ["system", "light", "dark"];
const languageOptions: AppLanguage[] = ["zh-CN", "en-US"];

export function PersonalSettingsDialog({ open, onClose, onLogout }: PersonalSettingsDialogProps) {
  const { language, setLanguage, setTheme, t, theme, themeLocked, hideReasoning, setHideReasoning } = useAppPreferences();

  if (!open) return null;

  return (
    <div className="modal-backdrop personal-settings-backdrop" onClick={onClose}>
      <section className="llm-dialog settings-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="settings-dialog__header">
          <h3>{t("settings.title")}</h3>
          <button type="button" className="icon-button subtle" onClick={onClose} aria-label={t("settings.close")}>×</button>
        </div>
        <div className="settings-dialog__body">
          {themeLocked ? null : (
            <div className="settings-row">
              <span className="settings-row__label">{t("settings.theme")}</span>
              <div className="segmented-control">
                {themeOptions.map((item) => (
                  <button key={item} type="button" className={`segment ${theme === item ? "active" : ""}`} onClick={() => setTheme(item)}>
                    {t(`theme.${item}`)}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="settings-row">
            <span className="settings-row__label">{t("settings.language")}</span>
            <div className="segmented-control">
              {languageOptions.map((item) => (
                <button key={item} type="button" className={`segment ${language === item ? "active" : ""}`} onClick={() => setLanguage(item)}>
                  {item === "zh-CN" ? "中文" : "English"}
                </button>
              ))}
            </div>
          </div>
          <div className="settings-row">
            <span className="settings-row__label">收起思考过程</span>
            <button
              type="button"
              className={`toggle-switch ${hideReasoning ? "on" : ""}`}
              onClick={() => setHideReasoning(!hideReasoning)}
              aria-pressed={hideReasoning}
            >
              <span className="toggle-switch__thumb" />
            </button>
          </div>
        </div>
        {onLogout ? (
          <div className="settings-dialog__footer">
            <button type="button" className="action-button danger-ghost-btn" onClick={onLogout}>
              {t("settings.signOut")}
            </button>
          </div>
        ) : null}
      </section>
    </div>
  );
}
