import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import type {
  Application,
  ApprovalStatus,
  UpdateApplicationInput,
  UrlType,
} from "../types";
import { resolveAppHref, resolveIconSrc } from "../lib/links";
import { fileToLogoDataUrl } from "../lib/image";
import { defaultLogoFor } from "../logos";
import { CheckIcon, PlusIcon, XIcon } from "./icons";

/**
 * Application Manager. Every signed-in user manages the applications they have
 * submitted (in any approval state); administrators manage every application and
 * can approve or reject submissions. Team options are limited to the caller's
 * own teams unless they are an administrator.
 */
export function ApplicationManager(props: {
  isAdmin: boolean;
  teamOptions: readonly string[];
}) {
  const { isAdmin, teamOptions } = props;
  const [apps, setApps] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  // Transient, view-local reverse-proxy push results, keyed by application id.
  // Populated from an action's returned push status and shown once next to the
  // application name. Cleared on reload (and therefore on leaving this view).
  const [pushNotices, setPushNotices] = useState<Record<number, string>>({});

  const reload = useCallback(async () => {
    setApps(
      isAdmin
        ? await api.listManagedApplications()
        : await api.listMyApplications(),
    );
  }, [isAdmin]);

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

  // Run an action that returns the affected application, then surface its push
  // status as a transient notice next to that application's name.
  const runPushAction = useCallback(
    async (action: () => Promise<Application>) => {
      setError(null);
      try {
        const result = await action();
        if (result && result.last_push_status) {
          setPushNotices((current) => ({
            ...current,
            [result.id]: result.last_push_status as string,
          }));
        }
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

      <section className="card">
        <h2>{isAdmin ? "All applications" : "Your applications"}</h2>
        {apps.length === 0 ? (
          <p className="muted">
            {isAdmin
              ? "No applications yet. Create one above."
              : "You have not submitted any applications yet."}
          </p>
        ) : (
          <div className="user-list">
            {apps.map((app) => (
              <ApplicationRow
                key={app.id}
                app={app}
                isAdmin={isAdmin}
                teamOptions={teamOptions}
                onSave={(input) =>
                  runAction(async () => {
                    await api.updateApplication(app.id, input);
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
                  runPushAction(async () => {
                    return await api.updateApplication(app.id, {
                      approval_status: status,
                    });
                  })
                }
                onRetryPush={() =>
                  runPushAction(async () => {
                    return await api.retryApplicationPush(app.id);
                  })
                }
                pushNotice={pushNotices[app.id]}
                onDelete={() =>
                  runAction(async () => {
                    await api.deleteApplication(app.id);
                  })
                }
              />
            ))}
          </div>
        )}
      </section>
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
}) {
  const allSelected =
    props.options.length > 0 && props.selected.length === props.options.length;
  return (
    <fieldset className="team-picker">
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
            checked={!isAlias}
            onChange={() => props.onUrlTypeChange("url")}
          />
          <span>Full URL</span>
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
              // validates it (letters, digits, and dashes only).
              onChange={(e) =>
                props.onUrlChange(
                  e.target.value.replace(/^\/+/, "").replace(/[^A-Za-z0-9-]/g, ""),
                )
              }
              placeholder="my-dashboard"
              aria-label="Local alias relative path"
              pattern="[A-Za-z0-9-]{1,30}"
              maxLength={30}
              required
            />
          </span>
          <span className="muted logo-hint">
            Letters, digits, and dashes only; maximum 30 characters.
          </span>
        </label>
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
  const [appsPort, setAppsPort] = useState("");
  const [appsServer, setAppsServer] = useState("");
  const [busy, setBusy] = useState(false);

  // Each alias application has its own port, settable by any user. The upstream
  // server host comes from the owning user's configured apps host; an admin
  // (who has no per-user apps host) can set the host on the application itself.
  const showPort = urlType === "alias";
  const showServer = props.isAdmin && urlType === "alias";

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
      const created = await api.createApplication({
        name: appName,
        url: url.trim(),
        url_type: urlType,
        description: description.trim(),
        icon_url: icon,
        teams,
        ...(showPort ? { apps_port: appsPort.trim() } : {}),
        ...(showServer ? { apps_server: appsServer.trim() } : {}),
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
          onUrlTypeChange={setUrlType}
          onUrlChange={setUrl}
        />

        {showPort && (
          <label className="field">
            <span>Application port</span>
            <input
              type="text"
              value={appsPort}
              onChange={(e) => setAppsPort(e.target.value)}
              placeholder="8080"
              inputMode="numeric"
              aria-label="Application port"
            />
            <span className="muted logo-hint">
              The port your application listens on.{" "}
              {props.isAdmin
                ? "Set the server host below."
                : "The server host comes from your account's apps server (set by an administrator)."}
            </span>
          </label>
        )}

        {showServer && (
          <label className="field">
            <span>Apps server (host/IP)</span>
            <input
              type="text"
              value={appsServer}
              onChange={(e) => setAppsServer(e.target.value)}
              placeholder="apps.example.com"
              aria-label="Apps server host or IP"
            />
            <span className="muted logo-hint">
              The host where this application runs (used as the alias upstream).
              Admins set this per application since they have no per-user apps
              server.
            </span>
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
          onChange={setIconUrl}
          onError={props.onError}
        />

        <TeamCheckboxes
          options={props.teamOptions}
          selected={teams}
          onToggle={toggleTeam}
          onSetAll={setTeams}
        />

        <div className="row-actions">
          <button
            type="submit"
            className="btn primary"
            disabled={
              busy || name.trim().length === 0 || url.trim().length === 0
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
  teamOptions: readonly string[];
  onSave: (input: UpdateApplicationInput) => void;
  onToggleActive: () => void;
  onSetApproval: (status: ApprovalStatus) => void;
  onRetryPush: () => void;
  onDelete: () => void;
  /** Transient reverse-proxy push status shown once next to the name. */
  pushNotice?: string;
}) {
  const { app, isAdmin } = props;
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [showPushLog, setShowPushLog] = useState(false);
  const [name, setName] = useState(app.name);
  const [urlType, setUrlType] = useState<UrlType>(app.url_type);
  const [url, setUrl] = useState(app.url);
  const [description, setDescription] = useState(app.description);
  const [iconUrl, setIconUrl] = useState(app.icon_url);
  const [teams, setTeams] = useState<string[]>(app.teams);
  const [appsPort, setAppsPort] = useState(app.apps_port ?? "");
  const [appsServer, setAppsServer] = useState(app.apps_server ?? "");
  const [logoError, setLogoError] = useState<string | null>(null);

  useEffect(() => {
    setName(app.name);
    setUrlType(app.url_type);
    setUrl(app.url);
    setDescription(app.description);
    setIconUrl(app.icon_url);
    setTeams(app.teams);
    setAppsPort(app.apps_port ?? "");
    setAppsServer(app.apps_server ?? "");
  }, [
    app.name,
    app.url_type,
    app.url,
    app.description,
    app.icon_url,
    app.teams,
    app.apps_port,
    app.apps_server,
  ]);

  const dirty = useMemo(
    () =>
      name !== app.name ||
      urlType !== app.url_type ||
      url !== app.url ||
      description !== app.description ||
      iconUrl !== app.icon_url ||
      appsPort !== (app.apps_port ?? "") ||
      appsServer !== (app.apps_server ?? "") ||
      teams.length !== app.teams.length ||
      teams.some((t) => !app.teams.includes(t)),
    [name, urlType, url, description, iconUrl, appsPort, appsServer, teams, app],
  );

  // Each alias application has its own port, editable by any user. The upstream
  // server host comes from the owning user's configured apps host; an admin can
  // set the host on the application itself.
  const showPort = urlType === "alias";
  const showServer = isAdmin && urlType === "alias";

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
        </div>
      </div>

      {isAdmin && (
        <div className="row-actions approval-actions">
          {(app.approval_status !== "approved" ||
            app.pending_alias ||
            app.pending_is_active !== null && app.pending_is_active !== undefined) && (
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
          {app.teams.length > 0 && (
            <div className="tag-row">
              {app.teams.map((team) => (
                <span key={team} className="tag">
                  {team}
                </span>
              ))}
            </div>
          )}
          {app.created_by && (
            <div className="tag-row publisher-row">
              <span className="tag publisher-tag">
                published by {publisherLabel(app.created_by)}
              </span>
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
                    className="btn approve"
                    onClick={props.onRetryPush}
                  >
                    Push
                  </button>
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
            onUrlTypeChange={setUrlType}
            onUrlChange={setUrl}
          />

          {showPort && (
            <label className="field">
              <span>Application port</span>
              <input
                type="text"
                value={appsPort}
                onChange={(e) => setAppsPort(e.target.value)}
                placeholder="8080"
                inputMode="numeric"
                aria-label="Application port"
              />
            </label>
          )}

          {showServer && (
            <label className="field">
              <span>Apps server (host/IP)</span>
              <input
                type="text"
                value={appsServer}
                onChange={(e) => setAppsServer(e.target.value)}
                placeholder="apps.example.com"
                aria-label="Apps server host or IP"
              />
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

          <TeamCheckboxes
            options={props.teamOptions}
            selected={teams}
            onToggle={toggleTeam}
            onSetAll={setTeams}
          />

          <div className="row-actions">
            <button
              type="button"
              className="btn primary"
              disabled={!dirty}
              onClick={() => {
                props.onSave({
                  name,
                  url,
                  url_type: urlType,
                  description,
                  icon_url: iconUrl,
                  teams,
                  ...(showPort ? { apps_port: appsPort.trim() } : {}),
                  ...(showServer ? { apps_server: appsServer.trim() } : {}),
                });
                setEditing(false);
              }}
            >
              Save changes
            </button>
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
