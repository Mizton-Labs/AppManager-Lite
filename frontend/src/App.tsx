import { useCallback, useEffect, useState } from "react";
import { api, ApiError, setCsrfToken } from "./api";
import type { SessionState } from "./types";
import { getAppName, setBranding } from "./branding";
import { Login } from "./components/Login";
import { ChangePasswordForm } from "./components/ChangePasswordForm";
import { PortalShell } from "./components/PortalShell";
import { ThemeProvider, useTheme } from "./theme";
import { isPortalLandingPage, safeNextFromLocation } from "./lib/navigation";

export function App() {
  return (
    <ThemeProvider>
      <AppContent />
    </ThemeProvider>
  );
}

/**
 * issue_local_033: whether the authenticated-bootstrap flow should resume a
 * pending `?next=` destination (e.g. a protected alias reached from a
 * genuinely cross-site page, where the browser omitted the SameSite=Strict
 * session cookie from that first request -- nginx's auth_request then
 * redirected to the portal landing page with `next` set). Deliberately
 * mirrors every gate the rest of the app already enforces, so this can never
 * bypass a security/policy decision made elsewhere:
 *  - auth must be enabled (an auth-disabled session's `authenticated: true`
 *    is synthetic and does not mean every alias is reachable, so redirecting
 *    on that alone could loop against a private alias);
 *  - the session must actually be authenticated;
 *  - a local session with a mandatory pending password change must see that
 *    screen first, never the alias (SSO sessions are unaffected by the local
 *    password flag, matching existing forced-change behavior below);
 *  - only ever runs from the portal's own landing page, never some other SPA
 *    route that happens to carry a stray `next` parameter.
 */
function resumableNext(session: SessionState): string {
  if (!session.enable_auth || !session.authenticated) return "";
  if (!isPortalLandingPage()) return "";
  const user = session.user;
  if (session.auth_method === "local" && user?.must_change_password) return "";
  return safeNextFromLocation();
}

function AppContent() {
  const { applySessionTheme } = useTheme();
  const [loading, setLoading] = useState(true);
  const [session, setSession] = useState<SessionState | null>(null);
  const [resuming, setResuming] = useState(false);

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

  useEffect(() => {
    if (loading || !session) return;
    const destination = resumableNext(session);
    if (!destination) return;
    setResuming(true);
    // A full document replace (not assign): this is a recovery hop for an
    // already-authenticated session, not a new navigation the user should be
    // able to "back" out of into the `?next=` handoff page.
    window.location.replace(destination);
  }, [loading, session]);

  if (loading || resuming) {
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
