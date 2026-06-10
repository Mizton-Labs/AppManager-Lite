import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import type { ApiUser, Role, UpdateUserInput } from "../types";
import { copyToClipboard } from "../lib/clipboard";
import { BundleTemplateManagement } from "./BundleTemplateManagement";

const ROLES: Role[] = ["admin", "user"];

interface Credential {
  username: string;
  password: string;
  note: string;
}

export function UserManagement(props: { currentUser: ApiUser | null }) {
  const [users, setUsers] = useState<ApiUser[]>([]);
  const [teams, setTeams] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [credential, setCredential] = useState<Credential | null>(null);

  const reload = useCallback(async () => {
    const [nextUsers, nextTeams] = await Promise.all([
      api.listUsers(),
      api.listTeams(),
    ]);
    setUsers(nextUsers);
    setTeams(nextTeams.map((team) => team.name));
  }, []);

  useEffect(() => {
    reload()
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Failed to load users."),
      )
      .finally(() => setLoading(false));
  }, [reload]);

  const runAction = useCallback(
    async (action: () => Promise<void>) => {
      setError(null);
      try {
        await action();
        await reload();
      } catch (err) {
        setError(
          err instanceof ApiError ? err.message : "The operation failed.",
        );
      }
    },
    [reload],
  );

  if (loading) {
    return <p role="status">Loading users…</p>;
  }

  return (
    <div className="stack wide">
      {credential && (
        <CredentialBanner
          credential={credential}
          onDismiss={() => setCredential(null)}
        />
      )}
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}

      <CreateUserCard
        teams={teams}
        onCreated={(cred) => {
          setCredential(cred);
          void reload();
        }}
        onError={setError}
      />

      <BundleTemplateManagement />

      <section className="card">
        <h2>Users</h2>
        <div className="user-list">
          {users.map((user) => (
            <UserRow
              key={user.id}
              user={user}
              teams={teams}
              isSelf={props.currentUser?.id === user.id}
              onSave={(input) =>
                runAction(async () => {
                  await api.updateUser(user.id, input);
                })
              }
              onResetPassword={() =>
                runAction(async () => {
                  const result = await api.resetPassword(user.id);
                  setCredential({
                    username: result.user.username,
                    password: result.password,
                    note: "Password reset. Share securely; the user must change it at next sign-in.",
                  });
                })
              }
              onDelete={(deleteApps) =>
                runAction(async () => {
                  await api.deleteUser(user.id, { delete_apps: deleteApps });
                })
              }
            />
          ))}
        </div>
      </section>
    </div>
  );
}

function CredentialBanner(props: { credential: Credential; onDismiss: () => void }) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">(
    "idle",
  );
  const { username, password, note } = props.credential;

  async function copy() {
    const ok = await copyToClipboard(password);
    if (ok) {
      setCopyState("copied");
      window.setTimeout(() => setCopyState("idle"), 2000);
    } else {
      setCopyState("error");
    }
  }

  function selectPassword(event: React.MouseEvent<HTMLElement>) {
    // Make the one-time password easy to copy by hand when the clipboard is
    // unavailable: a click selects its text.
    const range = document.createRange();
    range.selectNodeContents(event.currentTarget);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
  }

  return (
    <section className="card credential" role="status">
      <h2>Temporary credentials</h2>
      <p className="muted">{note}</p>
      <div className="credential-row">
        <span>
          <strong>{username}</strong>
          <code
            className="credential-value"
            onClick={selectPassword}
            title="Click to select"
          >
            {password}
          </code>
        </span>
        <span className="row-actions">
          <button type="button" className="btn ghost" onClick={copy}>
            {copyState === "copied" ? "Copied" : "Copy"}
          </button>
          <button type="button" className="btn ghost" onClick={props.onDismiss}>
            Dismiss
          </button>
        </span>
      </div>
      {copyState === "error" && (
        <p className="muted credential-copy-error">
          Couldn’t copy automatically — click the password to select it, then
          copy manually.
        </p>
      )}
    </section>
  );
}

