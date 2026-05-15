import type { CurrentUserProfile } from "../../api/client";
import { useAppPreferences, type AppLanguage, type AppTheme } from "../../i18n";

type PersonalSettingsDialogProps = {
  currentUser?: CurrentUserProfile | null;
  open: boolean;
  onClose: () => void;
  onLogout?: () => void;
};

const themeOptions: AppTheme[] = ["system", "light", "dark"];
const languageOptions: AppLanguage[] = ["zh-CN", "en-US"];

function getRoleKey(currentUser?: CurrentUserProfile | null) {
  if (currentUser?.can_manage_system) return "account.role.systemAdmin";
  if (currentUser?.can_manage_platforms) return "account.role.platformAdmin";
  return "account.role.user";
}

export function PersonalSettingsDialog({ currentUser, open, onClose, onLogout }: PersonalSettingsDialogProps) {
  const { language, setLanguage, setTheme, t, theme } = useAppPreferences();

  if (!open) return null;

  const profileItems = [
    [t("account.username"), currentUser?.username || currentUser?.full_name || t("common.unknown")],
    [t("account.email"), currentUser?.email || t("common.notRecorded")],
    [t("account.provider"), currentUser?.provider || t("common.notRecorded")],
    [t("account.role"), t(getRoleKey(currentUser))],
    [t("account.platforms"), String(currentUser?.managed_platform_count ?? 0)],
  ];

  return (
    <div className="modal-backdrop personal-settings-backdrop" onClick={onClose}>
      <section className="llm-dialog personal-settings-dialog" onClick={(event) => event.stopPropagation()}>
        <div className="llm-dialog__header personal-settings-dialog__header">
          <div className="personal-settings-dialog__heading">
            <div className="personal-settings-dialog__avatar" aria-hidden="true">
              {(currentUser?.full_name || "A").slice(0, 1).toUpperCase()}
            </div>
            <div>
              <h3>{t("settings.title")}</h3>
              <p>{t("settings.subtitle")}</p>
            </div>
          </div>
          <button type="button" className="icon-button subtle" onClick={onClose} aria-label={t("settings.close")}>
            ×
          </button>
        </div>

        <div className="llm-dialog__body personal-settings-dialog__body">
          <section className="personal-settings-section">
            <div>
              <span className="personal-settings-section__eyebrow">{t("settings.general")}</span>
              <h4>{t("settings.theme")}</h4>
            </div>
            <div className="preference-choice-grid">
              {themeOptions.map((item) => (
                <button
                  key={item}
                  type="button"
                  className={`preference-choice ${theme === item ? "is-active" : ""}`}
                  onClick={() => setTheme(item)}
                >
                  {t(`theme.${item}`)}
                </button>
              ))}
            </div>

            <div>
              <h4>{t("settings.language")}</h4>
            </div>
            <div className="preference-choice-grid">
              {languageOptions.map((item) => (
                <button
                  key={item}
                  type="button"
                  className={`preference-choice ${language === item ? "is-active" : ""}`}
                  onClick={() => setLanguage(item)}
                >
                  {item === "zh-CN" ? t("language.zh") : t("language.en")}
                </button>
              ))}
            </div>
          </section>

          <section className="personal-settings-section personal-settings-section--account">
            <div>
              <span className="personal-settings-section__eyebrow">{t("settings.account")}</span>
              <h4>{t("settings.profile")}</h4>
            </div>
            <div className="profile-card">
              <div className="profile-card__identity">
                <strong>{currentUser?.full_name || t("common.unknown")}</strong>
                <span>{currentUser?.account_id || currentUser?.username || t("common.notRecorded")}</span>
              </div>
              <div className="profile-card__grid">
                {profileItems.map(([label, value]) => (
                  <div key={label} className="profile-card__item">
                    <span>{label}</span>
                    <strong>{value}</strong>
                  </div>
                ))}
              </div>
            </div>
          </section>
        </div>

        <div className="llm-dialog__footer personal-settings-dialog__footer">
          <p>{t("settings.signOutHint")}</p>
          <button
            type="button"
            className="action-button sidebar-footer__button sidebar-footer__button--ghost danger-button"
            onClick={onLogout}
          >
            {t("settings.signOut")}
          </button>
        </div>
      </section>
    </div>
  );
}
