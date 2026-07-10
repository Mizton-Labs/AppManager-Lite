import { useCallback, useEffect, useState } from "react";
import { api, ApiError, setCsrfToken } from "./api";
import type { SessionState } from "./types";
import { getAppName, setBranding } from "./branding";
import { Login } from "./components/Login";
import { ChangePasswordForm } from "./components/ChangePasswordForm";
import { PortalShell } from "./components/PortalShell";
import { ThemeProvider, useTheme } from "./theme";

export function App() {
  return (
    <ThemeProvider>
      <AppContent />
    </ThemeProvider>
  );
}

function AppContent() {
  const { applySessionTheme } = useTheme();
  const [loading, setLoading] = useState(true);
  const [session, setSession] = useState<SessionState | null>(null);

  const refresh = useCallback(async () => {
    const next = await api.getSession();
    setCsrfToken(next.csrf_token);
    setBranding(next);
    // issue_020: the theme is per-user. Apply the signed-in user's own theme,
    // falling back to the admin default (also used pre-auth on the login form).
    applySessionTheme(next.user?.theme, next.default_theme);
    setSession(next);
    return next;
  }, [applySessionTheme]);

  useEffect(() => {
    refresh()
      .catch(() => setSession(null))
      .finally(() => setLoading(false));
  }, [refresh]);

  const handleAuthenticated = useCallback(
    (next: SessionState) => {
      setCsrfToken(next.csrf_token);
      setBranding(next);
      applySessionTheme(next.user?.theme, next.default_theme);
      setSession(next);
    },
    [applySessionTheme],
  );

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