function CreateUserCard(props: {
  teams: string[];
  onCreated: (credential: Credential) => void;
  onError: (message: string) => void;
}) {
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Role>("user");
  const [selectedTeams, setSelectedTeams] = useState<string[]>([]);
  const [selfService, setSelfService] = useState(false);
  const [appsServer, setAppsServer] = useState("");
  const [appsServerIp, setAppsServerIp] = useState("");
  const [busy, setBusy] = useState(false);
  const hasServerLocation = Boolean(appsServer.trim() || appsServerIp.trim());

  function toggleTeam(team: string) {
    setSelectedTeams((current) =>
      current.includes(team)
        ? current.filter((t) => t !== team)
        : [...current, team],
    );
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    props.onError("");
    setBusy(true);
    try {
      const result = await api.createUser({
        username: username.trim(),
        role,
        teams: selectedTeams,
        self_service: selfService,
        apps_server: appsServer.trim(),
        apps_server_ip: appsServerIp.trim(),
      });
      props.onCreated({
        username: result.user.username,
        password: result.password,
        note: "Account created. Share securely; the user must change this password at first sign-in.",
      });
      setUsername("");
      setRole("user");
      setSelectedTeams([]);
      setSelfService(false);
      setAppsServer("");
      setAppsServerIp("");
    } catch (err) {
      props.onError(
        err instanceof ApiError ? err.message : "Unable to create the user.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Create user</h2>
      <form className="create-form" onSubmit={onSubmit}>
        <div className="form-row">
          <label className="field">
            <span>Username (email)</span>
            <input
              type="email"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="user@example.com"
              required
            />
            <span className="muted logo-hint">
              Use the user's email address; this is their sign-in username.
            </span>
          </label>
          <label className="field">
            <span>Role</span>
            <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
        </div>

        <TeamPicker
          teams={props.teams}
          selected={selectedTeams}
          onToggle={toggleTeam}
        />

        <label className="field">
          <span>Apps server hostname</span>
          <input
            type="text"
            value={appsServer}
            onChange={(e) => setAppsServer(e.target.value)}
            placeholder="apps.example.com"
          />
          <span className="muted logo-hint">
            Preferred hostname where this user's applications run.
          </span>
        </label>

        <label className="field">
          <span>Apps server IP</span>
          <input
            type="text"
            value={appsServerIp}
            onChange={(e) => setAppsServerIp(e.target.value)}
            placeholder="10.0.0.8"
          />
          <span className="muted logo-hint">
            Used when a module needs an IP, or as the fallback when no hostname is set.
          </span>
        </label>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={selfService}
            onChange={(e) => setSelfService(e.target.checked)}
          />
          <span>Self-service (applications go live without approval)</span>
        </label>

        <button
          type="submit"
          className="btn primary"
          disabled={busy || username.trim().length === 0 || !hasServerLocation}
        >
          {busy ? "Creating…" : "Create user"}
        </button>
      </form>
    </section>
  );
}

function UserRow(props: {
  user: ApiUser;
  teams: string[];
  isSelf: boolean;
  onSave: (input: UpdateUserInput) => void;
  onResetPassword: () => void;
  onDelete: (deleteApps: boolean) => void;
}) {
  const { user } = props;
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleteApps, setDeleteApps] = useState(false);
  const [role, setRole] = useState<Role>(user.role);
  const [teams, setTeams] = useState<string[]>(user.teams);
  const [selfService, setSelfService] = useState(user.self_service);
  const [appsServer, setAppsServer] = useState(user.apps_server);
  const [appsServerIp, setAppsServerIp] = useState(user.apps_server_ip);

  useEffect(() => {
    setRole(user.role);
    setTeams(user.teams);
    setSelfService(user.self_service);
    setAppsServer(user.apps_server);
    setAppsServerIp(user.apps_server_ip);
  }, [user.role, user.teams, user.self_service, user.apps_server, user.apps_server_ip]);

  const dirty = useMemo(
    () =>
      role !== user.role ||
      selfService !== user.self_service ||
      appsServer !== user.apps_server ||
      appsServerIp !== user.apps_server_ip ||
      teams.length !== user.teams.length ||
      teams.some((t) => !user.teams.includes(t)),
    [
      role,
      selfService,
      appsServer,
      appsServerIp,
      teams,
      user.role,
      user.self_service,
      user.apps_server,
      user.apps_server_ip,
      user.teams,
    ],
  );
  const hasServerLocation = Boolean(appsServer.trim() || appsServerIp.trim());

  function toggleTeam(team: string) {
    setTeams((current) =>
      current.includes(team)
        ? current.filter((t) => t !== team)
        : [...current, team],
    );
  }

  return (
    <article className={user.is_active ? "user-card" : "user-card inactive"}>
      <div className="user-card-head">
        <div className="user-identity">
          <span className="user-name">{user.username}</span>
          <span className="role-badge">{user.role}</span>
          {user.self_service && (
            <span className="status-badge ok">self-service</span>
          )}
          {!user.is_active && <span className="status-badge off">disabled</span>}
          {user.must_change_password && (
            <span className="status-badge warn">must change password</span>
          )}
        </div>
        <div className="row-actions">
          <button
            type="button"
            className="btn ghost"
            onClick={() => setEditing((v) => !v)}
          >
            {editing ? "Close" : "Edit"}
          </button>
          <button type="button" className="btn ghost" onClick={props.onResetPassword}>
            Reset password
          </button>
          <button
            type="button"
            className="btn ghost"
            onClick={() => props.onSave({ is_active: !user.is_active })}
            disabled={props.isSelf}
            title={props.isSelf ? "You cannot change your own active status" : undefined}
          >
            {user.is_active ? "Disable" : "Enable"}
          </button>
        </div>
      </div>

      {!editing && user.teams.length > 0 && (
        <div className="tag-row">
          {user.teams.map((team) => (
            <span key={team} className="tag">
              {team}
            </span>
          ))}
        </div>
      )}

      {editing && (
        <div className="user-edit">
          <label className="field inline">
            <span>Role</span>
            <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>

          <TeamPicker teams={props.teams} selected={teams} onToggle={toggleTeam} />

          <label className="field">
            <span>Apps server hostname</span>
            <input
              type="text"
              value={appsServer}
              onChange={(e) => setAppsServer(e.target.value)}
              placeholder="apps.example.com"
            />
            <span className="muted logo-hint">
              Preferred hostname where this user's applications run.
            </span>
          </label>

          <label className="field">
            <span>Apps server IP</span>
            <input
              type="text"
              value={appsServerIp}
              onChange={(e) => setAppsServerIp(e.target.value)}
              placeholder="10.0.0.8"
            />
            <span className="muted logo-hint">
              Used when a module needs an IP, or as the fallback when no hostname is set.
            </span>
          </label>

          <label className="checkbox">
            <input
              type="checkbox"
              checked={selfService}
              onChange={(e) => setSelfService(e.target.checked)}
            />
            <span>Self-service (applications go live without approval)</span>
          </label>

          <div className="row-actions">
            <button
              type="button"
              className="btn primary"
              disabled={!dirty || !hasServerLocation}
              onClick={() => {
                props.onSave({
                  role,
                  teams,
                  self_service: selfService,
                  apps_server: appsServer.trim(),
                  apps_server_ip: appsServerIp.trim(),
                });
                setEditing(false);
              }}
            >
              Save changes
            </button>
            {confirmingDelete ? (
              <span className="confirm-inline">
                <span>Delete {user.username}?</span>
                <label className="checkbox inline-checkbox">
                  <input
                    type="checkbox"
                    checked={deleteApps}
                    onChange={(e) => setDeleteApps(e.target.checked)}
                  />
                  <span>Also delete this user's apps</span>
                </label>
                <button
                  type="button"
                  className="btn danger"
                  onClick={() => {
                    props.onDelete(deleteApps);
                    setConfirmingDelete(false);
                    setDeleteApps(false);
                  }}
                >
                  Confirm delete
                </button>
                <button
                  type="button"
                  className="btn ghost"
                  onClick={() => {
                    setConfirmingDelete(false);
                    setDeleteApps(false);
                  }}
                >
                  Cancel
                </button>
              </span>
            ) : (
              <button
                type="button"
                className="btn danger"
                disabled={props.isSelf}
                title={props.isSelf ? "You cannot delete your own account" : undefined}
                onClick={() => setConfirmingDelete(true)}
              >
                Delete
              </button>
            )}
          </div>
        </div>
      )}
    </article>
  );
}

function TeamPicker(props: {
  teams: string[];
  selected: string[];
  onToggle: (team: string) => void;
}) {
  return (
    <fieldset className="team-picker">
      <legend>Teams</legend>
      <div className="team-grid">
        {props.teams.map((team) => (
          <label key={team} className="checkbox">
            <input
              type="checkbox"
              checked={props.selected.includes(team)}
              onChange={() => props.onToggle(team)}
            />
            <span>{team}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
