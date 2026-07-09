import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type {
  JumpSyncEntry,
  ProviderTemplate,
  ProvisioningSettings,
  ServerTemplate,
  SshKey,
} from "../types";
import { SubTabs } from "./SubTabs";

/**
 * Settings -> Server Provisioning (administrators only).
 *
 * Three cards: the LXC/VM provider connection (Proxmox), the provisioning
 * policy (self-service, per-user server and resource limits), and the server
 * templates registered for creating user servers.
 */
export function ServerProvisioning() {
  const [settings, setSettings] = useState<ProvisioningSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getProvisioningSettings()
      .then(setSettings)
      .catch((err) =>
        setLoadError(
          err instanceof ApiError
            ? err.message
            : "Failed to load provisioning settings.",
        ),
      );
  }, []);

  if (loadError) {
    return (
      <p className="alert error" role="alert">
        {loadError}
      </p>
    );
  }
  if (!settings) {
    return <p role="status">Loading provisioning settings...</p>;
  }
  return (
    <SubTabs
      ariaLabel="Server provisioning sections"
      tabs={[
        {
          id: "provider",
          label: "Provider",
          render: () => (
            <div className="grid">
              <ProviderCard settings={settings} onSaved={setSettings} />
            </div>
          ),
        },
        {
          id: "policy",
          label: "Policy",
          render: () => (
            <div className="grid">
              <PolicyCard settings={settings} onSaved={setSettings} />
            </div>
          ),
        },
        {
          id: "jump",
          label: "Jump Server",
          render: () => (
            <div className="grid">
              <JumpServerCard settings={settings} onSaved={setSettings} />
            </div>
          ),
        },
        {
          id: "templates",
          label: "Server Templates",
          render: () => <ServerTemplatesSection />,
        },
      ]}
    />
  );
}

