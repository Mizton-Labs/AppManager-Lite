import { useCallback, useEffect, useState } from "react";
import { api, ApiError, setCsrfToken } from "./api";
import type { SessionState } from "./types";
import { getAppName, setBranding } from "./branding";
import { Login } from "./components/Login";
import { ChangePasswordForm } from "./components/ChangePasswordForm";
import { PortalShell } from "./components/PortalShell";

export function App() {
  const [loading, setLoading] = useState(true);
  const [session, setSession] = useState<SessionState | null>(null);

  const refresh = useCallback(async () => {
    const next = await api.getSession();
    setCsrfToken(next.csrf_token);
    setBranding(next);
    setSession(next);
    return next;
  }, []);

  useEffect(() => {
    refresh()
      .catch(() => setSession(null))
      .finally(() => setLoading(false));
  }, [refresh]);

  const handleAuthenticated = useCallback((next: SessionState) => {
    setCsrfToken(next.csrf_token);
    setBranding(next);
    setSession(next);
  }, []);

  const handleLogout = useCallback(async () => {
    try {
      await api.logout();
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
    }
    setCsrfToken(null);
    await refresh().catch(() => setSession(null));
  }, [refresh]);

  if (loading) {
    return (
      <div className="app-loading" role="status">
        Loading…
      </div>
    );
  }

  if (!session || (session.enable_auth && !session.authenticated)) {
    return <Login onAuthenticated={handleAuthenticated} />;
  }

  const user = session.user;

  if (
    session.enable_auth &&
    session.auth_method === "local" &&
    user?.must_change_password
  ) {
    return (
      <ForcedChange
        username={user.username}
        onChanged={async () => {
          await refresh();
        }}
      />
    );
  }

  return (
    <PortalShell
      session={session}
      onLogout={session.enable_auth ? handleLogout : null}
      onPasswordChanged={async () => {
        await refresh();
      }}
      onSessionRefresh={async () => {
        await refresh();
      }}
    />
  );
}

function ForcedChange(props: { username: string; onChanged: () => Promise<void> }) {
  return (
    <div className="center-page">
      <div className="card auth-card">
        <h1 className="brand">{getAppName()}</h1>
        <h2>Update your password</h2>
        <p className="muted">
          Signed in as <strong>{props.username}</strong>. You must set a new
          password before continuing.
        </p>
        <ChangePasswordForm requireCurrent onChanged={props.onChanged} submitLabel="Set password" />
      </div>
    </div>
  );
}
