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

/** issue_local_032: the bucket an application's reordering is confined to --
 * the same visible ownership group (an admin's own apps vs. everyone else's)
 * AND the same approval status, since both are used to group/sort the
 * management list and crossing either boundary would appear to save and then
 * silently "snap back" (management lists sort by approval status first). */
function appBucketKey(
  app: Application,
  isAdmin: boolean,
  currentUserId: number | null,
): string {
  const ownerBucket =
    isAdmin && currentUserId != null
      ? app.created_by_id === currentUserId
        ? "mine"
        : "other"
      : "all";
  return `${ownerBucket}:${app.approval_status}`;
}

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
  // issue_local_032: staged reordering. `apps` is the draft (displayed) order;
  // `savedOrderIds` is the last-known-persisted order, refreshed by every
  // `reload()` (including after a successful Save). Any other action that
  // reloads (create/edit/delete/etc.) intentionally discards an unsaved
  // reorder draft the same way, since the refreshed data may no longer match it.
  const [savedOrderIds, setSavedOrderIds] = useState<number[]>([]);
  const [savingOrder, setSavingOrder] = useState(false);

  const onAppsChanged = props.onAppsChanged;
  const reload = useCallback(async () => {
    const [nextApps, nextUsers] = await Promise.all([
      isAdmin ? api.listManagedApplications() : api.listMyApplications(),
      isAdmin ? api.listUsers() : Promise.resolve([]),
    ]);
    setApps(nextApps);
    setSavedOrderIds(nextApps.map((a) => a.id));
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

  /** issue_local_032: reorder is staged locally only -- no network call here.
   * The move/drag target is constrained to the same bucket (see
   * appBucketKey); reaching outside it is a no-op. */
  const moveApp = useCallback(
    (appId: number, direction: -1 | 1) => {
      setApps((current) => {
        const index = current.findIndex((app) => app.id === appId);
        if (index < 0) return current;
        const bucketOf = (app: Application) =>
          appBucketKey(app, isAdmin, currentUserId);
        const myBucket = bucketOf(current[index]);
        let targetIndex = index + direction;
        while (
          targetIndex >= 0 &&
          targetIndex < current.length &&
          bucketOf(current[targetIndex]) !== myBucket
        ) {
          targetIndex += direction;
        }
        if (targetIndex < 0 || targetIndex >= current.length) return current;
        if (bucketOf(current[targetIndex]) !== myBucket) return current;
        const next = [...current];
        [next[index], next[targetIndex]] = [next[targetIndex], next[index]];
        return next;
      });
    },
    [isAdmin, currentUserId],
  );

  /** Drag-and-drop equivalent of moveApp: move `draggedAppId` to sit at
   * `targetAppId`'s position, only when both are in the same bucket. */
  const reorderWithinBucket = useCallback(
    (draggedAppId: number, targetAppId: number) => {
      if (draggedAppId === targetAppId) return;
      setApps((current) => {
        const sourceIndex = current.findIndex((a) => a.id === draggedAppId);
        const targetIndex = current.findIndex((a) => a.id === targetAppId);
        if (sourceIndex < 0 || targetIndex < 0) return current;
        const bucketOf = (app: Application) =>
          appBucketKey(app, isAdmin, currentUserId);
        if (bucketOf(current[sourceIndex]) !== bucketOf(current[targetIndex])) {
          return current;
        }
        const next = [...current];
        const [moved] = next.splice(sourceIndex, 1);
        const newTargetIndex = next.findIndex((a) => a.id === targetAppId);
        next.splice(newTargetIndex, 0, moved);
        return next;
      });
    },
    [isAdmin, currentUserId],
  );

  const orderDirty = useMemo(
    () =>
      apps.length === savedOrderIds.length &&
      apps.some((app, index) => app.id !== savedOrderIds[index]),
    [apps, savedOrderIds],
  );

  /** Revert the draft order back to the last-saved order (object data is
   * unchanged; only position is restored). */
  const discardOrder = useCallback(() => {
    setApps((current) => {
      const byId = new Map(current.map((a) => [a.id, a]));
      const restored = savedOrderIds
        .map((id) => byId.get(id))
        .filter((a): a is Application => a != null);
      // Safety net: if the id sets diverged (shouldn't happen since a reload
      // always resets both together), fall back to the current draft as-is.
      return restored.length === current.length ? restored : current;
    });
  }, [savedOrderIds]);

  const saveOrder = useCallback(async () => {
    setSavingOrder(true);
    setError(null);
    try {
      const byId = new Map(apps.map((a) => [a.id, a]));
      const currentBuckets = new Map<string, number[]>();
      for (const app of apps) {
        const key = appBucketKey(app, isAdmin, currentUserId);
        const list = currentBuckets.get(key);
        if (list) list.push(app.id);
        else currentBuckets.set(key, [app.id]);
      }
      const savedBuckets = new Map<string, number[]>();
      for (const id of savedOrderIds) {
        const app = byId.get(id);
        if (!app) continue;
        const key = appBucketKey(app, isAdmin, currentUserId);
        const list = savedBuckets.get(key);
        if (list) list.push(id);
        else savedBuckets.set(key, [id]);
      }
      const groups: {
        application_ids: number[];
        expected_application_ids: number[];
      }[] = [];
      for (const [key, ids] of currentBuckets) {
        const expected = savedBuckets.get(key) ?? [];
        if (ids.length !== expected.length) continue;
        const changed = ids.some((id, index) => id !== expected[index]);
        if (changed) {
          groups.push({ application_ids: ids, expected_application_ids: expected });
        }
      }
      if (groups.length === 0) return;
      await api.reorderApplications(groups);
      await reload();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Unable to save the new application order.",
      );
    } finally {
      setSavingOrder(false);
    }
  }, [apps, savedOrderIds, isAdmin, currentUserId, reload]);

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
          aliasOptions={ownerAliasOptions(apps, props.currentUser?.id)}
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
        <div className="manager-toolbar application-manager-actions">
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
          {orderDirty && (
            <span className="reorder-actions">
              <button
                type="button"
                className="btn accent"
                onClick={() => void saveOrder()}
                disabled={savingOrder}
              >
                {savingOrder ? "Saving…" : "Save changes"}
              </button>
              <button
                type="button"
                className="btn ghost"
                onClick={discardOrder}
                disabled={savingOrder}
              >
                Discard changes
              </button>
            </span>
          )}
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
            onDropReorder={(draggedAppId) =>
              reorderWithinBucket(draggedAppId, app.id)
            }
            teamOptions={teamOptions}
            aliasOptions={ownerAliasOptions(apps, app.created_by_id)}
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

/** An existing alias application offered as an embedded app's frame target;
 * the value stored is the alias slug (the alias app's ``url``). */
export type AliasOption = { label: string; value: string };

/** The given owner's existing alias applications, as dropdown options for an
 * embedded app's frame target.
 *
 * An embedded app renders the same-origin alias path (served by the reverse
 * proxy under the portal's own domain) -- the only source reachable by external
 * users and free of mixed-content. So its target must be an existing alias the
 * owner owns. Derived from the already-loaded application list; only the
 * owner's alias-type apps are offered (the backend enforces the same scope).
 */
function ownerAliasOptions(
  apps: readonly Application[],
  ownerId: number | null | undefined,
): AliasOption[] {
  if (!ownerId) return [];
  return apps
    .filter(
      (a) =>
        a.url_type === "alias" &&
        a.created_by_id === ownerId &&
        // Match the backend's find_owner_alias_by_slug contract: only an
        // active, approved alias is a usable (and accepted) frame target.
        a.is_active &&
        a.approval_status === "approved",
    )
    .map((a) => ({ label: a.name, value: a.url }));
}

/** Dropdown to pick an existing alias for an embedded app to frame. When the
 * owner has no aliases, a notice explains that an alias must be created first;
 * there is no free-text entry (an embedded app can only frame a real alias). */
function EmbeddedAliasField(props: {
  alias: string;
  aliases: readonly AliasOption[];
  onAliasChange: (value: string) => void;
}) {
  if (props.aliases.length === 0) {
    return (
      <div className="field">
        <span>Embedded target (alias)</span>
        <span className="alert warn" role="alert">
          You have no aliases yet. An embedded app can only frame an existing
          alias. Create the alias application first, then add the embedded app.
        </span>
      </div>
    );
  }
  return (
    <div className="field">
      <span>Embedded target (alias)</span>
      <select
        value={props.alias}
        onChange={(e) => props.onAliasChange(e.target.value)}
        aria-label="Embedded target alias"
        required
      >
        <option value="" disabled>
          Select an alias…
        </option>
        {props.aliases.map((a) => (
          <option key={a.value} value={a.value}>
            {a.label}
          </option>
        ))}
      </select>
      <span className="muted logo-hint">
        The embedded app frames one of your existing aliases inside the portal.
        Its content is served through the alias's reverse proxy, so it is
        reachable by external users. To frame something that has no alias yet,
        create the alias application first.
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
        // Embedded target is selected from an alias dropdown rendered by the
        // form (EmbeddedAliasField); nothing to show inline here.
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

/** Opt-in "rewrite root paths" toggle for aliases whose upstream assumes it
 * runs at '/' (emits root-absolute links). When on, nginx strips the /alias
 * prefix and rewrites the upstream's root-absolute responses back under
 * /alias/. Does not fix links an app builds at runtime in JavaScript. */
function RewriteRootField(props: {
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="field">
      <Toggle
        checked={props.checked}
        onChange={props.onChange}
        label="Rewrite root paths (for apps that assume they run at /)"
      />
      <span className="muted logo-hint">
        Enable for apps that generate root-absolute links (e.g. <code>/assets/…</code>)
        and would otherwise show a blank page under an alias sub-path. The proxy
        rewrites server-emitted links and redirects back under the alias. Note:
        it cannot fix paths an app builds at runtime in JavaScript, so
        single-page apps may still not work fully.
      </span>
    </div>
  );
}

/** Opt-in "pass authenticated user" toggle for aliases whose upstream can use
 * the signed-in AppManager identity (e.g. to apply per-user roles). When on,
 * nginx forwards the fixed `X-AppManager-User` header, sourced only from the
 * trusted auth check -- never from the client -- containing the signed-in
 * account's username/email. Requires "Require AppManager authentication" to
 * be enabled, since there is otherwise no authenticated identity to send. */
function PassAuthenticatedUserField(props: {
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
        label="Pass authenticated user header to app"
      />
      <span className="muted logo-hint">
        Sends the signed-in user's account username/email to the app in a fixed{" "}
        <code>X-AppManager-User</code> header, so it can apply per-user roles.
        The value always comes from the current AppManager session, never from
        the request itself. Only trust this header if the app is reachable
        exclusively through this reverse proxy. Requires "Require AppManager
        authentication".
      </span>
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
  /** The creator's existing aliases, offered as embedded-app frame targets. */
  aliasOptions: readonly AliasOption[];
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
  const [appsRewriteRoot, setAppsRewriteRoot] = useState(false);
  const [passAuthenticatedUser, setPassAuthenticatedUser] = useState(false);
  const [isPrivate, setIsPrivate] = useState(false);
  const [sharedUsers, setSharedUsers] = useState<ApplicationShareUser[]>([]);
  const [busy, setBusy] = useState(false);
  const [appsServerOptions, setAppsServerOptions] = useState<AppsServerOption[]>([]);
  // Embedded apps frame an existing alias; the stored url is that alias's slug.
  const [embeddedAlias, setEmbeddedAlias] = useState("");

  useEffect(() => {
    let active = true;
    if (!props.currentUserId) {
      setAppsServerOptions([]);
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
    return () => {
      active = false;
    };
  }, [props.currentUserId]);

  // Each alias application has its own upstream target. The host is prefilled
  // from the signed-in user's configured apps server when available.
  const showPort = urlType === "alias";
  const showServer = urlType === "alias";
  const isEmbedded = urlType === "embedded";
  // Private/user-restricted aliases always require authentication regardless
  // of the toggle; the authenticated-user header can only ever be sent when
  // authentication is actually in effect.
  const effectiveAliasAuthRequired =
    isPrivate || sharedUsers.length > 0 ? true : aliasAuthRequired;

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
      // Embedded apps frame an existing alias: their `url` is the selected alias
      // slug (no apps_* fields). Other types send the url field as typed.
      const resolvedUrl = isEmbedded ? embeddedAlias : url.trim();
      if (isEmbedded && !resolvedUrl) {
        props.onError("Select an existing alias for the embedded app to frame.");
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
        ...(urlType === "alias" ? { apps_rewrite_root: appsRewriteRoot } : {}),
        ...(urlType === "alias"
          ? { pass_authenticated_user: effectiveAliasAuthRequired && passAuthenticatedUser }
          : {}),
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
          <EmbeddedAliasField
            alias={embeddedAlias}
            aliases={props.aliasOptions}
            onAliasChange={setEmbeddedAlias}
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
            onChange={(checked) => {
              setAliasAuthRequired(checked);
              if (!checked) setPassAuthenticatedUser(false);
            }}
            disabled={isPrivate || sharedUsers.length > 0}
          />
        )}

        {showPort && (
          <RewriteRootField
            checked={appsRewriteRoot}
            onChange={setAppsRewriteRoot}
          />
        )}

        {showPort && (
          <PassAuthenticatedUserField
            checked={passAuthenticatedUser && effectiveAliasAuthRequired}
            onChange={setPassAuthenticatedUser}
            disabled={!effectiveAliasAuthRequired}
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
              // Embedded apps require a selected existing alias to frame;
              // other types require the url field.
              (isEmbedded
                ? embeddedAlias.length === 0
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
  /** issue_local_032: drag-and-drop equivalent of the move buttons -- called
   * on the row a dragged card is dropped onto, with the dragged card's id. */
  onDropReorder?: (draggedAppId: number) => void;
  teamOptions: readonly string[];
  /** The (resulting) owner's existing aliases, offered as the embedded frame
   * target in the edit form. */
  aliasOptions: readonly AliasOption[];
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
  const [appsRewriteRoot, setAppsRewriteRoot] = useState(
    !!app.apps_rewrite_root,
  );
  const [passAuthenticatedUser, setPassAuthenticatedUser] = useState(
    !!app.pass_authenticated_user,
  );
  const [isPrivate, setIsPrivate] = useState(!!app.is_private);
  const [sharedUsers, setSharedUsers] = useState<ApplicationShareUser[]>(app.shared_users ?? []);
  const [ownerId, setOwnerId] = useState(String(app.created_by_id ?? ""));
  const [logoError, setLogoError] = useState<string | null>(null);
  const [appsServerOptions, setAppsServerOptions] = useState<AppsServerOption[]>([]);
  // Embedded apps frame an existing alias; prefill with the stored alias slug.
  const [embeddedAlias, setEmbeddedAlias] = useState(
    app.url_type === "embedded" ? app.url : "",
  );

  // issue_021: the apps-server dropdown reflects the *current* owner's
  // servers -- for a non-admin this is always self (ownerId never changes,
  // there's no owner picker); for an admin it reloads whenever they pick a
  // different owner in the Owner select below.
  useEffect(() => {
    let active = true;
    const idNum = Number(ownerId);
    if (!ownerId || !Number.isFinite(idNum) || idNum <= 0) {
      setAppsServerOptions([]);
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
    setEmbeddedAlias(app.url_type === "embedded" ? app.url : "");
    setAliasAuthRequired(app.alias_auth_required);
    setAppsRewriteRoot(!!app.apps_rewrite_root);
    setPassAuthenticatedUser(!!app.pass_authenticated_user);
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
    app.apps_rewrite_root,
    app.pass_authenticated_user,
    app.is_private,
    app.shared_users,
    app.created_by_id,
    props.defaultAppsServer,
  ]);

  const dirty = useMemo(
    () =>
      name !== app.name ||
      urlType !== app.url_type ||
      // Embedded apps store the selected alias slug in `url`.
      (urlType === "embedded"
        ? embeddedAlias !== (app.url_type === "embedded" ? app.url : "")
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
      appsRewriteRoot !== !!app.apps_rewrite_root ||
      passAuthenticatedUser !== !!app.pass_authenticated_user ||
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
      embeddedAlias,
      description,
      iconUrl,
      appsProtocol,
      appsPort,
      appsServer,
      appsServerOptions,
      appsPath,
      aliasAuthRequired,
      appsRewriteRoot,
      passAuthenticatedUser,
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
  const effectiveAliasAuthRequired =
    isPrivate || sharedUsers.length > 0 ? true : aliasAuthRequired;

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
          setPassAuthenticatedUser(config.pass_authenticated_user);
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
    <article
      className={`user-card${app.is_active ? "" : " inactive"}${editing ? " editing" : ""}`}
      draggable={props.showReorder !== false && !editing}
      onDragStart={(e) => {
        e.dataTransfer.setData("text/plain", String(app.id));
        e.dataTransfer.effectAllowed = "move";
      }}
      onDragOver={(e) => {
        if (props.showReorder !== false) e.preventDefault();
      }}
      onDrop={(e) => {
        e.preventDefault();
        const draggedId = Number(e.dataTransfer.getData("text/plain"));
        if (draggedId && draggedId !== app.id) {
          props.onDropReorder?.(draggedId);
        }
      }}
    >
      <div className="user-card-head app-card-head">
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
          {app.pending_pass_authenticated_user !== null &&
            app.pending_pass_authenticated_user !== undefined && (
              <span className="status-badge warn push-needed">
                {app.pending_pass_authenticated_user
                  ? "user header enable requested"
                  : "user header disable requested"}
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
          {app.url_type === "embedded" &&
            !props.aliasOptions.some((a) => a.value === app.url) && (
              <span
                className="status-badge rejected"
                title={
                  `This embedded app frames the alias "${app.url}", which no ` +
                  `longer exists. Re-point it at an existing alias, recreate ` +
                  `that alias, or remove this embedded app.`
                }
              >
                missing alias — needs attention
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
        </div>
        <div className="row-actions">
          {props.showReorder !== false && (
            <>
              <span
                className="team-drag-handle"
                aria-hidden="true"
                title="Drag to reorder"
              >
                ⠿
              </span>
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
              app.pending_alias_auth_required !== undefined) ||
            (app.pending_apps_rewrite_root !== null &&
              app.pending_apps_rewrite_root !== undefined) ||
            (app.pending_pass_authenticated_user !== null &&
              app.pending_pass_authenticated_user !== undefined)) && (
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
            <EmbeddedAliasField
              alias={embeddedAlias}
              aliases={props.aliasOptions}
              onAliasChange={setEmbeddedAlias}
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
              onChange={(checked) => {
                setAliasAuthRequired(checked);
                if (!checked) setPassAuthenticatedUser(false);
              }}
              disabled={isPrivate || sharedUsers.length > 0}
            />
          )}

          {showPort && (
            <RewriteRootField
              checked={appsRewriteRoot}
              onChange={setAppsRewriteRoot}
            />
          )}

          {showPort && (
            <PassAuthenticatedUserField
              checked={passAuthenticatedUser && effectiveAliasAuthRequired}
              onChange={setPassAuthenticatedUser}
              disabled={!effectiveAliasAuthRequired}
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
                // Embedded apps store the selected alias slug; other types
                // send url as typed.
                const resolvedUrl = isEmbedded ? embeddedAlias : url;
                if (isEmbedded && !resolvedUrl) {
                  setLogoError(
                    "Select an existing alias for the embedded app to frame.",
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
                  ...(urlType === "alias" ? { apps_rewrite_root: appsRewriteRoot } : {}),
                  ...(urlType === "alias"
                    ? { pass_authenticated_user: effectiveAliasAuthRequired && passAuthenticatedUser }
                    : {}),
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
