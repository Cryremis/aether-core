// frontend/src/pages/LoginPage.tsx
import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  getW3Config,
  loginWithPassword,
  loginWithW3Callback,
  setAccessToken,
} from "../api/client";

type LoginPageProps = {
  onLoggedIn: () => void;
};

export function LoginPage({ onLoggedIn }: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [w3Template, setW3Template] = useState("");

  useEffect(() => {
    void getW3Config()
      .then((result) => {
        const data = (result.data ?? {}) as Record<string, unknown>;
        setW3Template(String(data.authorize_url_template ?? ""));
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const url = new URL(window.location.href);
    const code = url.searchParams.get("code");
    if (!code) {
      return;
    }

    setBusy(true);
    setError("");

    void loginWithW3Callback(code, `${window.location.origin}${window.location.pathname}`)
      .then((result) => {
        setAccessToken(String(result.access_token ?? ""));
        url.searchParams.delete("code");
        url.searchParams.delete("state");
        window.history.replaceState({}, "", url.toString());
        onLoggedIn();
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "W3 登录失败");
      })
      .finally(() => setBusy(false));
  }, [onLoggedIn]);

  const canSubmit = useMemo(() => {
    return !!username.trim() && !!password && !busy;
  }, [busy, password, username]);

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
      setError(err instanceof Error ? err.message : "账号登录失败");
    } finally {
      setBusy(false);
    }
  };

  const handleW3Login = () => {
    if (!w3Template) {
      return;
    }
    const redirectUri = encodeURIComponent(`${window.location.origin}${window.location.pathname}`);
    window.location.href = w3Template.replace("{redirect_uri}", redirectUri);
  };

  return (
    <main className="login-screen">
      <section className="login-card">
        <div className="login-card__header">
          <span className="login-card__eyebrow">AetherCore Admin</span>
          <h1>轻量控制台登录</h1>
          <p>支持系统管理员、调试账号与 W3 OAuth 登录。</p>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <label>
            <span>用户名</span>
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="请输入用户名"
            />
          </label>

          <label>
            <span>密码</span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="请输入密码"
            />
          </label>

          <button type="submit" disabled={!canSubmit}>
            {busy ? "登录中..." : "账号登录"}
          </button>
        </form>

        <button
          className="login-w3-button"
          type="button"
          onClick={handleW3Login}
          disabled={!w3Template || busy}
        >
          使用 W3 登录
        </button>

        {error ? <div className="login-error">{error}</div> : null}
      </section>
    </main>
  );
}
