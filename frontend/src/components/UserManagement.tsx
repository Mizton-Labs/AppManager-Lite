import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import type {
  ApiUser,
  ProvisionResult,
  Role,
  ServerTemplateOption,
  UpdateUserInput,
} from "../types";
import { copyToClipboard } from "../lib/clipboard";
import { BundleTemplateManagement } from "./BundleTemplateManagement";
import { UserServersPanel } from "./UserServers";
import { SubTabs } from "./SubTabs";
import { PlusIcon } from "./icons";

/** Case-insensitive substring match of a user against a query. */
function userMatches(user: ApiUser, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hay = [user.username, user.user_id, user.role, ...user.teams]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return hay.includes(q);
}

const ROLES: Role[] = ["admin", "user"];

interface Credential {
  username: string;
  password: string;
  note: string;
  provisioning?: ProvisionResult[];
}

export function UserManagement(props: { currentUser: ApiUser | null }) {
  const [users, setUsers] = useState<ApiUser[]>([]);
  const [teams, setTeams] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [credential, setCredential] = useState<Credential | null>(null);
  // issue_018: while a create-user request is in flight, show an indeterminate
  // progress card listing the servers being provisioned. `null` = idle.
  const [provisioning, setProvisioning] = useState<string[] | null>(null);
  // issue_024: the create-user card is collapsed behind an "Add user" button,
  // and the user list has a client-side filter.
  const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState("");

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

      <SubTabs
        ariaLabel="User management sections"
        tabs={[
          {
            id: "users",
            label: "Users",
            render: () => (
              <div className="stack wide users-tab">
                {provisioning !== null && (
                  <ProvisioningProgressCard templateNames={provisioning} />
                )}
                {creating ? (
                  <CreateUserCard
                    teams={teams}
                    onCreatingChange={setProvisioning}
                    onCreated={(cred) => {
                      setCredential(cred);
                      setCreating(false);
                      void reload();
                    }}
                    onCancel={() => setCreating(false)}
                    onError={setError}
                  />
                ) : (
                  <div className="manager-toolbar">
                    <button
                      type="button"
                      className="btn accent"
                      onClick={() => {
                        setError(null);
                        setCreating(true);
                      }}
                    >
                      <PlusIcon />
                      <span className="btn-label">Add user</span>
                    </button>
                  </div>
                )}
                <section className="card">
                  <div className="card-head-row">
                    <h2>Users</h2>
                    <input
                      type="search"
                      className="list-filter"
                      placeholder="Filter users…"
                      aria-label="Filter users"
                      value={filter}
                      onChange={(e) => setFilter(e.target.value)}
                    />
                  </div>
                  <div className="user-list">
                    {(() => {
                      const shown = users.filter((u) => userMatches(u, filter));
                      if (shown.length === 0) {
                        return (
                          <p className="muted">
                            {filter.trim()
                              ? "No users match the filter."
                              : "No users."}
                          </p>
                        );
                      }
                      return shown.map((user) => (
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
                              await api.deleteUser(user.id, {
                                delete_apps: deleteApps,
                              });
                            })
                          }
                        />
                      ));
                    })()}
                  </div>
                </section>
              </div>
            ),
          },
          {
            id: "bundle-templates",
            label: "Bundle Templates",
            render: () => <BundleTemplateManagement />,
          },
        ]}
      />
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
      {props.credential.provisioning &&
        props.credential.provisioning.length > 0 && (
          <div className="rotation-summary">
            <h3>Server provisioning</h3>
            <ul>
              {props.credential.provisioning.map((result) => (
                <li key={result.template_id}>
                  <span
                    className={
                      result.status === "created"
                        ? "status-badge ok"
                        : result.status === "failed"
                          ? "status-badge warn"
                          : "status-badge off"
                    }
                  >
                    {result.status}
                  </span>{" "}
                  {result.template_name || `#${result.template_id}`}
                  {result.detail ? (
                    <span className="muted"> — {result.detail}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        )}
    </section>
  );
}

/** issue_018: indeterminate progress card shown at the top of the Users tab
 * while a create-user request (and its synchronous server provisioning) is in
 * flight. */
function ProvisioningProgressCard(props: { templateNames: string[] }) {
  const { templateNames } = props;
  return (
    <section className="card" aria-live="polite">
      <h2>Creating user…</h2>
      <div
        className="progress-indeterminate"
        role="progressbar"
        aria-label="Creating user"
      >
        <span className="progress-indeterminate-bar" />
      </div>
      {templateNames.length > 0 ? (
        <p className="muted">
          Provisioning {templateNames.length} server
          {templateNames.length === 1 ? "" : "s"}:{" "}
          {templateNames.join(", ")}. This can take a minute; please wait.
        </p>
      ) : (
        <p className="muted">Creating the account…</p>
      )}
    </section>
  );
}

function CreateUserCard(props: {
  teams: string[];
  onCreated: (credential: Credential) => void;
  onError: (message: string) => void;
  onCreatingChange: (templateNames: string[] | null) => void;
  onCancel?: () => void;
}) {
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Role>("user");
  const [selectedTeams, setSelectedTeams] = useState<string[]>([]);
  const [selfService, setSelfService] = useState(false);
  const [appsServer, setAppsServer] = useState("");
  const [appsServerIp, setAppsServerIp] = useState("");
  const [serverTemplates, setServerTemplates] = useState<
    ServerTemplateOption[]
  >([]);
  // Server template IDs to auto-provision on creation; every template is
  // enabled by default so a new user gets one server per template.
  const [provisionIds, setProvisionIds] = useState<Set<number>>(new Set());
  // Master toggle for server provisioning (default on, preserving prior
  // behavior). Turning it off clears the template selection so the account is
  // created without any servers, without requiring the admin to individually
  // uncheck every template.
  const [createServers, setCreateServers] = useState(true);
  const [busy, setBusy] = useState(false);
  // issue_017: apps-server templates offered as the default apps server. When
  // any exist, a selection (a template name, or "Custom") is required. When
  // none exist, the custom host/IP is optional with a warning.
  const appsServerTemplates = serverTemplates.filter((t) => t.is_apps_server);
  const CUSTOM = "__custom__";
  const [appsServerChoice, setAppsServerChoice] = useState("");
  const useCustomAppsServer =
    appsServerTemplates.length === 0 || appsServerChoice === CUSTOM;
  // A default apps server is required only when apps-server templates exist.
  const appsServerSatisfied =
    appsServerTemplates.length === 0 || appsServerChoice !== "";

  useEffect(() => {
    api
      .listServerTemplates()
      .then((templates) => {
        setServerTemplates(templates);
        setProvisionIds(new Set(templates.map((t) => t.id)));
      })
      .catch(() => undefined);
  }, []);

  function toggleTeam(team: string) {
    setSelectedTeams((current) =>
      current.includes(team)
        ? current.filter((t) => t !== team)
        : [...current, team],
    );
  }

  function toggleProvision(id: number) {
    setProvisionIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleCreateServers(checked: boolean) {
    setCreateServers(checked);
    if (checked) {
      setProvisionIds(new Set(serverTemplates.map((t) => t.id)));
    } else {
      setProvisionIds(new Set());
    }
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    props.onError("");
    setBusy(true);
    const selectedTemplates = serverTemplates.filter((t) =>
      provisionIds.has(t.id),
    );
    // Surface the in-flight provisioning to the parent so it can show the
    // progress card above this form. Provisioning runs server-side as part of
    // the (blocking) create request, so this reflects the request lifetime.
    props.onCreatingChange(selectedTemplates.map((t) => t.name));
    try {
      const result = await api.createUser({
        username: username.trim(),
        role,
        teams: selectedTeams,
        self_service: selfService,
        apps_server: useCustomAppsServer
          ? appsServer.trim()
          : appsServerChoice,
        apps_server_ip: useCustomAppsServer ? appsServerIp.trim() : "",
        provision_templates: selectedTemplates.map((t) => t.id),
      });
      props.onCreated({
        username: result.user.username,
        password: result.password,
        note: "Account created. Share securely; the user must change this password at first sign-in.",
        provisioning: result.provisioning,
      });
      setUsername("");
      setRole("user");
      setSelectedTeams([]);
      setSelfService(false);
      setAppsServer("");
      setAppsServerIp("");
      setAppsServerChoice("");
      setProvisionIds(new Set(serverTemplates.map((t) => t.id)));
      setCreateServers(true);
    } catch (err) {
      props.onError(
        err instanceof ApiError ? err.message : "Unable to create the user.",
      );
    } finally {
      setBusy(false);
      props.onCreatingChange(null);
    }
  }

  return (
    <section className="card">
      <div className="card-head-row">
        <h2>Create user</h2>
        {props.onCancel && (
          <button type="button" className="btn ghost" onClick={props.onCancel}>
            Cancel
          </button>
        )}
      </div>
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

        {appsServerTemplates.length > 0 && (
          <label className="field">
            <span>Default apps server</span>
            <select
              value={appsServerChoice}
              onChange={(e) => setAppsServerChoice(e.target.value)}
              required
            >
              <option value="" disabled>
                Select an apps server…
              </option>
              {appsServerTemplates.map((t) => (
                <option key={t.id} value={t.name}>
                  {t.name} ({t.kind.toUpperCase()})
                </option>
              ))}
              <option value={CUSTOM}>Custom…</option>
            </select>
            <span className="muted logo-hint">
              Where this user's applications run. Choose an apps server, or
              Custom to enter a hostname/IP directly.
            </span>
          </label>
        )}

        {appsServerTemplates.length === 0 && (
          <p className="muted logo-hint">
            No apps servers are configured. You can optionally set a custom apps
            server below; without one, this account can view applications but a
            custom apps server is required to create them.
          </p>
        )}

        {useCustomAppsServer && (
          <>
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
          </>
        )}

        <label className="checkbox">
          <input
            type="checkbox"
            checked={selfService}
            onChange={(e) => setSelfService(e.target.checked)}
          />
          <span>Self-service (applications go live without approval)</span>
        </label>

        {serverTemplates.length > 0 && (
          <fieldset className="team-picker">
            <legend>Provision servers</legend>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={createServers}
                onChange={(e) => toggleCreateServers(e.target.checked)}
              />
              <span>Create servers for this user</span>
            </label>
            {createServers && (
              <>
                <p className="muted logo-hint">
                  A server is created from each selected template
                  (named <code>TEMPLATE-USERID</code>). Failures don't block user
                  creation.
                </p>
                <div className="team-grid">
                  {serverTemplates.map((template) => (
                    <label key={template.id} className="checkbox">
                      <input
                        type="checkbox"
                        checked={provisionIds.has(template.id)}
                        onChange={() => toggleProvision(template.id)}
                      />
                      <span>
                        {template.name} ({template.kind.toUpperCase()})
                      </span>
                    </label>
                  ))}
                </div>
              </>
            )}
          </fieldset>
        )}

        <button
          type="submit"
          className="btn primary"
          disabled={busy || username.trim().length === 0 || !appsServerSatisfied}
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
  // issue_025: cards are collapsed by default; the teams/servers detail (and
  // its per-user servers fetch) is deferred until the admin expands the card.
  const [expanded, setExpanded] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleteApps, setDeleteApps] = useState(false);
  const [role, setRole] = useState<Role>(user.role);
  const [teams, setTeams] = useState<string[]>(user.teams);
  const [selfService, setSelfService] = useState(user.self_service);
  const [appsServer, setAppsServer] = useState(user.apps_server);
  const [appsServerIp, setAppsServerIp] = useState(user.apps_server_ip);
  const [username, setUsername] = useState(user.username);

  useEffect(() => {
    setRole(user.role);
    setTeams(user.teams);
    setSelfService(user.self_service);
    setAppsServer(user.apps_server);
    setAppsServerIp(user.apps_server_ip);
    setUsername(user.username);
  }, [
    user.role,
    user.teams,
    user.self_service,
    user.apps_server,
    user.apps_server_ip,
    user.username,
  ]);

  const dirty = useMemo(
    () =>
      role !== user.role ||
      selfService !== user.self_service ||
      appsServer !== user.apps_server ||
      appsServerIp !== user.apps_server_ip ||
      username.trim().toLowerCase() !== user.username.toLowerCase() ||
      teams.length !== user.teams.length ||
      teams.some((t) => !user.teams.includes(t)),
    [
      role,
      selfService,
      appsServer,
      appsServerIp,
      username,
      teams,
      user.role,
      user.self_service,
      user.apps_server,
      user.apps_server_ip,
      user.username,
      user.teams,
    ],
  );
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
          <div className="user-name-block">
            <span className="user-name">{user.username}</span>
            <code className="user-id">{user.user_id}</code>
          </div>
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
            aria-expanded={expanded || editing}
            onClick={() => {
              // Collapsing also closes an open edit form.
              if (expanded || editing) {
                setExpanded(false);
                setEditing(false);
              } else {
                setExpanded(true);
              }
            }}
          >
            {expanded || editing ? "Collapse" : "Expand"}
          </button>
          <button
            type="button"
            className="btn ghost"
            onClick={() => {
              setExpanded(true);
              setEditing((v) => !v);
            }}
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

      {expanded && !editing && user.teams.length > 0 && (
        <div className="tag-row">
          {user.teams.map((team) => (
            <span key={team} className="tag">
              {team}
            </span>
          ))}
        </div>
      )}

      {expanded && !editing && (
        <UserServersPanel
          userId={user.id}
          canCreate
          canDelete
          isAdmin
          userDerivedId={user.user_id}
          defaultPubkeyUser={user.user_id}
        />
      )}

      {editing && (
        <div className="user-edit">
          <label className="field">
            <span>Sign-in email</span>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="user@example.com"
            />
            <span className="muted logo-hint">
              Changes only how this account signs in (local login and SSO
              matching). The immutable <code>{user.user_id}</code> user ID,
              server names, SSH accounts, and pool/jump ownership never change.
            </span>
          </label>

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
              disabled={!dirty || username.trim().length === 0}
              onClick={() => {
                props.onSave({
                  username: username.trim(),
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
