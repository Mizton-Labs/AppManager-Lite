import { useCallback, useEffect, useRef, useState } from "react";
import {
  Link,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import type { Application, SessionState, Team } from "../types";
import { api } from "../api";
import { getAppName, getLogoSrc } from "../branding";
import { Sidebar } from "./Sidebar";
import { AccountPanel } from "./AccountPanel";
import { HomeView } from "./HomeView";
import { TeamView } from "./TeamView";
import { SettingsView } from "./SettingsView";
import { AboutView } from "./AboutView";
import { AuditView } from "./AuditView";
import { ServersView } from "./ServersView";
import { UserGuideView } from "./UserGuideView";
import { AppStatisticsView } from "./AppStatisticsView";
import { ApplicationManager } from "./ApplicationManager";
import { EmbeddedAppView } from "./EmbeddedAppView";
import { LogOutIcon, MenuIcon } from "./icons";

const SIDEBAR_KEY = "appmanager-lite.sidebar.collapsed";

/** issue_local_032: map a route path to its allowlisted navigation-activity
 * destination key (see backend repository.NAVIGATION_DESTINATIONS). Returns
 * null for a path that should not be recorded (e.g. an unmatched/wildcard
 * redirect intermediate). */
function destinationForPath(pathname: string): string | null {
  const exact: Record<string, string> = {
    "/": "home",
    "/account": "account",
    "/app-manager": "app_manager",
    "/settings": "settings",
    "/about": "about",
    "/user-guide": "user_guide",
    "/servers": "servers",
    "/audit": "audit",
    "/app-statistics": "app_statistics",
  };
  if (exact[pathname]) return exact[pathname];
  if (pathname.startsWith("/teams/")) return "team";
  if (pathname.startsWith("/embedded/")) return "embedded_application";
  return null;
}

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "1";
  } catch {
    return false;
  }
}

function writeCollapsed(value: boolean): void {
  try {
    localStorage.setItem(SIDEBAR_KEY, value ? "1" : "0");
  } catch {
    /* storage unavailable; collapse state is best-effort */
  }
}

/**
 * Authenticated portal shell: top header (logo -> Home, current user, sign out)
 * plus a collapsible sidebar and the routed content area. Settings (application
 * management) is reached from the sidebar and is available to every signed-in
 * user; team visibility is filtered here for the sidebar and home view.
 */
