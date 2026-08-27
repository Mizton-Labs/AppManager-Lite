import { useState } from "react";
import type { ApiUser } from "../types";
import { api } from "../api";
import { UserManagement } from "./UserManagement";
import { TeamManagement } from "./TeamManagement";
import { GeneralSettings } from "./GeneralSettings";
import { ServerProvisioning } from "./ServerProvisioning";
import { RemoteAccessConfig } from "./RemoteAccessConfig";

type Tab = "users" | "teams" | "servers" | "remote" | "general";

/** issue_local_032: allowlisted navigation-activity destination per tab. */
const TAB_DESTINATION: Record<Tab, string> = {
  general: "settings.general",
  users: "settings.users",
  teams: "settings.teams",
  servers: "settings.server_provisioning",
  remote: "settings.remote_access",
};

/**
 * Administrator settings area (route-guarded to admins): user management, teams,
 * server provisioning, remote access, and general settings (branding +
 * reverse-proxy configuration). Application management lives in its own top-level
 * "App Manager" section, not here.
 *
 * On first run (a fresh deployment that has not been configured yet) an
 * administrator lands here with the General Settings tab open and a short setup
 * prompt; saving reverse-proxy protected alias configuration completes setup.
 */
export function SettingsView(props: {
  isAdmin: boolean;
  currentUser: ApiUser | null;
  /** Retained for API compatibility with the shell; unused here. */
  appTeamOptions: readonly string[];
  /** True when this is the one-time first-run setup visit (admins only). */
  firstRun?: boolean;
  /** Called after the deployment is configured (refreshes the session). */
  onConfigured?: () => void | Promise<void>;
  /** Called after teams change so the sidebar/pickers refresh. */
  onTeamsChanged?: () => void | Promise<void>;
}) {
  const { currentUser, firstRun } = props;
  const [tab, setTab] = useState<Tab>("general");
  const showUsers = tab === "users";
  const showTeams = tab === "teams";
  const showServers = tab === "servers";
  const showRemote = tab === "remote";

  function selectTab(next: Tab) {
    setTab(next);
    void api.recordNavigation(TAB_DESTINATION[next]).catch(() => undefined);
  }

  return (
    <div className="stack wide">
      <header className="view-head">
        <h1>Settings</h1>
        <p className="muted">
          Configure user access, teams, branding, and reverse-proxy settings.
        </p>
      </header>

      {firstRun && (
        <p className="alert success" role="status">
          Welcome! Finish setup by setting branding and reverse-proxy protected
          alias authentication under General Settings, then save.
        </p>
      )}

      <nav className="tabs" aria-label="Settings sections">
        <button
          type="button"
          className={tab === "general" ? "tab active" : "tab"}
          aria-current={tab === "general"}
          onClick={() => selectTab("general")}
        >
          General Settings
        </button>
        <button
          type="button"
          className={tab === "users" ? "tab active" : "tab"}
          aria-current={tab === "users"}
          onClick={() => selectTab("users")}
        >
          User Management
        </button>
        <button
          type="button"
          className={tab === "teams" ? "tab active" : "tab"}
          aria-current={tab === "teams"}
          onClick={() => selectTab("teams")}
        >
          Teams
        </button>
        <button
          type="button"
          className={tab === "servers" ? "tab active" : "tab"}
          aria-current={tab === "servers"}
          onClick={() => selectTab("servers")}
        >
          Server Provisioning
        </button>
        <button
          type="button"
          className={tab === "remote" ? "tab active" : "tab"}
          aria-current={tab === "remote"}
          onClick={() => selectTab("remote")}
        >
          Remote Access
        </button>
      </nav>

      {showUsers ? (
        <UserManagement currentUser={currentUser} />
      ) : showTeams ? (
        <TeamManagement onTeamsChanged={props.onTeamsChanged} />
      ) : showServers ? (
        <ServerProvisioning />
      ) : showRemote ? (
        <RemoteAccessConfig />
      ) : (
        <GeneralSettings firstRun={firstRun} onConfigured={props.onConfigured} />
      )}
    </div>
  );
}
