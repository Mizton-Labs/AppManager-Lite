import { useState } from "react";
import type { ApiUser } from "../types";
import { ApplicationManager } from "./ApplicationManager";
import { UserManagement } from "./UserManagement";
import { TeamManagement } from "./TeamManagement";
import { GeneralSettings } from "./GeneralSettings";
import { ServerProvisioning } from "./ServerProvisioning";

type Tab = "apps" | "users" | "teams" | "servers" | "general";

/**
 * Settings area. Every signed-in user can submit and manage applications for
 * their own teams here; administrators additionally see every application (with
 * its creator and approval state), a tab for user management, a tab to manage
 * teams, and a tab for general settings (branding + reverse-proxy
 * configuration).
 *
 * On first run (a fresh deployment that has not been configured yet) an
 * administrator lands here with the General Settings tab open and a short setup
  * prompt; saving reverse-proxy protected alias configuration completes setup.
 */
export function SettingsView(props: {
  isAdmin: boolean;
  currentUser: ApiUser | null;
  /** Teams selectable when creating/editing an application (all teams: any
   *  user may share an application with any team). */
  appTeamOptions: readonly string[];
  /** True when this is the one-time first-run setup visit (admins only). */
  firstRun?: boolean;
  /** Called after the deployment is configured (refreshes the session). */
  onConfigured?: () => void | Promise<void>;
  /** Called after teams change so the sidebar/pickers refresh. */
  onTeamsChanged?: () => void | Promise<void>;
}) {
  const { isAdmin, currentUser, appTeamOptions, firstRun } = props;
  // On first run, open the General Settings tab so setup is front-and-centre.
  const [tab, setTab] = useState<Tab>(firstRun ? "general" : "apps");
  const showUsers = isAdmin && tab === "users";
  const showTeams = isAdmin && tab === "teams";
  const showServers = isAdmin && tab === "servers";
  const showGeneral = isAdmin && tab === "general";

  return (
    <div className="stack wide">
      <header className="view-head">
        <h1>Settings</h1>
        <p className="muted">
          {isAdmin
            ? "Configure applications, user access, teams, branding, and reverse-proxy settings."
            : "Submit and manage applications for your teams."}
        </p>
      </header>

      {firstRun && (
        <p className="alert success" role="status">
          Welcome! Finish setup by setting branding and reverse-proxy protected
          alias authentication under General Settings, then save.
        </p>
      )}

      {isAdmin && (
        <nav className="tabs" aria-label="Settings sections">
          <button
            type="button"
            className={tab === "apps" ? "tab active" : "tab"}
            aria-current={tab === "apps"}
            onClick={() => setTab("apps")}
          >
            Application Manager
          </button>
          <button
            type="button"
            className={tab === "users" ? "tab active" : "tab"}
            aria-current={tab === "users"}
            onClick={() => setTab("users")}
          >
            User Management
          </button>
          <button
            type="button"
            className={tab === "teams" ? "tab active" : "tab"}
            aria-current={tab === "teams"}
            onClick={() => setTab("teams")}
          >
            Teams
          </button>
          <button
            type="button"
            className={tab === "servers" ? "tab active" : "tab"}
            aria-current={tab === "servers"}
            onClick={() => setTab("servers")}
          >
            Server Provisioning
          </button>
          <button
            type="button"
            className={tab === "general" ? "tab active" : "tab"}
            aria-current={tab === "general"}
            onClick={() => setTab("general")}
          >
            General Settings
          </button>
        </nav>
      )}

      {showUsers ? (
        <UserManagement currentUser={currentUser} />
      ) : showTeams ? (
        <TeamManagement onTeamsChanged={props.onTeamsChanged} />
      ) : showServers ? (
        <ServerProvisioning />
      ) : showGeneral ? (
        <GeneralSettings firstRun={firstRun} onConfigured={props.onConfigured} />
      ) : (
        <ApplicationManager
          isAdmin={isAdmin}
          teamOptions={appTeamOptions}
          currentUser={currentUser}
        />
      )}
    </div>
  );
}
