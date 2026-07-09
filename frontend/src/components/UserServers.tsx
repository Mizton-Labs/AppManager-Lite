import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { ServerTemplateOption, ServerUsage, UserServer } from "../types";

/**
 * Per-user server list + Add Server form (issue_015 phase 3).
 *
 * Reused by User Management (admins managing any user) and the Account page
 * (self-service users managing their own servers). Servers are shown as
 * small cards ("NAME - IP"); LXC servers get their IP automatically, VMs
 * prompt for a manual IP after being configured in Proxmox.
 */
export function UserServersPanel(props: {
  userId: number;
  /** Whether the caller may create servers for this user. */
  canCreate: boolean;
  /** Whether the caller may remove server records. */
  canDelete: boolean;
  /**
   * Whether the ACTOR using this panel is an administrator. Admin-initiated
   * creates are exempt from per-user quotas, so the create form reflects that
   * rather than the target user's standing limits.
   */
  isAdmin?: boolean;
  /** The target user's derived id; forms the static server-name prefix. */
  userDerivedId?: string;
  /** Prefill for the comma-separated OS users receiving the public key. */
  defaultPubkeyUser?: string;
}) {
  const [serversList, setServersList] = useState<UserServer[] | null>(null);
  const [templates, setTemplates] = useState<ServerTemplateOption[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  async function refresh() {
    try {
      setServersList(await api.listUserServers(props.userId));
      setError(null);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to load servers.",
      );
    }
  }

  useEffect(() => {
    setServersList(null);
    void refresh();
    if (props.canCreate) {
      api
        .listAccountServerTemplates()
        .then(setTemplates)
        .catch(() => setTemplates([]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.userId, props.canCreate]);

  return (
    <div className="server-panel">
      <div className="server-panel-head">
        <h3>Servers</h3>
        {props.canCreate && (
          <button
            type="button"
            className="btn ghost"
            onClick={() => {
              setAdding((v) => !v);
              setNotice(null);
            }}
          >
            {adding ? "Close" : "Add Server"}
          </button>
        )}
      </div>
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="alert success" role="status">
          {notice}
        </p>
      )}
      {adding && (
        <AddServerForm
          userId={props.userId}
          templates={templates}
          isAdmin={props.isAdmin ?? false}
          userDerivedId={props.userDerivedId ?? props.defaultPubkeyUser ?? ""}
          defaultPubkeyUser={props.defaultPubkeyUser ?? ""}
          onCreated={async (server) => {
            setAdding(false);
            const withWarnings = /ERROR:|WARNING:/.test(server.last_log);
            setNotice(
              server.status === "created"
                ? `Server '${server.name}' created` +
                    (withWarnings ? " with warnings (see its log)" : " successfully") +
                    (server.kind === "vm"
                      ? "; configure the VM in Proxmox and enter its IP address"
                      : server.ip_address
                        ? ` (IP ${server.ip_address})`
                        : "")
                : null,
            );
            await refresh();
          }}
          onFailed={refresh}
        />
      )}
      {serversList === null ? (
        <p role="status">Loading servers...</p>
      ) : serversList.length === 0 ? (
        <p className="muted">No servers.</p>
      ) : (
        <div className="server-list">
          {serversList.map((server) => (
            <ServerCard
              key={server.id}
              server={server}
              canDelete={props.canDelete}
              isAdmin={props.isAdmin ?? false}
              onChanged={refresh}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** Colour band for a quota bar based on how much of the limit is committed. */
export function quotaLevel(used: number, limit: number): "ok" | "warn" | "full" {
  if (limit <= 0) return "ok";
  const pct = (used / limit) * 100;
  if (pct > 90) return "full";
  if (pct >= 70) return "warn";
  return "ok";
}

/**
 * Small horizontal "resources left" indicator bars shown at the top of the
 * create-server form (issue_015-r4 F3). Reflects the user's committed usage
 * against their per-user limits; administrators are unrestricted and see a
 * short note instead of bars.
 */
function QuotaBars(props: { userId: number; isAdmin: boolean }) {
  const [usage, setUsage] = useState<ServerUsage | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    // An admin actor is not quota-gated, so the usage figures are not needed.
    if (props.isAdmin) return;
    let active = true;
    setUsage(null);
    setFailed(false);
    api
      .getUserServerUsage(props.userId)
      .then((u) => {
        if (active) setUsage(u);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [props.userId, props.isAdmin]);

  // An admin acting here is exempt from per-user quotas (admin-created servers
  // are quota-exempt), so the target user's standing limits do not gate this
  // create. Say so instead of showing bars that could read "full" yet still
  // allow the create.
  if (props.isAdmin) {
    return (
      <p className="quota-unlimited muted">
        Creating as administrator: per-user resource limits are not enforced.
      </p>
    );
  }

  // Fail quietly: the form must remain usable if usage can't be loaded.
  if (failed) return null;
  if (!usage) {
    return (
      <p className="quota-bars-loading muted" role="status">
        Loading resource usage...
      </p>
    );
  }
  if (usage.unlimited) {
    return (
      <p className="quota-unlimited muted">No resource limits (administrator).</p>
    );
  }

  const rows: { label: string; used: number; limit: number; unit?: string }[] = [
    { label: "Servers", used: usage.servers.used, limit: usage.servers.limit },
    { label: "CPUs", used: usage.cpus.used, limit: usage.cpus.limit },
    {
      label: "Memory",
      used: usage.memory_gb.used,
      limit: usage.memory_gb.limit,
      unit: "GB",
    },
    {
      label: "Disk",
      used: usage.disk_gb.used,
      limit: usage.disk_gb.limit,
      unit: "GB",
    },
  ];

  return (
    <div className="quota-bars" aria-label="Resource usage">
      {rows.map((r) => {
        const level = quotaLevel(r.used, r.limit);
        const pct =
          r.limit > 0 ? Math.min(100, Math.round((r.used / r.limit) * 100)) : 0;
        const unit = r.unit ? ` ${r.unit}` : "";
        return (
          <div className="quota-bar" key={r.label}>
            <span className="quota-bar-label">{r.label}</span>
            <span
              className="quota-bar-track"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={r.limit}
              aria-valuenow={r.used}
              aria-label={`${r.label}: ${r.used} of ${r.limit}${unit} used`}
            >
              <span
                className={`quota-bar-fill quota-${level}`}
                style={{ width: `${pct}%` }}
              />
            </span>
            <span className="quota-bar-value">
              {r.used}/{r.limit}
              {unit}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function AddServerForm(props: {
  userId: number;
  templates: ServerTemplateOption[];
  isAdmin: boolean;
  /** The target user's derived id, used to build the static server-name prefix. */
  userDerivedId: string;
  defaultPubkeyUser: string;
  onCreated: (server: UserServer) => void | Promise<void>;
  onFailed?: () => void | Promise<void>;
}) {
  const [templateId, setTemplateId] = useState(
    props.templates[0] ? String(props.templates[0].id) : "",
  );
  const [name, setName] = useState("");
  const [installPubkey, setInstallPubkey] = useState(true);
  const [pubkeyUsers, setPubkeyUsers] = useState(props.defaultPubkeyUser);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [failLog, setFailLog] = useState<string | null>(null);
  const [showFailLog, setShowFailLog] = useState(false);

  useEffect(() => {
    if (!templateId && props.templates[0]) {
      setTemplateId(String(props.templates[0].id));
    }
  }, [props.templates, templateId]);

  const selectedTemplate = props.templates.find(
    (t) => String(t.id) === templateId,
  );
  const selectedKind = selectedTemplate?.kind ?? "lxc";
  // Server names always carry a static prefix "<template-slug>-<owner-id>-";
  // the user only chooses the suffix. The backend composes and validates the
  // full name (max 63), slugifying the template portion, so mirror that here to
  // show the final name and the characters remaining.
  const MAX_NAME = 63;
  const templateSlug = (selectedTemplate?.name ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  const namePrefix =
    selectedTemplate && props.userDerivedId
      ? `${templateSlug || "server"}-${props.userDerivedId}-`
      : "";
  const suffixMax = Math.max(0, MAX_NAME - namePrefix.length);
  const suffixLeft = Math.max(0, suffixMax - name.trim().length);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setFailLog(null);
    try {
      const wantsKey = installPubkey && selectedKind === "lxc";
      const server = await api.createUserServer(props.userId, {
        template_id: Number(templateId),
        name: name.trim(),
        install_pubkey: wantsKey,
        pubkey_users: wantsKey ? pubkeyUsers.trim() : "",
      });
      if (server.status === "failed") {
        setError("Server creation failed.");
        setFailLog(server.last_log);
        await props.onFailed?.();
      } else {
        setName("");
        await props.onCreated(server);
      }
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Unable to create the server.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (props.templates.length === 0) {
    return (
      <p className="muted">
        No server templates are available. An administrator must register
        templates under Settings → Server Provisioning first.
      </p>
    );
  }

  return (
    <form className="create-form" onSubmit={onSubmit}>
      <QuotaBars userId={props.userId} isAdmin={props.isAdmin} />
      {error && (
        <p className="alert error" role="alert">
          {error}{" "}
          {failLog && (
            <button
              type="button"
              className="btn ghost"
              aria-expanded={showFailLog}
              onClick={() => setShowFailLog((v) => !v)}
            >
              {showFailLog ? "Hide details" : "View details"}
            </button>
          )}
        </p>
      )}
      {failLog && showFailLog && <pre className="push-log">{failLog}</pre>}
      <label className="field">
        <span>Template</span>
        <select
          value={templateId}
          onChange={(e) => setTemplateId(e.target.value)}
        >
          {props.templates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name} ({t.kind.toUpperCase()})
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>Server name suffix</span>
        <div className="prefixed-input">
          {namePrefix && (
            <span className="name-prefix" aria-hidden="true">
              {namePrefix}
            </span>
          )}
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={suffixMax || undefined}
            disabled={!!namePrefix && suffixMax === 0}
            aria-label="Server name suffix"
            placeholder="e.g. test-1"
            required
          />
        </div>
        <span className="field-hint muted">
          {namePrefix ? (
            <>
              Full name: <code>{namePrefix}{name.trim() || "…"}</code>
              {" · "}
              {suffixMax === 0
                ? "the template name is too long; ask an administrator to shorten it"
                : `${suffixLeft} of ${suffixMax} characters left`}
            </>
          ) : (
            "Select a template to see the full server name."
          )}
        </span>
      </label>
      {selectedKind === "vm" ? (
        <p className="muted">
          SSH key installation is not available for VM templates: configure
          the VM in Proxmox first, then enter its IP address on the server
          card.
        </p>
      ) : (
        <>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={installPubkey}
              onChange={(e) => setInstallPubkey(e.target.checked)}
            />
            <span>Install the user's SSH public key on the new server</span>
          </label>
          {installPubkey && (
            <label className="field">
              <span>OS users receiving the key (comma-separated)</span>
              <input
                value={pubkeyUsers}
                onChange={(e) => setPubkeyUsers(e.target.value)}
                placeholder="user1, user2"
              />
            </label>
          )}
        </>
      )}
      <div className="row-actions">
        <button
          type="submit"
          className="btn primary"
          disabled={busy || !name.trim() || !templateId}
        >
          {busy ? "Creating server..." : "Create Server"}
        </button>
      </div>
    </form>
  );
}

function ServerCard(props: {
  server: UserServer;
  canDelete: boolean;
  isAdmin: boolean;
  onChanged: () => void | Promise<void>;
}) {
  const { server } = props;
  const [showLog, setShowLog] = useState(false);
  const [ipDraft, setIpDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [confirmingForce, setConfirmingForce] = useState(false);

  const needsIp = server.kind === "vm" && !server.ip_address &&
    server.status !== "failed";

  async function saveIp() {
    setBusy(true);
    setError(null);
    try {
      await api.updateUserServer(server.user_id, server.id, {
        ip_address: ipDraft.trim(),
      });
      await props.onChanged();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Unable to save the IP.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function scheduleDeletion() {
    setBusy(true);
    setError(null);
    try {
      await api.deleteUserServer(server.user_id, server.id);
      setConfirmingDelete(false);
      await props.onChanged();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Unable to schedule deletion.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function cancelDeletion() {
    setBusy(true);
    setError(null);
    try {
      await api.cancelServerDeletion(server.user_id, server.id);
      await props.onChanged();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Unable to cancel deletion.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function forceRemove() {
    setBusy(true);
    setError(null);
    try {
      await api.forceRemoveServer(server.user_id, server.id);
      setConfirmingForce(false);
      await props.onChanged();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Unable to force-remove.",
      );
    } finally {
      setBusy(false);
    }
  }

  const cardClass = server.deletion_failed
    ? "server-card deletion-failed"
    : server.deletion_pending
      ? "server-card deletion-pending"
      : server.status === "failed"
        ? "server-card failed"
        : "server-card";

  return (
    <article className={cardClass}>
      <div className="server-card-head">
        <span className="server-name">
          {server.name}
          {server.ip_address ? ` - ${server.ip_address}` : ""}
        </span>
        <span className="role-badge">{server.kind.toUpperCase()}</span>
        {server.deletion_failed ? (
          <span className="status-badge rejected">deletion failed</span>
        ) : server.deletion_pending ? (
          <span className="status-badge warn">deletion pending</span>
        ) : server.status === "failed" ? (
          <span className="status-badge warn">failed</span>
        ) : server.status === "reference" ? (
          <span className="status-badge ok">reference</span>
        ) : null}
      </div>
      <p className="muted server-meta">
        {server.template_name && <>Template: {server.template_name} · </>}
        {server.cpus > 0 && (
          <>
            {server.cpus} CPU · {server.memory_gb} GB RAM · {server.disk_gb} GB
            disk
          </>
        )}
      </p>
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}

      {server.deletion_failed && props.isAdmin && (
        <p className="alert error" role="alert">
          Automatic destruction of this server failed. Check the guest directly
          in Proxmox, then force-remove this record.
          {server.deletion_error && (
            <>
              {" "}
              <span className="muted">({server.deletion_error})</span>
            </>
          )}
        </p>
      )}

      {server.deletion_pending && !server.deletion_failed && (
        <p className="alert warn" role="status">
          Scheduled for deletion — {deletionCountdown(server.deletion_requested_at)}.
          This is permanent; cancel below to keep the server.
        </p>
      )}

      {needsIp && (
        <div className="row-actions">
          <input
            aria-label="Server IP address"
            placeholder="Enter the VM's IP address"
            value={ipDraft}
            onChange={(e) => setIpDraft(e.target.value)}
            maxLength={15}
          />
          <button
            type="button"
            className="btn primary"
            onClick={saveIp}
            disabled={busy || !ipDraft.trim()}
          >
            Save IP
          </button>
        </div>
      )}

      <div className="row-actions">
        {server.last_log && (
          <button
            type="button"
            className="btn ghost"
            aria-expanded={showLog}
            onClick={() => setShowLog((v) => !v)}
          >
            {showLog ? "Hide log" : "View log"}
          </button>
        )}

        {/* Pending (not failed): allow cancelling the scheduled deletion. */}
        {server.deletion_pending && !server.deletion_failed && (
          <button
            type="button"
            className="btn"
            onClick={cancelDeletion}
            disabled={busy}
          >
            Cancel deletion
          </button>
        )}

        {/* Admin recovery for a failed destroy: force-remove the record. */}
        {server.deletion_failed && props.isAdmin && (
          confirmingForce ? (
            <>
              <button
                type="button"
                className="btn danger"
                onClick={forceRemove}
                disabled={busy}
              >
                Confirm force remove
              </button>
              <button
                type="button"
                className="btn ghost"
                onClick={() => setConfirmingForce(false)}
                disabled={busy}
              >
                Keep
              </button>
            </>
          ) : (
            <button
              type="button"
              className="btn danger"
              onClick={() => setConfirmingForce(true)}
            >
              Force remove
            </button>
          )
        )}

        {/* Normal delete entry point: schedule a deferred deletion. Hidden once
            pending/failed (those have their own actions above). */}
        {props.canDelete &&
          !server.deletion_pending &&
          !server.deletion_failed &&
          (confirmingDelete ? (
            <>
              <button
                type="button"
                className="btn danger"
                onClick={scheduleDeletion}
                disabled={busy}
              >
                Yes, schedule deletion
              </button>
              <button
                type="button"
                className="btn ghost"
                onClick={() => setConfirmingDelete(false)}
                disabled={busy}
              >
                Cancel
              </button>
            </>
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

      {confirmingDelete && !server.deletion_pending && (
        <p className="alert warn" role="alert">
          This permanently deletes the server. It enters a 24-hour grace period,
          then the LXC/VM is destroyed and cannot be recovered. You can cancel
          during the grace period.
        </p>
      )}

      {showLog && <pre className="push-log">{server.last_log}</pre>}
    </article>
  );
}

/** Human "time left" until a pending deletion is destroyed (24h from request). */
function deletionCountdown(requestedAt: string): string {
  const requested = Date.parse(requestedAt.replace(" ", "T") + "Z");
  if (Number.isNaN(requested)) return "within 24 hours";
  const destroyAt = requested + 24 * 60 * 60 * 1000;
  const msLeft = destroyAt - Date.now();
  if (msLeft <= 0) return "destroying shortly";
  const hours = Math.floor(msLeft / (60 * 60 * 1000));
  if (hours >= 1) {
    return `auto-destroys in about ${hours} hour${hours === 1 ? "" : "s"}`;
  }
  const minutes = Math.max(1, Math.floor(msLeft / (60 * 1000)));
  return `auto-destroys in about ${minutes} minute${minutes === 1 ? "" : "s"}`;
}
