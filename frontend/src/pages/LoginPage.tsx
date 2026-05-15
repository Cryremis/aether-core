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

export function LoginPage({ onLoggedIn, variant = "page" }: LoginPageProps) {
  const { t } = useAppPreferences();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
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
            <input value={username} onChange={(event) => setUsername(event.target.value)} placeholder={t("auth.usernamePlaceholder")} />
          </label>

          <label>
            <span>{t("auth.password")}</span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={t("auth.passwordPlaceholder")}
            />
          </label>

          <button type="submit" disabled={!canSubmit}>
            {busy ? t("auth.busy") : t("auth.passwordLogin")}
          </button>
        </form>

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
