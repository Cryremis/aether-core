import { useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";

import type { CurrentUserProfile } from "../api/client";
import { useAppPreferences } from "../i18n";
import { WorkbenchIcons as Icons } from "./workbench/WorkbenchIcons";

type AppChromeProps = {
  currentUser: CurrentUserProfile | null;
  authed: boolean;
  onRequireAuth: (targetPath: string) => void;
  onOpenPersonalSettings: () => void;
  onLogout: () => void;
};

export function AppChrome({ currentUser, authed, onRequireAuth, onOpenPersonalSettings, onLogout }: AppChromeProps) {
  const { language, setLanguage, t } = useAppPreferences();
  const location = useLocation();
  const navigate = useNavigate();
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const accountMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!accountMenuOpen) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!accountMenuRef.current?.contains(event.target as Node)) {
        setAccountMenuOpen(false);
      }
    };
    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [accountMenuOpen]);

  const guardedNavigate = (target: string) => {
    if (authed) {
      navigate(target);
      return;
    }
    onRequireAuth(target);
  };

  const roleLabel = currentUser?.can_manage_system
    ? t("account.role.systemAdmin")
    : currentUser?.can_manage_platforms
      ? `${t("account.role.platformAdmin")} · ${currentUser.managed_platform_count}`
      : t("account.role.user");

  const adminEntryHref = currentUser?.can_manage_system
    ? "/system"
    : currentUser?.can_manage_platforms
      ? "/platforms"
      : undefined;

  const handleMenuAction = (action: () => void) => {
    setAccountMenuOpen(false);
    action();
  };

  return (
    <header className="product-nav">
      <Link className="product-nav__brand" to="/">
        <span className="product-nav__mark">A</span>
        <span>{t("brand.name")}</span>
      </Link>

      <div className="product-nav__center">
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
            <>
              <button type="button" className={location.pathname.startsWith("/ips") ? "is-active" : ""} onClick={() => guardedNavigate("/ips")}>
                {t("nav.ips")}
              </button>
              <button type="button" className={location.pathname.startsWith("/system") ? "is-active" : ""} onClick={() => guardedNavigate("/system")}>
                {t("nav.system")}
              </button>
            </>
          ) : null}
        </nav>
      </div>

      <div className="product-nav__actions">
        <button
          type="button"
          className="nav-text-button"
          onClick={() => setLanguage(language === "zh-CN" ? "en-US" : "zh-CN")}
        >
          {language === "zh-CN" ? t("language.en") : t("language.zh")}
        </button>
        {authed ? (
          <div className="product-nav__account" ref={accountMenuRef}>
            {accountMenuOpen ? (
              <div className="account-menu product-account-menu" role="menu">
                <button type="button" className="account-menu__item" onClick={() => handleMenuAction(onOpenPersonalSettings)}>
                  <Icons.User />
                  <span>{t("settings.title")}</span>
                </button>
                {adminEntryHref ? (
                  <button type="button" className="account-menu__item" onClick={() => handleMenuAction(() => navigate(adminEntryHref))}>
                    <Icons.Console />
                    <span>{t("workbench.sidebar.adminConsole")}</span>
                  </button>
                ) : null}
                <button type="button" className="account-menu__item account-menu__item--danger" onClick={() => handleMenuAction(onLogout)}>
                  <Icons.LogOut />
                  <span>{t("nav.signOut")}</span>
                </button>
              </div>
            ) : null}
            <button
              type="button"
              className={`product-nav-account-trigger${accountMenuOpen ? " is-open" : ""}`}
              aria-expanded={accountMenuOpen}
              aria-label={t("workbench.sidebar.accountMenu")}
              onClick={() => setAccountMenuOpen((current) => !current)}
            >
              <span className="product-nav-account-trigger__avatar">{(currentUser?.full_name || "A").slice(0, 1).toUpperCase()}</span>
              <span className="product-nav-account-trigger__meta">
                <strong>{currentUser?.full_name || "User"}</strong>
                <span>{roleLabel}</span>
              </span>
              <Icons.ChevronDown />
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