export function PortalShell(props: {
  session: SessionState;
  onLogout: (() => void) | null;
  onPasswordChanged: () => void | Promise<void>;
  onSessionRefresh: () => void | Promise<void>;
}) {
  const { session } = props;
  const user = session.user;
  const isAdmin = !session.enable_auth || user?.role === "admin";

  // Teams are administrator-managed and fetched from the backend, so the
  // sidebar, routes, and pickers are data-driven (no hardcoded team list).
  const [teams, setTeams] = useState<Team[]>([]);
  const reloadTeams = useCallback(async () => {
    try {
      setTeams(await api.listTeams());
    } catch {
      // A failed fetch leaves the previous list in place; the sidebar simply
      // shows no (or stale) teams rather than breaking the shell.
    }
  }, []);
  useEffect(() => {
    void reloadTeams();
  }, [reloadTeams]);

  // Team sections are visible to every user; team pages then show apps shared to
  // that specific team, which makes an app's sharing scope explicit.
  const visibleTeams: Team[] = teams;
  const visibleTeamNames: readonly string[] = visibleTeams.map((t) => t.name);
  const allTeamNames: readonly string[] = teams.map((t) => t.name);

  // Embedded apps the user may access populate a dedicated sidebar section and
  // are opened in an in-portal iframe. Reloaded on demand after create/edit.
  const [embeddedApps, setEmbeddedApps] = useState<Application[]>([]);
  const reloadEmbeddedApps = useCallback(async () => {
    try {
      const apps = await api.listApplications();
      setEmbeddedApps(apps.filter((a) => a.url_type === "embedded" && a.is_active));
    } catch {
      // Leave the previous list in place on a failed fetch.
    }
  }, []);
  useEffect(() => {
    void reloadEmbeddedApps();
  }, [reloadEmbeddedApps]);

  const [collapsed, setCollapsed] = useState<boolean>(readCollapsed);

  // First-login wizard: the very first time an administrator reaches the portal
  // before the deployment has been configured, send them to Settings once so
  // they can set the initial branding. Subsequent logins land on Home. Guarded
  // by a ref so it only redirects a single time per mount.
  const navigate = useNavigate();
  const redirectedToSetup = useRef(false);
  useEffect(() => {
    if (
      session.enable_auth &&
      isAdmin &&
      !session.configured &&
      !redirectedToSetup.current
    ) {
      redirectedToSetup.current = true;
      navigate("/settings", { replace: true });
    }
  }, [session.enable_auth, session.configured, isAdmin, navigate]);

  function toggleSidebar() {
    setCollapsed((current) => {
      const next = !current;
      writeCollapsed(next);
      return next;
    });
  }

  // The embedded app view fills the entire content section edge-to-edge (no
  // max-width/padding cap), so the framed app uses all available space and
  // grows automatically when the sidebar collapses. Other routes keep the
  // default centered, padded layout.
  const location = useLocation();
  const fullBleed = location.pathname.startsWith("/embedded/");

  // issue_local_032: record navigation to an allowlisted top-level section,
  // debounced so a quick sequence of redirects (e.g. the first-run bounce
  // into /settings) only records the final settled destination. Never
  // records for the synthetic auth-disabled identity (id 0), which has no
  // real account to attribute activity to.
  useEffect(() => {
    if (!user || user.id === 0) return;
    const destination = destinationForPath(location.pathname);
    if (!destination) return;
    const timer = window.setTimeout(() => {
      void api.recordNavigation(destination).catch(() => undefined);
    }, 750);
    return () => window.clearTimeout(timer);
  }, [location.pathname, user]);

  return (
    <div className={collapsed ? "app-shell collapsed" : "app-shell"}>
      <header className="shell-header">
        <button
          type="button"
          className="icon-btn"
          onClick={toggleSidebar}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!collapsed}
        >
          <MenuIcon />
        </button>

        <Link to="/" className="brand-link" aria-label={`${getAppName()} home`}>
          <img
            className="brand-logo"
            src={getLogoSrc()}
            alt=""
            width={28}
            height={28}
          />
          <span className="brand-title">{getAppName()}</span>
        </Link>

        <div className="header-right">
          {user && (
            <span className="user-chip">
              {user.username}
              <span className="role-badge">{user.role}</span>
            </span>
          )}
          {props.onLogout && (
            <button
              type="button"
              className="btn ghost"
              onClick={props.onLogout}
              title="Sign out"
            >
              <LogOutIcon />
              <span className="btn-label">Sign out</span>
            </button>
          )}
        </div>
      </header>

      <div className="shell-body">
        <Sidebar
          teams={visibleTeams}
          embeddedApps={embeddedApps}
          collapsed={collapsed}
          isAdmin={isAdmin}
        />
        <main className={fullBleed ? "shell-main full-bleed" : "shell-main"}>
          <Routes>
            <Route path="/" element={<HomeView teams={visibleTeamNames} />} />
            <Route
              path="/teams/:slug"
              element={<TeamView teams={visibleTeamNames} />}
            />
            <Route
              path="/account"
              element={
                user ? (
                  <AccountPanel
                    user={user}
                    onPasswordChanged={props.onPasswordChanged}
                  />
                ) : (
                  <Navigate to="/" replace />
                )
              }
            />
            <Route
              path="/app-manager"
              element={
                <ApplicationManager
                  isAdmin={isAdmin}
                  teamOptions={isAdmin ? allTeamNames : visibleTeamNames}
                  currentUser={user}
                  onAppsChanged={reloadEmbeddedApps}
                />
              }
            />
            <Route
              path="/embedded/:id"
              element={<EmbeddedAppView />}
            />
            <Route
              path="/settings"
              element={
                isAdmin ? (
                  <SettingsView
                    isAdmin={isAdmin}
                    currentUser={user}
                    appTeamOptions={allTeamNames}
                    firstRun={session.enable_auth && !session.configured}
                    onConfigured={props.onSessionRefresh}
                    onTeamsChanged={reloadTeams}
                  />
                ) : (
                  <Navigate to="/" replace />
                )
              }
            />
            <Route path="/about" element={<AboutView />} />
            <Route path="/user-guide" element={<UserGuideView />} />
            <Route
              path="/servers"
              element={
                user ? (
                  <ServersView currentUser={user} isAdmin={isAdmin} />
                ) : (
                  <Navigate to="/" replace />
                )
              }
            />
            <Route
              path="/audit"
              element={isAdmin ? <AuditView /> : <Navigate to="/" replace />}
            />
            <Route path="/app-statistics" element={isAdmin ? <AppStatisticsView /> : <Navigate to="/" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
