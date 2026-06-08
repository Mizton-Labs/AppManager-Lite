import { useState } from "react";
import { api, ApiError } from "../api";
import type { SessionState } from "../types";
import { getAppName, getLogoSrc } from "../branding";

export function Login(props: { onAuthenticated: (session: SessionState) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const session = await api.login(username, password);
      props.onAuthenticated(session);
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

        {error && (
          <p className="alert error" role="alert">
            {error}
          </p>
        )}

        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
