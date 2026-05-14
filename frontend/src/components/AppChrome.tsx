import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";

import type { CurrentUserProfile } from "../api/client";
import { useAppPreferences } from "../i18n";
import { ThemeIconButton } from "./ThemeIconButton";

type AppChromeProps = {
  currentUser: CurrentUserProfile | null;
  authed: boolean;
  onRequireAuth: (targetPath: string) => void;
  onLogout: () => void;
};

export function AppChrome({ currentUser, authed, onRequireAuth, onLogout }: AppChromeProps) {
  const { language, setLanguage, t } = useAppPreferences();
  const location = useLocation();
  const navigate = useNavigate();

  const guardedNavigate = (target: string) => {
    if (authed) {
      navigate(target);
      return;
    }
    onRequireAuth(target);
  };

  return (
    <header className="product-nav">
      <Link className="product-nav__brand" to="/">
        <span className="product-nav__mark">A</span>
        <span>{t("brand.name")}</span>
      </Link>

      <nav className="product-nav__links" aria-label="Primary">
        <NavLink to="/" className={({ isActive }) => (isActive ? "is-active" : "")}>
          {t("nav.home")}
        </NavLink>
        <button type="button" className={location.pathname.startsWith("/workbench") || location.pathname.startsWith("/chat") ? "is-active" : ""} onClick={() => guardedNavigate("/workbench")}>
          {t("nav.chat")}
        </button>
        <button type="button" className={location.pathname.startsWith("/platforms") ? "is-active" : ""} onClick={() => guardedNavigate("/platforms")}>
          {t("nav.platforms")}
        </button>
        {currentUser?.can_manage_system ? (
          <button type="button" className={location.pathname.startsWith("/system") ? "is-active" : ""} onClick={() => guardedNavigate("/system")}>
            {t("nav.system")}
          </button>
        ) : null}
      </nav>

      <div className="product-nav__actions">
        <ThemeIconButton className="nav-theme-button" showLabel />
        <button
          type="button"
          className="nav-text-button"
          onClick={() => setLanguage(language === "zh-CN" ? "en-US" : "zh-CN")}
        >
          {language === "zh-CN" ? t("language.en") : t("language.zh")}
        </button>
        {authed ? (
          <div className="product-nav__user">
            <span>{currentUser?.full_name || "User"}</span>
            <button type="button" onClick={onLogout}>
              {t("nav.signOut")}
            </button>
          </div>
        ) : (
          <button type="button" className="nav-primary-button" onClick={() => onRequireAuth(location.pathname + location.search)}>
            {t("nav.signIn")}
          </button>
        )}
      </div>
    </header>
  );
}
