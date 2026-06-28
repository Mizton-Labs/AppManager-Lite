import { useEffect, useState } from "react";
import { api, apiBase, ApiError } from "../api";
import type { SessionState, SsoConfig } from "../types";
import { getAppName, getLogoSrc } from "../branding";

function safeNextPath(): string {
  const next = new URLSearchParams(window.location.search).get("next")?.trim() ?? "";
  if (!next.startsWith("/") || next.startsWith("//")) return "";
  return next;
}

function ssoLoginHref(loginUrl: string): string {
  const url = new URL(loginUrl, apiBase());
  const next = safeNextPath();
  if (next) {
    url.searchParams.set("next", next);
  }
  return url.toString();
}

export function Login(props: { onAuthenticated: (session: SessionState) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ssoConfig, setSsoConfig] = useState<SsoConfig | null>(null);

  useEffect(() => {
    let active = true;
    api
      .getSsoConfig()
      .then((config) => {
        if (active) {
          setSsoConfig(config);
        }
      })
      .catch(() => {
        if (active) {
          setSsoConfig({ enabled: false, local_login_enabled: true, providers: [] });
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const showLocalLogin = ssoConfig?.local_login_enabled !== false;

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const session = await api.login(username, password);
      props.onAuthenticated(session);
      const next = safeNextPath();
      if (next) {
        window.location.assign(next);
      }
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Unable to sign in. Try again.",
      );
      setBusy(false);
    }
  }

  return (
    <div className="center-page">
      <form className="card auth-card" onSubmit={onSubmit}>
        <img
          className="brand-logo auth-logo"
          src={getLogoSrc()}
          alt=""
          width={48}
          height={48}
        />
        <h1 className="brand">{getAppName()}</h1>
        <p className="muted">Sign in to access the lab portal.</p>

        {ssoConfig?.enabled && (
          <div className="stack">
            {ssoConfig.providers.map((provider) => (
              <a
                key={provider.protocol}
                className="btn primary"
                href={ssoLoginHref(provider.login_url)}
              >
                Sign in with {provider.label}
              </a>
            ))}
          </div>
        )}

        {showLocalLogin && (
          <>
            {ssoConfig?.enabled && <p className="muted">Or use local credentials.</p>}
            <label className="field">
              <span>Username</span>
              <input
                type="text"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                autoFocus
              />
            </label>

            <label className="field">
              <span>Password</span>
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </label>
          </>
        )}

        {error && (
          <p className="alert error" role="alert">
            {error}
          </p>
        )}

        {showLocalLogin && (
          <button type="submit" className="btn primary" disabled={busy}>
            {busy ? "Signing in..." : "Sign in"}
          </button>
        )}
      </form>
    </div>
  );
}
