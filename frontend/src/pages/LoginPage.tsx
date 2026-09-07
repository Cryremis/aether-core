import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  listOAuthProviders,
  loginWithOAuthCallback,
  loginWithPassword,
  setAccessToken,
} from "../api/client";
import { useAppPreferences } from "../i18n";

type LoginPageProps = {
  onLoggedIn: () => void;
  variant?: "page" | "modal";
};

type OAuthProvider = {
  provider_key: string;
  display_name: string;
  authorize_url_template: string;
};

const REMEMBER_KEY = "aethercore_remembered_credentials";

function readRememberedCredentials(): { username: string; password: string } | null {
  try {
    const raw = window.localStorage.getItem(REMEMBER_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { username?: string; password?: string };
    if (!parsed.username || !parsed.password) return null;
    return { username: parsed.username, password: parsed.password };
  } catch {
    return null;
  }
}

function saveRememberedCredentials(username: string, password: string) {
  window.localStorage.setItem(REMEMBER_KEY, JSON.stringify({ username, password }));
}

function clearRememberedCredentials() {
  window.localStorage.removeItem(REMEMBER_KEY);
}

function EyeIcon({ off }: { off?: boolean }) {
  return off ? (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
      <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
      <path d="M14.12 14.12a3 3 0 1 1-4.24-4.24" />
      <line x1="1" y1="1" x2="23" y2="23" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

export function LoginPage({ onLoggedIn, variant = "page" }: LoginPageProps) {
  const { t } = useAppPreferences();
  const remembered = useMemo(() => readRememberedCredentials(), []);
  const [username, setUsername] = useState(remembered?.username ?? "");
  const [password, setPassword] = useState(remembered?.password ?? "");
  const [remember, setRemember] = useState(Boolean(remembered));
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [providers, setProviders] = useState<OAuthProvider[]>([]);

  useEffect(() => {
    void listOAuthProviders()
      .then((result) => {
        setProviders((result.data ?? []) as OAuthProvider[]);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const url = new URL(window.location.href);
    const code = url.searchParams.get("code");
    const providerKey = url.searchParams.get("provider") || url.searchParams.get("state");
    if (!code || !providerKey) {
      return;
    }

    setBusy(true);
    setError("");

    void loginWithOAuthCallback(providerKey, code, `${window.location.origin}${window.location.pathname}`)
      .then((result) => {
        setAccessToken(String(result.access_token ?? ""));
        url.searchParams.delete("code");
        url.searchParams.delete("state");
        url.searchParams.delete("provider");
        window.history.replaceState({}, "", url.toString());
        onLoggedIn();
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "OAuth login failed");
      })
      .finally(() => setBusy(false));
  }, [onLoggedIn]);

  const canSubmit = useMemo(() => !!username.trim() && !!password && !busy, [busy, password, username]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }

    setBusy(true);
    setError("");

    try {
      const result = await loginWithPassword({
        username: username.trim(),
        password,
      });
      // 登录成功后再持久化凭据,避免把错误密码存进 localStorage
      if (remember) {
        saveRememberedCredentials(username.trim(), password);
      } else {
        clearRememberedCredentials();
      }
      setAccessToken(String(result.access_token ?? ""));
      onLoggedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Account login failed");
    } finally {
      setBusy(false);
    }
  };

  const handleOAuthLogin = (provider: OAuthProvider) => {
    if (!provider.authorize_url_template) {
      return;
    }
    const redirectUri = encodeURIComponent(`${window.location.origin}${window.location.pathname}`);
    const url = provider.authorize_url_template.replace(/\{redirect_uri\}|%7Bredirect_uri%7D/g, redirectUri);
    const state = `state=${encodeURIComponent(provider.provider_key)}`;
    window.location.href = url.includes("state=") ? url : `${url}&${state}`;
  };

  const content = (
    <section className={`login-card ${variant === "modal" ? "login-card--modal" : ""}`}>
      <div className="login-card__header">
        <span className="login-card__eyebrow">AetherCore</span>
        <h1>{t("auth.title")}</h1>
        <p>{variant === "modal" ? t("auth.description") : t("auth.pageDescription")}</p>
      </div>

      <form className="login-form" onSubmit={handleSubmit}>
        <label>
          <span>{t("auth.username")}</span>
          <input
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder={t("auth.usernamePlaceholder")}
            autoComplete="username"
          />
        </label>

        <label>
          <span>{t("auth.password")}</span>
          <span className="login-password-field">
            <input
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={t("auth.passwordPlaceholder")}
              autoComplete="current-password"
            />
            <button
              type="button"
              className="login-password-toggle"
              onClick={() => setShowPassword((v) => !v)}
              aria-label={showPassword ? t("auth.hidePassword") : t("auth.showPassword")}
              title={showPassword ? t("auth.hidePassword") : t("auth.showPassword")}
              tabIndex={-1}
            >
              <EyeIcon off={!showPassword} />
            </button>
          </span>
        </label>

        <label className="login-remember">
          <input
            type="checkbox"
            checked={remember}
            onChange={(event) => setRemember(event.target.checked)}
          />
          <span>{t("auth.remember")}</span>
        </label>

        <button type="submit" disabled={!canSubmit}>
          {busy ? t("auth.busy") : t("auth.passwordLogin")}
        </button>
      </form>

      {providers.length > 0 ? (
        <div className="login-oauth-grid">
          {providers.map((provider) => (
            <button
              key={provider.provider_key}
              className="login-oauth-button"
              type="button"
              onClick={() => handleOAuthLogin(provider)}
              disabled={busy || !provider.authorize_url_template}
            >
              {t("auth.oauthLogin").replace("{provider}", provider.display_name)}
            </button>
          ))}
        </div>
      ) : null}

      {error ? <div className="login-error">{error}</div> : null}
    </section>
  );

  if (variant === "modal") {
    return content;
  }

  return (
    <main className="login-screen">
      {content}
    </main>
  );
}
