import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import type {
  ApiUser,
  Application,
  ApplicationShareUser,
  ApprovalStatus,
  UpdateApplicationInput,
  UrlType,
} from "../types";
import { resolveAppHref, resolveIconSrc } from "../lib/links";
import { fileToLogoDataUrl } from "../lib/image";
import { defaultLogoFor } from "../logos";
import { CheckIcon, PlusIcon, XIcon } from "./icons";

/** Case-insensitive substring match of an application against a query. */
function appMatches(app: Application, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hay = [
    app.name,
    app.description,
    app.url,
    app.created_by ?? "",
    ...app.teams,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return hay.includes(q);
}

/**
 * Application Manager. Every signed-in user manages the applications they have
 * submitted (in any approval state); administrators manage every application and
 * can approve or reject submissions. Team options are limited to the caller's
 * own teams unless they are an administrator.
 */
export function ApplicationManager(props: {
  isAdmin: boolean;
  teamOptions: readonly string[];
  currentUser?: ApiUser | null;
  /** Called after an application is created/edited/deleted so the shell can
   *  refresh dependent navigation (e.g. the Embedded apps sidebar section). */
  onAppsChanged?: () => void | Promise<void>;
}) {
  const { isAdmin, teamOptions } = props;
  const editAppId = Number(new URLSearchParams(window.location.search).get("editApp") ?? "") || null;
  const [apps, setApps] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  // Transient, view-local reverse-proxy push results, keyed by application id.
  // Populated from an action's returned push status and shown once next to the
  // application name. Cleared on reload (and therefore on leaving this view).
  const [pushNotices, setPushNotices] = useState<Record<number, string>>({});
  const [pushBusy, setPushBusy] = useState<Record<number, boolean>>({});
  const [pushMessages, setPushMessages] = useState<Record<number, string>>({});
  const [ownerOptions, setOwnerOptions] = useState<ApiUser[]>([]);
  // issue_024: client-side filter over the loaded applications.
  const [filter, setFilter] = useState("");

  const onAppsChanged = props.onAppsChanged;
  const reload = useCallback(async () => {
    const [nextApps, nextUsers] = await Promise.all([
      isAdmin ? api.listManagedApplications() : api.listMyApplications(),
      isAdmin ? api.listUsers() : Promise.resolve([]),
    ]);
    setApps(nextApps);
    setOwnerOptions(nextUsers.filter((user) => user.is_active));
    // Let the shell refresh embedded-app navigation after any change.
    void onAppsChanged?.();
  }, [isAdmin, onAppsChanged]);

  useEffect(() => {
    setLoading(true);
    reload()
      .catch((err) =>
        setError(
          err instanceof ApiError
            ? err.message
            : "Failed to load applications.",
        ),
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

  const currentUserId = props.currentUser?.id ?? null;

  const moveApp = useCallback(
    async (appId: number, direction: -1 | 1) => {
      const index = apps.findIndex((app) => app.id === appId);
      if (index < 0) return;
      // issue_024: reorder only within the same ownership group (an admin's own
      // apps vs. other users' apps), so the two subsections stay disjoint. The
      // target is the nearest neighbor in the chosen direction that belongs to
      // the same group; for a non-admin every app is in one group.
      const groupOf = (app: Application) =>
        isAdmin && currentUserId != null
          ? app.created_by_id === currentUserId
          : true;
      const myGroup = groupOf(apps[index]);
      let targetIndex = index + direction;
      while (
        targetIndex >= 0 &&
        targetIndex < apps.length &&
        groupOf(apps[targetIndex]) !== myGroup
      ) {
        targetIndex += direction;
      }
      if (targetIndex < 0 || targetIndex >= apps.length) return;
      if (groupOf(apps[targetIndex]) !== myGroup) return;
      const next = [...apps];
      [next[index], next[targetIndex]] = [next[targetIndex], next[index]];
      setApps(next);
      setError(null);
      try {
        await Promise.all(
          next.map((app, sortOrder) =>
            app.sort_order === sortOrder
              ? Promise.resolve()
              : api.updateApplication(app.id, { sort_order: sortOrder }),
          ),
        );
        await reload();
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Unable to reorder applications.");
        await reload();
      }
    },
    [apps, reload, isAdmin, currentUserId],
  );

  // Run an action that returns the affected application, then surface its push
  // status as a transient notice next to that application's name.
  const runPushAction = useCallback(
    async (
      appId: number,
      action: () => Promise<Application>,
      opts?: { onlyIfChanged?: string | null },
    ) => {
      setError(null);
      setPushBusy((current) => ({ ...current, [appId]: true }));
      setPushMessages((current) => ({ ...current, [appId]: "" }));
      try {
        const result = await action();
        // When onlyIfChanged is provided (a Save), only report a push if the
        // action actually pushed (last_push_at advanced); otherwise stay quiet.
        const pushed =
          opts === undefined ||
          !("onlyIfChanged" in opts) ||
          (result.last_push_at ?? null) !== (opts.onlyIfChanged ?? null);
        if (result && result.last_push_status && pushed) {
          setPushNotices((current) => ({
            ...current,
            [result.id]: result.last_push_status as string,
          }));
          const ok = result.last_push_status === "ok";
          setPushMessages((current) => ({
            ...current,
            [result.id]: ok
              ? "Push completed successfully."
              : `Push finished with status: ${result.last_push_status}.`,
          }));
          window.setTimeout(() => {
            setPushMessages((current) => {
              const next = { ...current };
              delete next[result.id];
              return next;
            });
          }, 5000);
        }
        await reload();
      } catch (err) {
        setPushMessages((current) => ({
          ...current,
          [appId]: err instanceof ApiError ? err.message : "Push failed.",
        }));
        setError(
          err instanceof ApiError ? err.message : "The operation failed.",
        );
      } finally {
        setPushBusy((current) => ({ ...current, [appId]: false }));
      }
    },
    [reload],
  );

  // issue_025: save an application, then surface the reverse-proxy push outcome
  // ONLY when the save actually triggered a push (an admin/self-service alias
  // config change auto-pushes server-side; a plain metadata edit does not). We
  // detect a real push by a change in last_push_at, so a residual prior status
  // is never reported as if this save had pushed.
  const runSaveAction = useCallback(
    (appId: number, action: () => Promise<Application>) => {
      const before = apps.find((a) => a.id === appId)?.last_push_at ?? null;
      return runPushAction(appId, action, {
        onlyIfChanged: before,
      });
    },
    [apps, runPushAction],
  );

  if (loading) {
    return <p role="status">Loading applications…</p>;
  }

  return (
    <div className="stack wide">
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}

      {creating ? (
        <CreateApplicationCard
          isAdmin={isAdmin}
          teamOptions={teamOptions}
          currentUserId={props.currentUser?.id}
          defaultAppsServer={defaultAliasAppsServer(props.currentUser)}
          onCreated={(created) => {
            setCreating(false);
            if (created && created.last_push_status) {
              setPushNotices((current) => ({
                ...current,
                [created.id]: created.last_push_status as string,
              }));
            }
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
            <span className="btn-label">New application</span>
          </button>
        </div>
      )}

      <div className="manager-toolbar list-filter-row">
        <input
          type="search"
          className="list-filter"
          placeholder="Filter applications…"
          aria-label="Filter applications"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>

      {(() => {
        const filtering = filter.trim().length > 0;
        // Reordering is only meaningful over the full list; hide the arrows
        // while a filter is active (a filtered subset would move ambiguously).
        const renderRow = (
          app: Application,
          group: Application[],
          index: number,
        ) => (
          <ApplicationRow
            key={app.id}
            app={app}
            isAdmin={isAdmin}
            defaultAppsServer={defaultAliasAppsServer(props.currentUser)}
            showReorder={!filtering}
            canMoveUp={index > 0}
            canMoveDown={index < group.length - 1}
            onMoveUp={() => void moveApp(app.id, -1)}
            onMoveDown={() => void moveApp(app.id, 1)}
            teamOptions={teamOptions}
            onSave={(input) =>
              // issue_025: surface the reverse-proxy push outcome only when the
              // save actually pushed (see runSaveAction).
              runSaveAction(app.id, async () => {
                return await api.updateApplication(app.id, input);
              })
            }
            onToggleActive={() =>
              runAction(async () => {
                await api.updateApplication(app.id, {
                  is_active: !app.is_active,
                });
              })
            }
            onSetApproval={(status) =>
              runPushAction(app.id, async () => {
                return await api.updateApplication(app.id, {
                  approval_status: status,
                });
              })
            }
            onRetryPush={() =>
              runPushAction(app.id, async () => {
                return await api.retryApplicationPush(app.id);
              })
            }
            pushNotice={pushNotices[app.id]}
            pushBusy={Boolean(pushBusy[app.id])}
            pushMessage={pushMessages[app.id]}
            ownerOptions={ownerOptions}
            initiallyEditing={editAppId === app.id}
            onDelete={() =>
              runAction(async () => {
                await api.deleteApplication(app.id);
              })
            }
          />
        );

        const renderList = (group: Application[], emptyText: string) => {
          const shown = group.filter((a) => appMatches(a, filter));
          if (shown.length === 0) {
            return <p className="muted">{emptyText}</p>;
          }
          // Move arrows are computed against the FULL group (not the filtered
          // subset); while filtering they're hidden anyway.
          return (
            <div className="user-list">
              {shown.map((app) =>
                renderRow(app, group, group.indexOf(app)),
              )}
            </div>
          );
        };

        // issue_024: for an admin whose identity is known, show their own
        // applications first, then everyone else's; each keeps its sort_order
        // and reorders in-group.
        if (isAdmin && currentUserId != null) {
          const mine = apps.filter((a) => a.created_by_id === currentUserId);
          const others = apps.filter((a) => a.created_by_id !== currentUserId);
          return (
            <>
              <section className="card">
                <h2>My applications</h2>
                {mine.length === 0 ? (
                  <p className="muted">You have not created any applications.</p>
                ) : (
                  renderList(mine, "No applications match the filter.")
                )}
              </section>
              <section className="card">
                <h2>Other users' applications</h2>
                {others.length === 0 ? (
                  <p className="muted">No other applications.</p>
                ) : (
                  renderList(others, "No applications match the filter.")
                )}
              </section>
            </>
          );
        }

        // Admin without a known identity (e.g. in isolation), or non-admin.
        return (
          <section className="card">
            <h2>{isAdmin ? "All applications" : "Your applications"}</h2>
            {apps.length === 0 ? (
              <p className="muted">
                {isAdmin
                  ? "No applications yet. Create one above."
                  : "You have not submitted any applications yet."}
              </p>
            ) : (
              renderList(apps, "No applications match the filter.")
            )}
          </section>
        );
      })()}
    </div>
  );
}

function ApprovalBadge({ status }: { status: ApprovalStatus }) {
  const className =
    status === "approved"
      ? "status-badge ok"
      : status === "pending"
        ? "status-badge warn"
        : "status-badge rejected";
  return <span className={className}>{status}</span>;
}

/** Badge colour for a reverse-proxy push status. */
function pushBadgeClass(status: string): string {
  if (status === "ok") return "ok";
  if (status === "skipped") return "warn";
  return "rejected";
}

function publisherLabel(username: string | null | undefined): string {
  if (!username) return "unknown";
  return username.split("@")[0] || username;
}

function TeamCheckboxes(props: {
  options: readonly string[];
  selected: string[];
  onToggle: (team: string) => void;
  onSetAll: (teams: string[]) => void;
  disabled?: boolean;
}) {
  const allSelected =
    props.options.length > 0 && props.selected.length === props.options.length;
  return (
    <fieldset className="team-picker" disabled={props.disabled}>
      <legend>Teams</legend>
      {props.options.length === 0 ? (
        <p className="muted">You are not a member of any team.</p>
      ) : (
        <>
          <div className="team-picker-actions">
            <button
              type="button"
              className="btn ghost btn-sm"
              onClick={() =>
                props.onSetAll(allSelected ? [] : [...props.options])
              }
            >
              {allSelected ? "Clear all" : "Select all"}
            </button>
          </div>
          <div className="team-grid">
            {props.options.map((team) => (
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
        </>
      )}
    </fieldset>
  );
}

function UserSharingFields(props: {
  users: ApplicationShareUser[];
  onChange: (users: ApplicationShareUser[]) => void;
  disabled?: boolean;
}) {
  const [identity, setIdentity] = useState("");
  const [status, setStatus] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [checking, setChecking] = useState(false);
  async function verify() {
    if (!identity.trim()) return;
    setChecking(true); setStatus(null);
    try {
      const found = await api.resolveShareUser(identity.trim());
      if (!props.users.some(user => user.id === found.id)) props.onChange([...props.users, found]);
      setIdentity("");
      setStatus({ tone: "success", text: `Verified ${found.user_id} (${found.username})` });
    } catch (err) {
      setStatus({ tone: "error", text: err instanceof ApiError ? err.message : "User does not exist." });
    } finally { setChecking(false); }
  }
  return <fieldset className="user-share-card" disabled={props.disabled}>
    <legend>Share with specific users</legend>
    <div className="user-share-input"><input value={identity} onChange={event => setIdentity(event.target.value)} placeholder="Username or user ID"/><button type="button" className="btn ghost" onClick={() => void verify()} disabled={checking || !identity.trim()}>{checking ? "Checking…" : "Verify and add"}</button></div>
    {status && <p className={`alert ${status.tone}`}>{status.text}</p>}
    <div className="user-share-list">{props.users.map(user => <span className="user-share-chip" key={user.id}><strong>{user.user_id}</strong><small>{user.username}</small><button type="button" aria-label={`Remove ${user.user_id}`} onClick={() => props.onChange(props.users.filter(item => item.id !== user.id))}>×</button></span>)}</div>
  </fieldset>;
}

/**
 * Grouped access controls for an application, shown together so the permission
 * model is clear: mark the app Private (owner + admins only), or share it with
 * whole Teams and/or specific Users. Private disables and clears both sharing
 * controls. Selecting a specific user marks the app as an AppManager-mediated
 * type (alias/embedded) with authentication required.
 */
function PermissionsFields(props: {
  isPrivate: boolean;
  onPrivateChange: (checked: boolean) => void;
  teamOptions: readonly string[];
  teams: string[];
  onToggleTeam: (team: string) => void;
  onSetTeams: (teams: string[]) => void;
  users: ApplicationShareUser[];
  onUsersChange: (users: ApplicationShareUser[]) => void;
}) {
  return (
    <fieldset className="permissions-group">
      <legend>Permissions</legend>
      <p className="muted permissions-hint">
        Control who can access this application: keep it private, or share it
        with teams and/or specific users.
      </p>
      <Toggle
        checked={props.isPrivate}
        onChange={props.onPrivateChange}
        label="Private application (only you)"
      />
      <TeamCheckboxes
        options={props.teamOptions}
        selected={props.teams}
        onToggle={props.onToggleTeam}
        onSetAll={props.onSetTeams}
        disabled={props.isPrivate}
      />
      <UserSharingFields
        users={props.users}
        onChange={props.onUsersChange}
        disabled={props.isPrivate}
      />
    </fieldset>
  );
}

/**
 * The deployment base URL shown as a greyed prefix before a local alias, e.g.
 * `https://server/home/`. Derived from the document base URI (the backend
 * injects a matching `<base href>`), so it reflects the real origin and path
 * prefix the alias will resolve against.
 */
function deploymentBase(): string {
  try {
    return new URL(".", document.baseURI).toString();
  } catch {
    return "/";
  }
}

function defaultAliasAppsServer(user?: ApiUser | null): string {
  return user?.apps_server || user?.apps_server_ip || "";
}

/** The value to prefill an alias-upstream host field with, before the user
 * has picked anything themselves.
 *
 * issue_021: the account-level apps_server/apps_server_ip (``literal``) is
 * now only a *reference* (e.g. the name of a template picked at account
 * creation) -- it is no longer safe to assume it is itself a connectable
 * host. It is only used directly when it already matches a known,
 * resolved apps-server option's host (a legitimate real address), or when
 * the owner has no provisioned apps-server servers at all (nothing better
 * to offer, so the pre-issue_021 behavior is kept as a best effort).
 * Otherwise the field is left blank: the dropdown still offers an explicit
 * pick, and an untouched blank value resolves correctly at alias-push time
 * via the backend's resolve_user_apps_server_host instead of silently
 * saving a non-resolvable reference string as if it were a host.
 */
function defaultAppsServerValue(
  literal: string,
  options: readonly AppsServerOption[],
): string {
  if (!literal || options.length === 0) return literal;
  return options.some((o) => o.value === literal) ? literal : "";
}

function normalizeAppsPath(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "";
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

function aliasUpstreamPreview(
  protocol: "http" | "https",
  host: string,
  port: string,
  path: string,
): string {
  const cleanHost = host.trim();
  const cleanPort = port.trim();
  if (!cleanHost || !cleanPort) return "Complete host and port to preview the upstream URL.";
  return `${protocol}://${cleanHost}:${cleanPort}${normalizeAppsPath(path) || "/"}`;
}

/** An owner's apps-server server, offered by name in the upstream dropdown;
 * the value pushed/stored is the resolvable host (issue_021). */
export type AppsServerOption = { label: string; value: string };

/** The given owner's apps-server servers, as dropdown options.
 *
 * issue_021: replaces the old template-name list with the owner's actual
 * provisioned apps-server servers -- the dropdown shows each server's name,
 * but the value stored/pushed is its resolvable host (hostname, else IP).
 * Servers without a usable host, or that failed provisioning, are excluded.
 */
async function fetchOwnerAppsServers(ownerId: number): Promise<AppsServerOption[]> {
  const servers = await api.listUserServers(ownerId);
  return servers
    .filter(
      (s) =>
        s.is_apps_server && s.status !== "failed" && (s.hostname || s.ip_address),
    )
    .map((s) => ({ label: s.name, value: s.hostname || s.ip_address }));
}

/** All of the given owner's non-failed servers with a usable host, offered as
 * dropdown options for an embedded application's source.
 *
 * Unlike {@link fetchOwnerAppsServers}, this is NOT restricted to apps-server
 * templates: an embedded app may be sourced from any server the user controls.
 * The value is the resolvable host (hostname, else IP); failed servers and
 * servers without a host are excluded. The backend enforces the same
 * membership, so this dropdown is a convenience, not the only guard.
 */
async function fetchOwnerServers(ownerId: number): Promise<AppsServerOption[]> {
  const servers = await api.listUserServers(ownerId);
  return servers
    .filter((s) => s.status !== "failed" && (s.hostname || s.ip_address))
    .map((s) => ({ label: s.name, value: s.hostname || s.ip_address }));
}

/** Compose an embedded source URL from its parts. Returns "" when the host or
 * port is missing (an incomplete selection cannot form a valid URL). */
function composeEmbeddedUrl(
  protocol: "http" | "https",
  host: string,
  port: string,
  path: string,
): string {
  const cleanHost = host.trim();
  const cleanPort = port.trim();
  if (!cleanHost || !cleanPort) return "";
  return `${protocol}://${cleanHost}:${cleanPort}${normalizeAppsPath(path)}`;
}

/** Best-effort decomposition of a stored embedded URL into its parts, used to
 * prefill the edit form's dropdown/inputs. Falls back to sensible defaults when
 * the stored value cannot be parsed.
 *
 * The port is left EMPTY when the URL carries none (rather than synthesising a
 * protocol default), and the path is preserved verbatim, so that a subsequent
 * composeEmbeddedUrl over the returned parts is a stable round-trip for URLs
 * this form produced (protocol://host:port[/path], no trailing slash). */
function parseEmbeddedUrl(url: string): {
  protocol: "http" | "https";
  host: string;
  port: string;
  path: string;
} {
  try {
    const parsed = new URL(url);
    const protocol = parsed.protocol === "https:" ? "https" : "http";
    // A lone "/" path is treated as "no path" so it composes back identically;
    // any other path (including one with a trailing slash) is kept verbatim.
    const path = parsed.pathname === "/" ? "" : parsed.pathname;
    return { protocol, host: parsed.hostname, port: parsed.port, path };
  } catch {
    return { protocol: "http", host: "", port: "", path: "" };
  }
}

/** Server dropdown + protocol/port/path for an embedded application's source.
 * The source host must be one of the owner's own servers (also enforced
 * server-side), so there is no free-text/Custom fallback. */
function EmbeddedSourceFields(props: {
  protocol: "http" | "https";
  host: string;
  port: string;
  path: string;
  servers: readonly AppsServerOption[];
  onProtocolChange: (value: "http" | "https") => void;
  onHostChange: (value: string) => void;
  onPortChange: (value: string) => void;
  onPathChange: (value: string) => void;
}) {
  if (props.servers.length === 0) {
    return (
      <div className="field">
        <span>Embedded source server</span>
        <span className="muted logo-hint">
          You have no servers yet. Provision a server first; an embedded
          application can only be sourced from one of your own servers.
        </span>
      </div>
    );
  }
  return (
    <div className="field">
      <span>Embedded source</span>
      <div className="alias-upstream-grid">
        <label className="field compact-field">
          <span>Protocol</span>
          <select
            value={props.protocol}
            onChange={(e) =>
              props.onProtocolChange(e.target.value === "https" ? "https" : "http")
            }
            aria-label="Embedded source protocol"
          >
            <option value="http">http</option>
            <option value="https">https</option>
          </select>
        </label>
        <label className="field compact-field alias-upstream-host">
          <span>Server</span>
          <select
            value={props.host}
            onChange={(e) => props.onHostChange(e.target.value)}
            aria-label="Embedded source server"
            required
          >
            <option value="" disabled>
              Select a server…
            </option>
            {props.servers.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field compact-field alias-upstream-port">
          <span>Port</span>
          <input
            type="text"
            value={props.port}
            onChange={(e) => props.onPortChange(e.target.value.replace(/[^0-9]/g, ""))}
            placeholder="3000"
            inputMode="numeric"
            aria-label="Embedded source port"
            required
          />
        </label>
        <label className="field compact-field alias-upstream-path">
          <span>Suffix/path (optional)</span>
          <input
            type="text"
            value={props.path}
            onChange={(e) => props.onPathChange(e.target.value)}
            placeholder="/app"
            aria-label="Embedded source suffix path"
          />
        </label>
      </div>
      <span className="muted logo-hint">
        Preview:{" "}
        <code>
          {composeEmbeddedUrl(props.protocol, props.host, props.port, props.path) ||
            "Select a server and port to preview the source URL."}
        </code>
        . Rendered inside the portal (iframe); reachable only from the Embedded
        apps sidebar after login. The source must allow being embedded in a
        frame. When AppManager is served over HTTPS, use an <code>https</code>{" "}
        source: an <code>http</code> source is auto-upgraded by the browser and
        will not load if the server has no TLS.
      </span>
    </div>
  );
}

function AliasUpstreamFields(props: {
  protocol: "http" | "https";
  host: string;
  port: string;
  path: string;
  appsServers: readonly AppsServerOption[];
  onProtocolChange: (value: "http" | "https") => void;
  onHostChange: (value: string) => void;
  onPortChange: (value: string) => void;
  onPathChange: (value: string) => void;
}) {
  const CUSTOM = "__custom__";
  // The dropdown reflects a known apps server when the current host matches one;
  // otherwise it falls back to Custom with a free-text field (also the default
  // for an empty host, so manual entry stays available).
  const isKnown = props.appsServers.some((s) => s.value === props.host);
  const selectValue = isKnown ? props.host : CUSTOM;
  const showCustom = !isKnown || props.appsServers.length === 0;
  return (
    <div className="field">
      <span>Alias upstream</span>
      <div className="alias-upstream-grid">
        <label className="field compact-field">
          <span>Protocol</span>
          <select
            value={props.protocol}
            onChange={(e) =>
              props.onProtocolChange(e.target.value === "https" ? "https" : "http")
            }
            aria-label="Alias upstream protocol"
          >
            <option value="http">http</option>
            <option value="https">https</option>
          </select>
        </label>
        <label className="field compact-field alias-upstream-host">
          <span>Apps server</span>
          <select
            value={selectValue}
            onChange={(e) =>
              props.onHostChange(e.target.value === CUSTOM ? "" : e.target.value)
            }
            aria-label="Alias upstream apps server"
          >
            {props.appsServers.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
            <option value={CUSTOM}>Custom…</option>
          </select>
          {showCustom && (
            <input
              type="text"
              value={props.host}
              onChange={(e) => props.onHostChange(e.target.value)}
              placeholder="apps.example.com"
              aria-label="Alias upstream server host or IP"
              required
            />
          )}
        </label>
        <label className="field compact-field alias-upstream-port">
          <span>Port</span>
          <input
            type="text"
            value={props.port}
            onChange={(e) => props.onPortChange(e.target.value.replace(/[^0-9]/g, ""))}
            placeholder="8080"
            inputMode="numeric"
            aria-label="Alias upstream port"
            required
          />
        </label>
        <label className="field compact-field alias-upstream-path">
          <span>Suffix/path (optional)</span>
          <input
            type="text"
            value={props.path}
            onChange={(e) => props.onPathChange(e.target.value)}
            placeholder="/app"
            aria-label="Alias upstream suffix path"
          />
        </label>
      </div>
      <span className="muted logo-hint">
        Preview: <code>{aliasUpstreamPreview(props.protocol, props.host, props.port, props.path)}</code>
      </span>
    </div>
  );
}

/**
 * Link-type selector plus the address input. The target type is chosen with
 * radio buttons -- "Local alias" (the default for new applications) or "Full
 * URL" -- and only the selected mode's input is editable; the other is disabled
 * and greyed. "Full URL" links are stored and validated as absolute http(s)
 * URLs. "Local alias" links are stored verbatim as a bare relative path and
 * resolved against the deployment base at render time; the base is shown greyed
 * immediately before the input so the user sees the full resulting URL. A
 * leading slash typed into the alias is stripped to match server validation.
 */
function UrlFields(props: {
  urlType: UrlType;
  url: string;
  onUrlTypeChange: (value: UrlType) => void;
  onUrlChange: (value: string) => void;
}) {
  const isAlias = props.urlType === "alias";
  const isEmbedded = props.urlType === "embedded";
  return (
    <div className="url-fields">
      <fieldset className="radio-group">
        <legend>Target type</legend>
        <label className="radio-option">
          <input
            type="radio"
            name="url_type"
            value="alias"
            checked={isAlias}
            onChange={() => props.onUrlTypeChange("alias")}
          />
          <span>Local alias</span>
        </label>
        <label className="radio-option">
          <input
            type="radio"
            name="url_type"
            value="url"
            checked={props.urlType === "url"}
            onChange={() => props.onUrlTypeChange("url")}
          />
          <span>Full URL</span>
        </label>
        <label className="radio-option">
          <input
            type="radio"
            name="url_type"
            value="embedded"
            checked={isEmbedded}
            onChange={() => props.onUrlTypeChange("embedded")}
          />
          <span>Embedded App (private)</span>
        </label>
      </fieldset>

      {isAlias ? (
        <label className="field">
          <span>Local alias (relative path)</span>
          <span className="input-group">
            <span className="input-prefix" aria-hidden="true">
              {deploymentBase()}
            </span>
            <input
              type="text"
              className="input-group-field"
              value={props.url}
              // Strip a leading slash and any disallowed characters so the
              // displayed prefix + alias matches how the server stores and
              // validates it (letters, digits, underscores, and dashes only).
              onChange={(e) =>
                props.onUrlChange(
                  e.target.value.replace(/^\/+/, "").replace(/[^A-Za-z0-9_-]/g, ""),
                )
              }
              placeholder="my-dashboard"
              aria-label="Local alias relative path"
              pattern="[A-Za-z0-9_-]{1,30}"
              maxLength={30}
              required
            />
          </span>
          <span className="muted logo-hint">
            Letters, digits, underscores, and dashes only; maximum 30 characters.
          </span>
        </label>
      ) : isEmbedded ? (
        // Embedded source is composed from a server dropdown rendered by the
        // form (EmbeddedSourceFields); nothing to show inline here.
        null
      ) : (
        <label className="field">
          <span>URL</span>
          <input
            type="url"
            value={props.url}
            onChange={(e) => props.onUrlChange(e.target.value)}
            placeholder="https://example.com/app"
            aria-label="Full URL address"
            required
          />
        </label>
      )}
    </div>
  );
}

/**
 * Accessible on/off switch. Rendered as a `role="switch"` button so it is a
 * clear toggle (not a checkbox) and is keyboard/space-operable.
 */
function Toggle(props: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={props.checked}
      aria-label={props.label}
      disabled={props.disabled}
      className={props.checked ? "toggle-switch on" : "toggle-switch"}
      onClick={() => props.onChange(!props.checked)}
    >
      <span className="toggle-track" aria-hidden="true">
        <span className="toggle-thumb" />
      </span>
      <span className="toggle-label">{props.label}</span>
    </button>
  );
}

function AliasAuthField(props: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="field">
      <Toggle
        checked={props.checked}
        onChange={props.onChange}
        disabled={props.disabled}
        label="Require AppManager authentication"
      />
      {!props.checked && (
        <span className="alert warn" role="alert">
          This alias will be reachable without an AppManager session. Only disable
          this if the upstream app has its own authentication or is safe to expose.
        </span>
      )}
    </div>
  );
}

/**
 * Logo chooser. A user may upload an image (downscaled in the browser to a small
 * square data URI) or paste an absolute image URL. When neither is provided, a
 * default logo from the bundled catalogue is assigned at create time. The
 * current value (whether an uploaded data URI or a URL) is shown as a small
 * preview and can be cleared.
 */
function LogoField(props: {
  value: string;
  onChange: (value: string) => void;
  onError: (message: string) => void;
}) {
  const [uploading, setUploading] = useState(false);
  const isUpload = props.value.startsWith("data:");

  async function onFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    // Reset the input so selecting the same file again still fires onChange.
    event.target.value = "";
    if (!file) {
      return;
    }
    setUploading(true);
    try {
      props.onChange(await fileToLogoDataUrl(file));
    } catch (err) {
      props.onError(
        err instanceof Error ? err.message : "Could not process that image.",
      );
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="field logo-field">
      <span>Logo (optional)</span>
      <div className="logo-field-row">
        <span className="logo-preview" aria-hidden="true">
          {props.value ? (
            <img src={resolveIconSrc(props.value)} alt="" width={32} height={32} />
          ) : (
            <span className="logo-preview-empty">—</span>
          )}
        </span>
        <div className="logo-field-controls">
          <input
            type="file"
            accept="image/png,image/webp,image/jpeg"
            onChange={onFileChange}
            aria-label="Upload logo image"
          />
          {!isUpload && (
            <input
              type="url"
              value={props.value}
              onChange={(e) => props.onChange(e.target.value)}
              placeholder="https://example.com/icon.svg"
              aria-label="Logo image URL"
            />
          )}
          {props.value && (
            <button
              type="button"
              className="btn ghost"
              onClick={() => props.onChange("")}
            >
              {uploading ? "…" : "Clear logo"}
            </button>
          )}
        </div>
      </div>
      <p className="muted logo-hint">
        Upload a PNG, WebP, or JPEG (auto-resized), or paste an image URL. A
        default logo is used when left blank.
      </p>
    </div>
  );
}

function CreateApplicationCard(props: {
  isAdmin: boolean;
  teamOptions: readonly string[];
  defaultAppsServer: string;
  /** New applications are always owned by their creator (no create-time
   * owner picker), so the apps-server dropdown loads this user's servers. */
  currentUserId?: number;
  onCreated: (created: Application) => void;
  onCancel: () => void;
  onError: (message: string) => void;
}) {
  const [name, setName] = useState("");
  const [urlType, setUrlType] = useState<UrlType>("alias");
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [iconUrl, setIconUrl] = useState("");
  const [teams, setTeams] = useState<string[]>([]);
  const [appsProtocol, setAppsProtocol] = useState<"http" | "https">("http");
  const [appsPort, setAppsPort] = useState("");
  const [appsServer, setAppsServer] = useState(props.defaultAppsServer);
  const [appsPath, setAppsPath] = useState("");
  const [aliasAuthRequired, setAliasAuthRequired] = useState(true);
  const [isPrivate, setIsPrivate] = useState(false);
  const [sharedUsers, setSharedUsers] = useState<ApplicationShareUser[]>([]);
  const [busy, setBusy] = useState(false);
  const [appsServerOptions, setAppsServerOptions] = useState<AppsServerOption[]>([]);
  // Embedded-app source parts. The URL is composed from these on submit; the
  // server is picked from the owner's own servers (enforced server-side).
  const [embeddedProtocol, setEmbeddedProtocol] = useState<"http" | "https">("http");
  const [embeddedServer, setEmbeddedServer] = useState("");
  const [embeddedPort, setEmbeddedPort] = useState("");
  const [embeddedPath, setEmbeddedPath] = useState("");
  const [embeddedServerOptions, setEmbeddedServerOptions] = useState<AppsServerOption[]>([]);

  useEffect(() => {
    let active = true;
    if (!props.currentUserId) {
      setAppsServerOptions([]);
      setEmbeddedServerOptions([]);
      return () => {
        active = false;
      };
    }
    fetchOwnerAppsServers(props.currentUserId)
      .then((options) => {
        if (!active) return;
        setAppsServerOptions(options);
        // Re-resolve the prefilled default now that the owner's real
        // apps-server hosts are known -- but only if the field is still
        // untouched, so we never clobber a value the user already picked
        // or typed.
        setAppsServer((current) =>
          current === props.defaultAppsServer
            ? defaultAppsServerValue(props.defaultAppsServer, options)
            : current,
        );
      })
      .catch(() => {
        if (active) setAppsServerOptions([]);
      });
    fetchOwnerServers(props.currentUserId)
      .then((options) => {
        if (active) setEmbeddedServerOptions(options);
      })
      .catch(() => {
        if (active) setEmbeddedServerOptions([]);
      });
    return () => {
      active = false;
    };
  }, [props.currentUserId]);

  // Each alias application has its own upstream target. The host is prefilled
  // from the signed-in user's configured apps server when available.
  const showPort = urlType === "alias";
  const showServer = urlType === "alias";
  const isEmbedded = urlType === "embedded";

  function toggleTeam(team: string) {
    setTeams((current) =>
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
      const appName = name.trim();
      // Precedence: an uploaded/typed logo wins; otherwise assign a default
      // from the bundled catalogue so every card shows a logo.
      const icon = iconUrl.trim() || defaultLogoFor(appName, teams);
      // Embedded apps compose their source URL from the server dropdown parts
      // and carry everything in `url` (no apps_* fields). Other types send the
      // url field as typed.
      const resolvedUrl = isEmbedded
        ? composeEmbeddedUrl(embeddedProtocol, embeddedServer, embeddedPort, embeddedPath)
        : url.trim();
      if (isEmbedded && !resolvedUrl) {
        props.onError("Select a server and port for the embedded source.");
        setBusy(false);
        return;
      }
      const created = await api.createApplication({
        name: appName,
        url: resolvedUrl,
        url_type: urlType,
        description: description.trim(),
        icon_url: icon,
        is_private: isPrivate,
        shared_user_ids: isPrivate ? [] : sharedUsers.map(user => user.id),
        teams: isPrivate ? [] : teams,
        alias_auth_required: urlType === "alias" ? (isPrivate || sharedUsers.length > 0 ? true : aliasAuthRequired) : true,
        ...(showPort ? { apps_protocol: appsProtocol } : {}),
        ...(showPort ? { apps_port: appsPort.trim() } : {}),
        ...(showServer ? { apps_server: appsServer.trim() } : {}),
        ...(showPort ? { apps_path: normalizeAppsPath(appsPath) } : {}),
      });
      props.onCreated(created);
    } catch (err) {
      props.onError(
        err instanceof ApiError
          ? err.message
          : "Unable to create the application.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>New application</h2>
      <form className="create-form" onSubmit={onSubmit}>
        <label className="field">
          <span>Name</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </label>

        <UrlFields
          urlType={urlType}
          url={url}
          onUrlTypeChange={value => {
            // Private/user-restricted apps must be a mediated type (alias or
            // embedded); block switching to a plain full URL while restricted.
            if ((isPrivate || sharedUsers.length > 0) && value === "url") return;
            setUrlType(value);
          }}
          onUrlChange={setUrl}
        />

        {isEmbedded && (
          <EmbeddedSourceFields
            protocol={embeddedProtocol}
            host={embeddedServer}
            port={embeddedPort}
            path={embeddedPath}
            servers={embeddedServerOptions}
            onProtocolChange={setEmbeddedProtocol}
            onHostChange={setEmbeddedServer}
            onPortChange={setEmbeddedPort}
            onPathChange={setEmbeddedPath}
          />
        )}

        {showPort && (
          <AliasUpstreamFields
            protocol={appsProtocol}
            host={appsServer}
            port={appsPort}
            path={appsPath}
            appsServers={appsServerOptions}
            onProtocolChange={setAppsProtocol}
            onHostChange={setAppsServer}
            onPortChange={setAppsPort}
            onPathChange={setAppsPath}
          />
        )}

        {showPort && (
          <AliasAuthField
            checked={aliasAuthRequired}
            onChange={setAliasAuthRequired}
            disabled={isPrivate || sharedUsers.length > 0}
          />
        )}

        <label className="field">
          <span>Description</span>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>

        <LogoField
          value={iconUrl}
          onChange={setIconUrl}
          onError={props.onError}
        />

        <PermissionsFields
          isPrivate={isPrivate}
          onPrivateChange={(checked) => {
            setIsPrivate(checked);
            if (checked) {
              // Private requires a mediated type; keep embedded, else use alias.
              if (urlType === "url") setUrlType("alias");
              setAliasAuthRequired(true);
            }
          }}
          teamOptions={props.teamOptions}
          teams={teams}
          onToggleTeam={toggleTeam}
          onSetTeams={setTeams}
          users={sharedUsers}
          onUsersChange={(users) => {
            setSharedUsers(users);
            if (users.length) {
              if (urlType === "url") setUrlType("alias");
              setAliasAuthRequired(true);
            }
          }}
        />

        <div className="row-actions">
          <button
            type="submit"
            className="btn primary"
            disabled={
              busy ||
              name.trim().length === 0 ||
              // Embedded apps derive their URL from the server dropdown parts,
              // so require a composable source there instead of the url field.
              (isEmbedded
                ? composeEmbeddedUrl(
                    embeddedProtocol,
                    embeddedServer,
                    embeddedPort,
                    embeddedPath,
                  ).length === 0
                : url.trim().length === 0)
            }
          >
            {busy ? "Creating…" : "Create application"}
          </button>
          <button type="button" className="btn ghost" onClick={props.onCancel}>
            Cancel
          </button>
        </div>
      </form>
    </section>
  );
}

function ApplicationRow(props: {
  app: Application;
  isAdmin: boolean;
  defaultAppsServer: string;
  canMoveUp: boolean;
  canMoveDown: boolean;
  /** issue_024: when false, the reorder arrows are omitted (e.g. while a
   * filter is active, since reordering a filtered subset is ambiguous). */
  showReorder?: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
  teamOptions: readonly string[];
  onSave: (input: UpdateApplicationInput) => void;
  onToggleActive: () => void;
  onSetApproval: (status: ApprovalStatus) => void;
  onRetryPush: () => void;
  onDelete: () => void;
  /** Transient reverse-proxy push status shown once next to the name. */
  pushNotice?: string;
  pushBusy: boolean;
  pushMessage?: string;
  ownerOptions: readonly ApiUser[];
  initiallyEditing?: boolean;
}) {
  const { app, isAdmin } = props;
  const [editing, setEditing] = useState(Boolean(props.initiallyEditing));
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [showPushLog, setShowPushLog] = useState(false);
  const [aliasConfigMessage, setAliasConfigMessage] = useState("");
  const [name, setName] = useState(app.name);
  const [urlType, setUrlType] = useState<UrlType>(app.url_type);
  const [url, setUrl] = useState(app.url);
  const [description, setDescription] = useState(app.description);
  const [iconUrl, setIconUrl] = useState(app.icon_url);
  const [teams, setTeams] = useState<string[]>(app.teams);
  const [appsProtocol, setAppsProtocol] = useState<"http" | "https">(
    app.apps_protocol ?? "http",
  );
  const [appsPort, setAppsPort] = useState(app.apps_port ?? "");
  const [appsServer, setAppsServer] = useState(
    app.apps_server || props.defaultAppsServer,
  );
  const [appsPath, setAppsPath] = useState(app.apps_path ?? "");
  const [aliasAuthRequired, setAliasAuthRequired] = useState(
    app.alias_auth_required,
  );
  const [isPrivate, setIsPrivate] = useState(!!app.is_private);
  const [sharedUsers, setSharedUsers] = useState<ApplicationShareUser[]>(app.shared_users ?? []);
  const [ownerId, setOwnerId] = useState(String(app.created_by_id ?? ""));
  const [logoError, setLogoError] = useState<string | null>(null);
  const [appsServerOptions, setAppsServerOptions] = useState<AppsServerOption[]>([]);
  // Embedded-app source parts, prefilled from the stored composed URL.
  const initialEmbedded = parseEmbeddedUrl(app.url_type === "embedded" ? app.url : "");
  const [embeddedProtocol, setEmbeddedProtocol] = useState<"http" | "https">(
    initialEmbedded.protocol,
  );
  const [embeddedServer, setEmbeddedServer] = useState(initialEmbedded.host);
  const [embeddedPort, setEmbeddedPort] = useState(initialEmbedded.port);
  const [embeddedPath, setEmbeddedPath] = useState(initialEmbedded.path);
  const [embeddedServerOptions, setEmbeddedServerOptions] = useState<AppsServerOption[]>([]);

  // issue_021: the apps-server dropdown reflects the *current* owner's
  // servers -- for a non-admin this is always self (ownerId never changes,
  // there's no owner picker); for an admin it reloads whenever they pick a
  // different owner in the Owner select below.
  useEffect(() => {
    let active = true;
    const idNum = Number(ownerId);
    if (!ownerId || !Number.isFinite(idNum) || idNum <= 0) {
      setAppsServerOptions([]);
      setEmbeddedServerOptions([]);
      return () => {
        active = false;
      };
    }
    fetchOwnerAppsServers(idNum)
      .then((options) => {
        if (!active) return;
        setAppsServerOptions(options);
        // Only re-resolve when the app itself has no explicit host and the
        // field still holds the untouched account-level default -- never
        // clobber an app's actual saved value or a user's own edit.
        if (app.apps_server) return;
        setAppsServer((current) =>
          current === props.defaultAppsServer
            ? defaultAppsServerValue(props.defaultAppsServer, options)
            : current,
        );
      })
      .catch(() => {
        if (active) setAppsServerOptions([]);
      });
    fetchOwnerServers(idNum)
      .then((options) => {
        if (active) setEmbeddedServerOptions(options);
      })
      .catch(() => {
        if (active) setEmbeddedServerOptions([]);
      });
    return () => {
      active = false;
    };
  }, [ownerId]);

  useEffect(() => {
    if (props.initiallyEditing) {
      setEditing(true);
    }
  }, [props.initiallyEditing]);

  useEffect(() => {
    setName(app.name);
    setUrlType(app.url_type);
    setUrl(app.url);
    setDescription(app.description);
    setIconUrl(app.icon_url);
    setTeams(app.teams);
    setAppsProtocol(app.apps_protocol ?? "http");
    setAppsPort(app.apps_port ?? "");
    setAppsServer(
      app.apps_server ||
        defaultAppsServerValue(props.defaultAppsServer, appsServerOptions),
    );
    setAppsPath(app.apps_path ?? "");
    const emb = parseEmbeddedUrl(app.url_type === "embedded" ? app.url : "");
    setEmbeddedProtocol(emb.protocol);
    setEmbeddedServer(emb.host);
    setEmbeddedPort(emb.port);
    setEmbeddedPath(emb.path);
    setAliasAuthRequired(app.alias_auth_required);
    setIsPrivate(!!app.is_private);
    setSharedUsers(app.shared_users ?? []);
    setOwnerId(String(app.created_by_id ?? ""));
  }, [
    app.name,
    app.url_type,
    app.url,
    app.description,
    app.icon_url,
    app.teams,
    app.apps_protocol,
    app.apps_port,
    app.apps_server,
    app.apps_path,
    app.alias_auth_required,
    app.is_private,
    app.shared_users,
    app.created_by_id,
    props.defaultAppsServer,
  ]);

  const composedEmbeddedUrl = composeEmbeddedUrl(
    embeddedProtocol,
    embeddedServer,
    embeddedPort,
    embeddedPath,
  );
  // Canonicalise the stored URL through the same parse->compose transform so an
  // equivalent-but-differently-formatted stored value (e.g. a lone trailing
  // slash, or an implicit default port) does not spuriously mark the embedded
  // form dirty on open (reviewer M1).
  const canonicalStoredEmbeddedUrl = useMemo(() => {
    if (app.url_type !== "embedded") return app.url;
    const p = parseEmbeddedUrl(app.url);
    return composeEmbeddedUrl(p.protocol, p.host, p.port, p.path);
  }, [app.url, app.url_type]);
  const dirty = useMemo(
    () =>
      name !== app.name ||
      urlType !== app.url_type ||
      (urlType === "embedded"
        ? composedEmbeddedUrl !== canonicalStoredEmbeddedUrl
        : url !== app.url) ||
      description !== app.description ||
      iconUrl !== app.icon_url ||
      appsProtocol !== (app.apps_protocol ?? "http") ||
      appsPort !== (app.apps_port ?? "") ||
      appsServer !==
        (app.apps_server ||
          defaultAppsServerValue(props.defaultAppsServer, appsServerOptions)) ||
      appsPath !== (app.apps_path ?? "") ||
      aliasAuthRequired !== app.alias_auth_required ||
      isPrivate !== !!app.is_private ||
      sharedUsers.length !== (app.shared_users ?? []).length ||
      sharedUsers.some(user => !(app.shared_users ?? []).some(existing => existing.id === user.id)) ||
      ownerId !== String(app.created_by_id ?? "") ||
      teams.length !== app.teams.length ||
      teams.some((t) => !app.teams.includes(t)),
    [
      name,
      urlType,
      url,
      composedEmbeddedUrl,
      canonicalStoredEmbeddedUrl,
      description,
      iconUrl,
      appsProtocol,
      appsPort,
      appsServer,
      appsServerOptions,
      appsPath,
      aliasAuthRequired,
      isPrivate,
      sharedUsers,
      ownerId,
      teams,
      app,
    ],
  );

  // Each alias application has its own upstream target, editable by its owner
  // or an admin.
  const showPort = urlType === "alias";
  const showServer = urlType === "alias";
  const isEmbedded = urlType === "embedded";

  useEffect(() => {
    let active = true;
    if (!editing || app.url_type !== "alias") {
      return () => {
        active = false;
      };
    }
    setAliasConfigMessage("Reading current deployed nginx config…");
    api
      .getApplicationAliasConfig(app.id)
      .then((config) => {
        if (!active) return;
        if (config.status === "ok") {
          setUrl(config.alias || app.url);
          setAppsProtocol(config.apps_protocol || "http");
          setAppsServer(
            config.apps_server ||
              app.apps_server ||
              defaultAppsServerValue(props.defaultAppsServer, appsServerOptions),
          );
          setAppsPort(config.apps_port || app.apps_port || "");
          setAppsPath(config.apps_path || "");
          setAliasAuthRequired(config.alias_auth_required);
          setAliasConfigMessage("Loaded current deployed config from nginx.");
        } else {
          setAliasConfigMessage(config.log || "Current nginx alias config was not available.");
        }
      })
      .catch((err) => {
        if (!active) return;
        setAliasConfigMessage(
          err instanceof ApiError
            ? err.message
            : "Unable to read current nginx alias config.",
        );
      });
    return () => {
      active = false;
    };
  }, [editing, app.id, app.url_type]);

  function toggleTeam(team: string) {
    setTeams((current) =>
      current.includes(team)
        ? current.filter((t) => t !== team)
        : [...current, team],
    );
  }

  return (
    <article className={app.is_active ? "user-card" : "user-card inactive"}>
      <div className="user-card-head">
        <div className="user-identity">
          <span className="user-name">{app.name}</span>
          <ApprovalBadge status={app.approval_status} />
          <span
            className={
              app.is_active
                ? "status-badge ok status-indicator"
                : "status-badge off status-indicator"
            }
          >
            {app.is_active ? "enabled" : "disabled"}
          </span>
          {app.pending_alias && (
            <span
              className="status-badge warn"
              title={`Alias change to "${app.pending_alias}" awaiting approval`}
            >
              alias change pending
            </span>
          )}
          {app.pending_is_active !== null && app.pending_is_active !== undefined && (
            <span className="status-badge warn push-needed">
              {app.pending_is_active ? "enable requested" : "disable requested"}
            </span>
          )}
          {app.pending_alias_auth_required !== null &&
            app.pending_alias_auth_required !== undefined && (
              <span className="status-badge warn push-needed">
                {app.pending_alias_auth_required
                  ? "auth enable requested"
                  : "auth exclusion requested"}
              </span>
            )}
          {app.url_type === "alias" && !app.alias_auth_required && (
            <span
              className="status-badge warn"
              title="This alias does not require an AppManager session."
            >
              unprotected alias
            </span>
          )}
          {isAdmin && app.needs_push && (
            <span className="status-badge rejected push-needed">
              proxy config changed - push required
            </span>
          )}
          {props.pushNotice && (
            <span
              className={`status-badge ${pushBadgeClass(props.pushNotice)} push-notice`}
              role="status"
            >
              proxy: {props.pushNotice}
            </span>
          )}
          {isAdmin && app.created_by && (
            <span className="muted created-by">by {app.created_by}</span>
          )}
        </div>
        <div className="row-actions">
          {props.showReorder !== false && (
            <>
              <button
                type="button"
                className="btn ghost btn-sm"
                onClick={props.onMoveUp}
                disabled={!props.canMoveUp}
                title="Move up"
                aria-label={`Move ${app.name} up`}
              >
                ↑
              </button>
              <button
                type="button"
                className="btn ghost btn-sm"
                onClick={props.onMoveDown}
                disabled={!props.canMoveDown}
                title="Move down"
                aria-label={`Move ${app.name} down`}
              >
                ↓
              </button>
            </>
          )}
          <button
            type="button"
            className="btn ghost"
            onClick={() => setEditing((v) => !v)}
          >
            {editing ? "Close" : "Edit"}
          </button>
          <button
            type="button"
            className={app.is_active ? "btn disable" : "btn enable"}
            onClick={props.onToggleActive}
          >
            {app.is_active ? "Disable" : "Enable"}
          </button>
          {/* Delete lives in the always-visible action row for quick access.
              When the card is expanded, the editor footer owns the Delete UI
              instead (both share `confirmingDelete`), so only render it here
              while collapsed to avoid two confirm prompts at once. */}
          {!editing &&
            (confirmingDelete ? (
              <span className="confirm-inline">
                <span>Delete {app.name}?</span>
                <button
                  type="button"
                  className="btn danger"
                  onClick={() => {
                    props.onDelete();
                    setConfirmingDelete(false);
                  }}
                >
                  Confirm delete
                </button>
                <button
                  type="button"
                  className="btn ghost"
                  onClick={() => setConfirmingDelete(false)}
                >
                  Cancel
                </button>
              </span>
            ) : (
              <button
                type="button"
                className="btn danger"
                onClick={() => setConfirmingDelete(true)}
              >
                Delete
              </button>
            ))}
        </div>
      </div>

      {isAdmin && (
        <div className="row-actions approval-actions">
          {(app.approval_status !== "approved" ||
            app.pending_alias ||
            (app.pending_is_active !== null && app.pending_is_active !== undefined) ||
            (app.pending_alias_auth_required !== null &&
              app.pending_alias_auth_required !== undefined)) && (
            <button
              type="button"
              className="btn approve"
              onClick={() => props.onSetApproval("approved")}
            >
              <CheckIcon />
              <span className="btn-label">Approve</span>
            </button>
          )}
          {app.approval_status === "pending" && (
            <button
              type="button"
              className="btn danger"
              onClick={() => props.onSetApproval("rejected")}
            >
              <XIcon />
              <span className="btn-label">Reject</span>
            </button>
          )}
        </div>
      )}

      {!editing && (
        <>
          <a
            className="app-url muted"
            href={resolveAppHref(app)}
            target="_blank"
            rel="noopener noreferrer"
          >
            {app.url}
          </a>
          {(app.publisher_team || app.created_by) && (
            <div className="tag-row publisher-row">
              {app.publisher_team && (
                <span className="tag publisher-team-tag">Team: {app.publisher_team}</span>
              )}
              {app.created_by && (
                <span className="tag publisher-tag">
                  Published by: {publisherLabel(app.created_by)}
                </span>
              )}
            </div>
          )}
        </>
      )}

      {editing && (
        <div className="user-edit">
          {isAdmin && app.last_push_status && (
            <div className="push-log-block">
              <div className="row-actions">
                <span className={`status-badge ${pushBadgeClass(app.last_push_status)}`}>
                  proxy: {app.last_push_status}
                </span>
                <button
                  type="button"
                  className="btn ghost"
                  onClick={() => setShowPushLog((v) => !v)}
                >
                  {showPushLog ? "Hide push log" : "View push log"}
                </button>
                {app.approval_status === "approved" && app.url_type === "alias" && (
                  <button
                    type="button"
                    className={props.pushBusy ? "btn warn" : "btn approve"}
                    onClick={props.onRetryPush}
                    disabled={props.pushBusy}
                  >
                    {props.pushBusy ? "Pushing…" : "Push"}
                  </button>
                )}
                {props.pushMessage && (
                  <span className="muted push-action-message" role="status">
                    {props.pushMessage}
                  </span>
                )}
              </div>
              {showPushLog && (
                <pre className="push-log">{app.last_push_log || "(empty)"}</pre>
              )}
            </div>
          )}
          <label className="field">
            <span>Name</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>

          <UrlFields
            urlType={urlType}
            url={url}
            onUrlTypeChange={value => {
              if ((isPrivate || sharedUsers.length > 0) && value === "url") return;
              setUrlType(value);
            }}
            onUrlChange={setUrl}
          />

          {isEmbedded && (
            <EmbeddedSourceFields
              protocol={embeddedProtocol}
              host={embeddedServer}
              port={embeddedPort}
              path={embeddedPath}
              servers={embeddedServerOptions}
              onProtocolChange={setEmbeddedProtocol}
              onHostChange={setEmbeddedServer}
              onPortChange={setEmbeddedPort}
              onPathChange={setEmbeddedPath}
            />
          )}

          {showPort && aliasConfigMessage && (
            <p className="muted logo-hint" role="status">
              {aliasConfigMessage}
            </p>
          )}

          {showPort && (
            <AliasUpstreamFields
              protocol={appsProtocol}
              host={appsServer}
              port={appsPort}
              path={appsPath}
              appsServers={appsServerOptions}
              onProtocolChange={setAppsProtocol}
              onHostChange={setAppsServer}
              onPortChange={setAppsPort}
              onPathChange={setAppsPath}
            />
          )}

          {showPort && (
            <AliasAuthField
              checked={aliasAuthRequired}
              onChange={setAliasAuthRequired}
              disabled={isPrivate || sharedUsers.length > 0}
            />
          )}

          {isAdmin && props.ownerOptions.length > 0 && (
            <label className="field">
              <span>Owner</span>
              <select
                value={ownerId}
                onChange={(e) => setOwnerId(e.target.value)}
              >
                {props.ownerOptions.map((owner) => (
                  <option key={owner.id} value={owner.id}>
                    {owner.username}
                  </option>
                ))}
              </select>
            </label>
          )}

          <label className="field">
            <span>Description</span>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          <LogoField
            value={iconUrl}
            onChange={(value) => {
              setLogoError(null);
              setIconUrl(value);
            }}
            onError={setLogoError}
          />
          {logoError && (
            <p className="alert error" role="alert">
              {logoError}
            </p>
          )}

          <PermissionsFields
            isPrivate={isPrivate}
            onPrivateChange={(checked) => {
              setIsPrivate(checked);
              if (checked) {
                if (urlType === "url") setUrlType("alias");
                setAliasAuthRequired(true);
              }
            }}
            teamOptions={props.teamOptions}
            teams={teams}
            onToggleTeam={toggleTeam}
            onSetTeams={setTeams}
            users={sharedUsers}
            onUsersChange={(users) => {
              setSharedUsers(users);
              if (users.length) {
                if (urlType === "url") setUrlType("alias");
                setAliasAuthRequired(true);
              }
            }}
          />

          <div className="row-actions">
            <button
              type="button"
              className="btn primary"
              disabled={!dirty}
              onClick={() => {
                // Embedded apps compose their source URL from the server
                // dropdown parts; other types send url as typed.
                const resolvedUrl = isEmbedded
                  ? composeEmbeddedUrl(
                      embeddedProtocol,
                      embeddedServer,
                      embeddedPort,
                      embeddedPath,
                    )
                  : url;
                if (isEmbedded && !resolvedUrl) {
                  setLogoError(
                    "Select a server and port for the embedded source.",
                  );
                  return;
                }
                props.onSave({
                  name,
                  url: resolvedUrl,
                  url_type: urlType,
                  description,
                  icon_url: iconUrl,
                  teams: isPrivate ? [] : teams,
                  is_private: isPrivate,
                  shared_user_ids: isPrivate ? [] : sharedUsers.map(user => user.id),
                  alias_auth_required: urlType === "alias" ? (isPrivate || sharedUsers.length > 0 ? true : aliasAuthRequired) : true,
                  ...(showPort ? { apps_protocol: appsProtocol } : {}),
                  ...(showPort ? { apps_port: appsPort.trim() } : {}),
                  ...(showServer ? { apps_server: appsServer.trim() } : {}),
                  ...(showPort ? { apps_path: normalizeAppsPath(appsPath) } : {}),
                  ...(isAdmin && ownerId ? { created_by: Number(ownerId) } : {}),
                });
                setEditing(false);
              }}
            >
              Save changes
            </button>
            {/* issue_025: when the approved alias still needs applying to the
                reverse proxy (a config change that wasn't auto-pushed, or a
                failed push), surface a highlighted Push button right next to
                Save so it's the obvious next step. */}
            {isAdmin &&
              app.approval_status === "approved" &&
              app.url_type === "alias" &&
              (app.needs_push || app.last_push_status === "failed") && (
                <button
                  type="button"
                  className="btn approve push-apply"
                  onClick={props.onRetryPush}
                  disabled={props.pushBusy}
                  title="Apply this change to the reverse proxy"
                >
                  {props.pushBusy
                    ? "Pushing…"
                    : "Push to reverse proxy"}
                </button>
              )}
            {props.pushMessage && (
              <span className="muted push-action-message" role="status">
                {props.pushMessage}
              </span>
            )}
            {confirmingDelete ? (
              <span className="confirm-inline">
                <span>Delete {app.name}?</span>
                <button
                  type="button"
                  className="btn danger"
                  onClick={() => {
                    props.onDelete();
                    setConfirmingDelete(false);
                  }}
                >
                  Confirm delete
                </button>
                <button
                  type="button"
                  className="btn ghost"
                  onClick={() => setConfirmingDelete(false)}
                >
                  Cancel
                </button>
              </span>
            ) : (
              <button
                type="button"
                className="btn danger"
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