function JumpServerCard(props: {
  settings: ProvisioningSettings;
  onSaved: (next: ProvisioningSettings) => void;
}) {
  const { settings } = props;
  const [enabled, setEnabled] = useState(settings.jump_enabled);
  const [host, setHost] = useState(settings.jump_host);
  const [user, setUser] = useState(settings.jump_user);
  const [port, setPort] = useState(String(settings.jump_port ?? 22));
  const [keyId, setKeyId] = useState(
    settings.jump_ssh_key_id !== null ? String(settings.jump_ssh_key_id) : "",
  );
  // Bundle-address override: when OFF (the default), the generated SSH config
  // bundle uses the management host/port; when ON, it uses a separate address.
  const [useAdminConfig, setUseAdminConfig] = useState(
    !settings.jump_bundle_override,
  );
  const [bundleHost, setBundleHost] = useState(settings.jump_bundle_host);
  const [bundlePort, setBundlePort] = useState(
    String(settings.jump_bundle_port ?? 22),
  );
  const [sshKeys, setSshKeys] = useState<SshKey[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  // Persistent post-save advisories: a sync reminder when the connection
  // settings changed, and an informational note when only the bundle address
  // changed.
  const [syncReminder, setSyncReminder] = useState(false);
  const [bundleNotice, setBundleNotice] = useState(false);
  const [syncResults, setSyncResults] = useState<JumpSyncEntry[] | null>(null);

  useEffect(() => {
    api
      .listSshKeys()
      .then((keys) => setSshKeys(Array.isArray(keys) ? keys : []))
      .catch(() => undefined);
  }, []);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    // Snapshot the connection-affecting fields before the save so we can tell
    // whether existing users need re-syncing.
    const connectionChanged =
      enabled !== settings.jump_enabled ||
      host.trim() !== settings.jump_host ||
      user.trim() !== settings.jump_user ||
      (Number(port) || 22) !== settings.jump_port ||
      (keyId ? Number(keyId) : null) !== settings.jump_ssh_key_id;
    const bundleOverride = !useAdminConfig;
    const bundleChanged =
      bundleOverride !== settings.jump_bundle_override ||
      bundleHost.trim() !== settings.jump_bundle_host ||
      (Number(bundlePort) || 22) !== settings.jump_bundle_port;
    try {
      const next = await api.updateProvisioningSettings({
        jump_enabled: enabled,
        jump_host: host.trim(),
        jump_user: user.trim(),
        jump_port: Number(port) || 22,
        jump_ssh_key_id: keyId ? Number(keyId) : null,
        jump_bundle_override: bundleOverride,
        // When using the admin config, clear the override address so a stale
        // value can't silently reactivate if the override is turned on later.
        jump_bundle_host: bundleOverride ? bundleHost.trim() : "",
        jump_bundle_port: bundleOverride ? Number(bundlePort) || 22 : 22,
      });
      props.onSaved(next);
      setSaved(true);
      // A connection change is the one that can break existing access.
      setSyncReminder(enabled && connectionChanged);
      // Surface the bundle note only when the bundle address changed without a
      // connection change (a connection change already prompts the stronger
      // sync reminder).
      setBundleNotice(bundleChanged && !(enabled && connectionChanged));
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Unable to save the jump server.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function onSync() {
    setBusy(true);
    setError(null);
    setSyncResults(null);
    try {
      const result = await api.syncJumpServerUsers();
      setSyncResults(result.results);
      // Users are now aligned with the current settings.
      setSyncReminder(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sync failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Jump server</h2>
      <p className="muted">
        When enabled, each new user gets an account on this bastion with their
        SSH public key installed; deleting a user removes their key. Select the
        SSH key used to reach the jump server.
      </p>
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      {saved && (
        <p className="alert success" role="status">
          Jump server settings saved.
        </p>
      )}
      {syncReminder && (
        <p className="alert warn" role="alert">
          Jump server connection settings changed. Existing users must be
          re-synced or their access may stop working — click{" "}
          <strong>Sync users to jump server</strong> below.
        </p>
      )}
      {bundleNotice && (
        <p className="alert info" role="status">
          The SSH config bundle address changed. This only affects the address
          written into users' downloaded SSH config; if it points to the same
          jump server as the admin config, no re-sync is needed.
        </p>
      )}
      <form className="create-form" onSubmit={onSubmit}>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          <span>Enable jump server onboarding</span>
        </label>
        <label className="field">
          <span>Jump host (IP or hostname)</span>
          <input value={host} onChange={(e) => setHost(e.target.value)} />
        </label>
        <label className="field">
          <span>Jump SSH port</span>
          <input
            type="number"
            min={1}
            max={65535}
            value={port}
            onChange={(e) => setPort(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Jump user (account used to manage the bastion)</span>
          <input value={user} onChange={(e) => setUser(e.target.value)} />
        </label>
        <label className="field">
          <span>SSH key</span>
          <select value={keyId} onChange={(e) => setKeyId(e.target.value)}>
            <option value="">None</option>
            {sshKeys.map((k) => (
              <option key={k.id} value={k.id}>
                {k.name} ({k.kind})
              </option>
            ))}
          </select>
        </label>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={useAdminConfig}
            onChange={(e) => setUseAdminConfig(e.target.checked)}
          />
          <span>Use jumpserver admin config in SSH config bundle</span>
        </label>
        {!useAdminConfig && (
          <div className="field-group">
            <p className="hint">
              The generated SSH config bundle will address the jump server at
              this host/port instead of the admin config above. Use this when
              the bastion is managed over a private interface but users connect
              over a different (e.g. public) address.
            </p>
            <label className="field">
              <span>Bundle host (IP or hostname)</span>
              <input
                value={bundleHost}
                onChange={(e) => setBundleHost(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Bundle SSH port</span>
              <input
                type="number"
                min={1}
                max={65535}
                value={bundlePort}
                onChange={(e) => setBundlePort(e.target.value)}
              />
            </label>
          </div>
        )}
        <div className="row-actions">
          <button type="submit" className="btn primary" disabled={busy}>
            {busy ? "Saving..." : "Save jump server"}
          </button>
          <button
            type="button"
            className="btn ghost"
            onClick={onSync}
            disabled={busy || !settings.jump_enabled}
            title={
              settings.jump_enabled
                ? "Onboard all existing users"
                : "Save an enabled jump server first"
            }
          >
            Sync users to jump server
          </button>
        </div>
      </form>
      {syncResults && (
        <div className="rotation-summary">
          <h3>Sync summary</h3>
          <ul>
            {syncResults.map((r) => (
              <li key={r.username}>
                <span
                  className={
                    r.status === "onboarded"
                      ? "status-badge ok"
                      : r.status === "failed"
                        ? "status-badge warn"
                        : "status-badge off"
                  }
                >
                  {r.status}
                </span>{" "}
                {r.username}
                {r.detail ? <span className="muted"> — {r.detail}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function ProviderCard(props: {
  settings: ProvisioningSettings;
  onSaved: (next: ProvisioningSettings) => void;
}) {
  const { settings } = props;
  const [providerType, setProviderType] = useState(
    settings.provider_type || "proxmox",
  );
  const [url, setUrl] = useState(settings.proxmox_url);
  const [tokenName, setTokenName] = useState(settings.proxmox_token_name);
  const [apiKey, setApiKey] = useState("");
  const [filter, setFilter] = useState(settings.proxmox_template_filter);
  const [templatesOnly, setTemplatesOnly] = useState(
    settings.proxmox_templates_only,
  );
  const [verifyTls, setVerifyTls] = useState(settings.proxmox_verify_tls);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [showLog, setShowLog] = useState(false);
  const [templates, setTemplates] = useState<ProviderTemplate[] | null>(null);
  const [templatesError, setTemplatesError] = useState<string | null>(null);
  // Bumped on every successful save so the verification dropdown refetches
  // even when the connection status/filter values did not change.
  const [saveCount, setSaveCount] = useState(0);

  const connStatus = props.settings.proxmox_conn_status;
  const connLog = props.settings.proxmox_conn_log;

  useEffect(() => {
    if (connStatus === "ok") {
      setTemplatesError(null);
      api
        .listProviderTemplates()
        .then((result) => {
          setTemplates(result.templates);
          setTemplatesError(
            result.status === "ok" ? null : "Failed to read templates.",
          );
        })
        .catch((err) =>
          setTemplatesError(
            err instanceof ApiError ? err.message : "Failed to read templates.",
          ),
        );
    } else {
      setTemplates(null);
    }
  }, [connStatus, saveCount]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const next = await api.updateProvisioningSettings({
        provider_type: providerType,
        proxmox_url: url.trim(),
        proxmox_token_name: tokenName.trim(),
        ...(apiKey.trim() ? { proxmox_api_key: apiKey.trim() } : {}),
        proxmox_template_filter: filter.trim(),
        proxmox_templates_only: templatesOnly,
        proxmox_verify_tls: verifyTls,
      });
      props.onSaved(next);
      setApiKey("");
      setSaved(true);
      setSaveCount((n) => n + 1);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Unable to save provider settings.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>LXC/VM provider</h2>
      <p className="muted">
        Connection to the virtualization provider used to create user servers.
        Saving runs a connection test. The API key is stored write-only and
        never shown again.
      </p>
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      {connStatus && (
        <p
          className={connStatus === "ok" ? "alert success" : "alert error"}
          role={connStatus === "ok" ? "status" : "alert"}
        >
          {connStatus === "ok"
            ? saved
              ? "Provider saved; connection test succeeded."
              : "Last connection test succeeded."
            : saved
              ? "Provider saved, but the connection test failed."
              : "The last connection test failed."}{" "}
          <button
            type="button"
            className="btn ghost"
            aria-expanded={showLog}
            onClick={() => setShowLog((v) => !v)}
          >
            {showLog ? "Hide connection log" : "View connection log"}
          </button>
        </p>
      )}
      {showLog && connLog && <pre className="push-log">{connLog}</pre>}
      <form className="create-form" onSubmit={onSubmit}>
        <label className="field">
          <span>Provider type</span>
          <select
            value={providerType}
            onChange={(e) => setProviderType(e.target.value)}
          >
            <option value="proxmox">Proxmox API</option>
          </select>
        </label>
        <label className="field">
          <span>Proxmox URL (PROTO://IP:PORT)</span>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://10.0.0.5:8006"
          />
        </label>
        <label className="field">
          <span>Token name</span>
          <input
            value={tokenName}
            onChange={(e) => setTokenName(e.target.value)}
            placeholder="user@pam!tokenid"
          />
        </label>
        <label className="field">
          <span>API key</span>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={
              props.settings.proxmox_api_key_set
                ? "(unchanged - enter a new key to replace)"
                : "API token secret"
            }
            autoComplete="off"
            maxLength={512}
          />
        </label>
        <label className="field">
          <span>Template name filter</span>
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="e.g. tpl-"
          />
        </label>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={templatesOnly}
            onChange={(e) => setTemplatesOnly(e.target.checked)}
          />
          <span>Only actual Proxmox templates (unchecked: match any VM/LXC by name)</span>
        </label>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={verifyTls}
            onChange={(e) => setVerifyTls(e.target.checked)}
          />
          <span>Verify TLS certificate</span>
        </label>
        {!verifyTls && (
          <p className="alert error" role="alert">
            TLS verification is disabled: the connection is vulnerable to
            interception. Only use this with self-signed lab instances.
          </p>
        )}
        <div className="row-actions">
          <button type="submit" className="btn primary" disabled={busy}>
            {busy ? "Testing connection..." : "Save and test connection"}
          </button>
        </div>
      </form>
      {connStatus === "ok" && (
        <div className="provider-templates">
          <label className="field">
            <span>Templates found (verification)</span>
            {templatesError ? (
              <p className="alert error" role="alert">
                {templatesError}
              </p>
            ) : templates === null ? (
              <p role="status">Loading templates...</p>
            ) : templates.length === 0 ? (
              <p className="muted">No templates match the current filter.</p>
            ) : (
              <select aria-label="Templates found">
                {templates.map((t) => (
                  <option key={`${t.kind}-${t.vmid}`} value={t.vmid}>
                    {t.name} (#{t.vmid}, {t.kind.toUpperCase()}, {t.node})
                  </option>
                ))}
              </select>
            )}
          </label>
        </div>
      )}
    </section>
  );
}

function PolicyCard(props: {
  settings: ProvisioningSettings;
  onSaved: (next: ProvisioningSettings) => void;
}) {
  const { settings } = props;
  const [selfService, setSelfService] = useState(
    settings.provisioning_self_service,
  );
  const [maxServers, setMaxServers] = useState(
    String(settings.provisioning_max_servers),
  );
  const [allowResourceEdit, setAllowResourceEdit] = useState(
    settings.provisioning_allow_resource_edit,
  );
  const [maxCpus, setMaxCpus] = useState(String(settings.provisioning_max_cpus));
  const [maxMemory, setMaxMemory] = useState(
    String(settings.provisioning_max_memory_gb),
  );
  const [maxDisk, setMaxDisk] = useState(
    String(settings.provisioning_max_disk_gb),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const next = await api.updateProvisioningSettings({
        provisioning_self_service: selfService,
        provisioning_max_servers: Number(maxServers),
        provisioning_allow_resource_edit: allowResourceEdit,
        provisioning_max_cpus: Number(maxCpus),
        provisioning_max_memory_gb: Number(maxMemory),
        provisioning_max_disk_gb: Number(maxDisk),
      });
      props.onSaved(next);
      setSaved(true);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Unable to save the policy.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Server provisioning policy</h2>
      <p className="muted">
        Limits applied to user-created servers. Administrator changes to a
        user's server resources do not count against these limits.
      </p>
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      {saved && (
        <p className="alert success" role="status">
          Provisioning policy saved.
        </p>
      )}
      <form className="create-form" onSubmit={onSubmit}>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={selfService}
            onChange={(e) => setSelfService(e.target.checked)}
          />
          <span>
            Enable self-service server provisioning (self-service users and
            administrators; normal users cannot request servers)
          </span>
        </label>
        <label className="field">
          <span>Max servers per user</span>
          <input
            type="number"
            min={0}
            max={100}
            value={maxServers}
            onChange={(e) => setMaxServers(e.target.value)}
          />
        </label>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={allowResourceEdit}
            onChange={(e) => setAllowResourceEdit(e.target.checked)}
          />
          <span>Allow self-service users to modify server resources</span>
        </label>
        <label className="field">
          <span>Max CPUs per user (cores)</span>
          <input
            type="number"
            min={1}
            max={1024}
            value={maxCpus}
            onChange={(e) => setMaxCpus(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Max memory per user (GB)</span>
          <input
            type="number"
            min={1}
            max={4096}
            value={maxMemory}
            onChange={(e) => setMaxMemory(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Max disk per user (GB)</span>
          <input
            type="number"
            min={1}
            max={65536}
            value={maxDisk}
            onChange={(e) => setMaxDisk(e.target.value)}
          />
        </label>
        <div className="row-actions">
          <button type="submit" className="btn primary" disabled={busy}>
            {busy ? "Saving..." : "Save policy"}
          </button>
        </div>
      </form>
    </section>
  );
}

function ServerTemplatesSection() {
  const [templates, setTemplates] = useState<ServerTemplate[]>([]);
  const [sshKeys, setSshKeys] = useState<SshKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setError(null);
    try {
      const [tpls, keys] = await Promise.all([
        api.listServerTemplates(),
        api.listSshKeys(),
      ]);
      setTemplates(tpls);
      setSshKeys(keys);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to load server templates.",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  return (
    <div className="grid">
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      <AddServerTemplateCard
        sshKeys={sshKeys}
        onError={setError}
        onAdded={refresh}
      />
      <ServerTemplatesListCard
        templates={templates}
        sshKeys={sshKeys}
        loading={loading}
        onError={setError}
        onDeleted={refresh}
      />
    </div>
  );
}

function AddServerTemplateCard(props: {
  sshKeys: SshKey[];
  onError: (message: string | null) => void;
  onAdded: () => Promise<void> | void;
}) {
  const { sshKeys } = props;
  const [vmid, setVmid] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"lxc" | "vm">("lxc");
  const [keyId, setKeyId] = useState("");
  const [mainUser, setMainUser] = useState("");
  const [enableSudo, setEnableSudo] = useState(true);
  const [enableTrusted, setEnableTrusted] = useState(true);
  const [busy, setBusy] = useState(false);

  async function onAdd(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    props.onError(null);
    try {
      await api.createServerTemplate({
        vmid: Number(vmid),
        name: name.trim(),
        kind,
        admin_ssh_key_id: keyId ? Number(keyId) : null,
        main_os_user: mainUser.trim(),
        enable_sudo: enableSudo,
        enable_trusted_access: enableTrusted,
      });
      setVmid("");
      setName("");
      setKeyId("");
      setMainUser("");
      setEnableSudo(true);
      setEnableTrusted(true);
      await props.onAdded();
    } catch (err) {
      props.onError(
        err instanceof ApiError ? err.message : "Unable to add the template.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Add server template</h2>
      <p className="muted">
        Preconfigured Proxmox templates (LXC or VM) offered to users when
        creating a server. Templates are assumed to be preconfigured (SSH
        keys, resources, user).
      </p>
      <form className="create-form" onSubmit={onAdd}>
        <label className="field">
          <span>LXC/VM ID</span>
          <input
            type="number"
            min={1}
            value={vmid}
            onChange={(e) => setVmid(e.target.value)}
            required
          />
        </label>
        <label className="field">
          <span>Template name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={60}
            required
          />
        </label>
        <fieldset className="field">
          <legend>Template type</legend>
          <label className="checkbox-field">
            <input
              type="radio"
              name="template-kind"
              checked={kind === "lxc"}
              onChange={() => setKind("lxc")}
            />
            <span>Existing LXC template</span>
          </label>
          <label className="checkbox-field">
            <input
              type="radio"
              name="template-kind"
              checked={kind === "vm"}
              onChange={() => setKind("vm")}
            />
            <span>Existing VM template</span>
          </label>
        </fieldset>
        <label className="field">
          <span>Admin SSH key (optional)</span>
          <select value={keyId} onChange={(e) => setKeyId(e.target.value)}>
            <option value="">None</option>
            {sshKeys.map((k) => (
              <option key={k.id} value={k.id}>
                {k.name} ({k.kind})
              </option>
            ))}
          </select>
          <span className="hint">
            The key used to manage server-template operations: cloning the
            template, installing user keys, and configuring trusted access on
            the resulting servers.
          </span>
          {sshKeys.length === 0 && (
            <span className="hint">
              Register keys under Settings &rarr; Remote Access first.
            </span>
          )}
        </label>
        <label className="field">
          <span>Main user (OS account, optional)</span>
          <input
            value={mainUser}
            onChange={(e) => setMainUser(e.target.value)}
            placeholder="e.g. coder (blank = the user's own ID)"
            maxLength={32}
          />
          <span className="hint">
            When set, the user's SSH key is installed only for this OS account.
          </span>
        </label>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={enableSudo}
            onChange={(e) => setEnableSudo(e.target.checked)}
          />
          <span>Grant the main user sudo access on the server</span>
        </label>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={enableTrusted}
            onChange={(e) => setEnableTrusted(e.target.checked)}
          />
          <span>
            Enable trusted SSH access (the user's servers can reach each other)
          </span>
        </label>
        <div className="row-actions">
          <button
            type="submit"
            className="btn primary"
            disabled={busy || !vmid || !name.trim()}
          >
            {busy ? "Adding..." : "Add template"}
          </button>
        </div>
      </form>
    </section>
  );
}

function ServerTemplatesListCard(props: {
  templates: ServerTemplate[];
  sshKeys: SshKey[];
  loading: boolean;
  onError: (message: string | null) => void;
  onDeleted: () => Promise<void> | void;
}) {
  const { templates, sshKeys, loading } = props;

  async function onDelete(template: ServerTemplate) {
    props.onError(null);
    try {
      await api.deleteServerTemplate(template.id);
      await props.onDeleted();
    } catch (err) {
      props.onError(
        err instanceof ApiError ? err.message : "Unable to delete the template.",
      );
    }
  }

  return (
    <section className="card">
      <h2>Server templates</h2>
      <p className="muted">
        Registered templates and the options applied to the servers they create.
      </p>
      {loading ? (
        <p role="status">Loading server templates...</p>
      ) : templates.length === 0 ? (
        <p className="muted">No server templates registered yet.</p>
      ) : (
        <div className="user-list">
          {templates.map((template) => {
            const adminKey =
              template.admin_ssh_key_id !== null
                ? sshKeys.find((k) => k.id === template.admin_ssh_key_id)
                    ?.name ?? `#${template.admin_ssh_key_id}`
                : template.admin_ssh_key_path || null;
            return (
              <article key={template.id} className="user-card">
                <div className="user-card-head">
                  <div className="user-identity">
                    <span className="user-name">{template.name}</span>
                    <span className="role-badge">
                      {template.kind.toUpperCase()}
                    </span>
                    <span className="status-badge ok">#{template.vmid}</span>
                  </div>
                  <div className="row-actions">
                    <button
                      type="button"
                      className="btn danger"
                      onClick={() => onDelete(template)}
                    >
                      Delete
                    </button>
                  </div>
                </div>
                <div className="badge-row">
                  <span className="status-badge">
                    Main user: {template.main_os_user || "(user's own ID)"}
                  </span>
                  <span
                    className={
                      template.enable_sudo
                        ? "status-badge ok"
                        : "status-badge off"
                    }
                  >
                    Sudo: {template.enable_sudo ? "on" : "off"}
                  </span>
                  <span
                    className={
                      template.enable_trusted_access
                        ? "status-badge ok"
                        : "status-badge off"
                    }
                  >
                    Trusted SSH: {template.enable_trusted_access ? "on" : "off"}
                  </span>
                </div>
                <p className="muted">
                  Admin key: {adminKey ?? "None"}
                </p>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
