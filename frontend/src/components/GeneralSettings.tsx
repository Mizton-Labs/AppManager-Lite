import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { BrandingSettings, ReverseProxySettings, SshKey } from "../types";
import { SubTabs } from "./SubTabs";
import { setBranding } from "../branding";
import { THEMES } from "../theme";
import { fileToLogoDataUrl } from "../lib/image";
import { resolveIconSrc } from "../lib/links";
import { PlusIcon, XIcon } from "./icons";

const AUTH_PROXY_SNIPPET = `location = /api/auth/proxy-check {
    proxy_pass http://APPMANAGER_HOST:APPMANAGER_PORT/api/auth/proxy-check;
    proxy_set_header Host $host;
    proxy_set_header Cookie $http_cookie;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
}

location @appmanager_login {
    return 302 /?next=$request_uri;
}`;

/**
 * General Settings (admin). Three cards:
 *
 * - **Application Basic Information** -- the configurable application name and
 *   logo (generic branding). Saving updates branding immediately and marks the
 *   deployment configured (which dismisses the first-login wizard).
 * - **Reverse Proxy Configuration** -- the nginx host, conf path, SSH key path,
 *   and alias template used to push application aliases.
 * - **About Collaborators** -- an admin-managed list of names shown on the
 *   About page (separate from the git-derived development team).
 *
 * The SSH key itself is never stored or shown here -- only the path to a key
 * file on the server.
 */
export function GeneralSettings(
  props: { firstRun?: boolean; onConfigured?: () => void } = {},
) {
  return (
    <SubTabs
      ariaLabel="General settings sections"
      // On first run, land on the Reverse Proxy sub-tab: saving that form
      // (with a successful protected-alias status) is what completes setup.
      initialTab={props.firstRun ? "reverse-proxy" : undefined}
      tabs={[
        {
          id: "basic",
          label: "Basic Information",
          render: () => (
            <ApplicationBasicInformation
              markConfigured={!props.firstRun}
              onSaved={props.onConfigured}
            />
          ),
        },
        {
          id: "reverse-proxy",
          label: "Reverse Proxy",
          render: () => (
            <ReverseProxyConfiguration
              firstRun={props.firstRun}
              onConfigured={props.onConfigured}
            />
          ),
        },
        {
          id: "collaborators",
          label: "Collaborators",
          render: () => <AboutCollaborators />,
        },
      ]}
    />
  );
}

/** Configurable application name + logo (generic branding). */
function ApplicationBasicInformation(props: {
  markConfigured: boolean;
  onSaved?: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const [appName, setAppName] = useState("");
  const [appLogo, setAppLogo] = useState("");
  const [defaultTheme, setDefaultTheme] = useState("dark-modern");
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    let active = true;
    api
      .getBrandingSettings()
      .then((result: BrandingSettings) => {
        if (!active) return;
        setAppName(result.app_name);
        setAppLogo(result.app_logo);
        setDefaultTheme(result.default_theme);
      })
      .catch((err) => {
        if (active) {
          setError(
            err instanceof ApiError ? err.message : "Failed to load branding.",
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function onLogoFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      setAppLogo(await fileToLogoDataUrl(file));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not process that image.",
      );
    } finally {
      setUploading(false);
    }
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSaved(false);
    setBusy(true);
    try {
      const result = await api.updateBrandingSettings({
        app_name: appName.trim(),
        app_logo: appLogo.trim(),
        default_theme: defaultTheme,
        configured: props.markConfigured ? true : undefined,
      });
      setAppName(result.app_name);
      setAppLogo(result.app_logo);
      setDefaultTheme(result.default_theme);
      // Reflect the new branding across the UI immediately.
      setBranding(result);
      setSaved(true);
      props.onSaved?.();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to save branding.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <p role="status">Loading branding…</p>;
  }

  return (
    <section className="card">
      <h2>Application Basic Information</h2>
      <p className="muted">
        Set the application name and logo shown across the portal, including the
        sign-in page.
      </p>

      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      {saved && (
        <p className="alert success" role="status">
          Branding saved.
        </p>
      )}

      <form className="create-form" onSubmit={onSubmit}>
        <label className="field">
          <span>Application name</span>
          <input
            type="text"
            value={appName}
            onChange={(e) => setAppName(e.target.value)}
            placeholder="My Security Portal"
            maxLength={128}
          />
        </label>

        <div className="field logo-field">
          <span>Logo</span>
          <div className="logo-field-row">
            <span className="logo-preview" aria-hidden="true">
              {appLogo ? (
                <img
                  src={resolveIconSrc(appLogo)}
                  alt=""
                  width={32}
                  height={32}
                />
              ) : (
                <span className="logo-preview-empty">—</span>
              )}
            </span>
            <div className="logo-field-controls">
              <input
                type="file"
                accept="image/png,image/webp,image/jpeg"
                onChange={onLogoFile}
                aria-label="Upload application logo"
              />
              {appLogo && (
                <button
                  type="button"
                  className="btn ghost"
                  onClick={() => setAppLogo("")}
                >
                  {uploading ? "…" : "Clear logo"}
                </button>
              )}
            </div>
          </div>
          <span className="muted logo-hint">
            Upload a PNG, WebP, or JPEG (auto-resized). A default logo is used
            when left blank.
          </span>
        </div>

        <label className="field">
          <span>Default theme</span>
          <select
            aria-label="Default theme"
            value={defaultTheme}
            onChange={(e) => setDefaultTheme(e.target.value)}
          >
            {THEMES.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
          <span className="muted logo-hint">
            Applied to users who have not chosen their own theme in Account.
          </span>
        </label>

        <div className="row-actions">
          <button type="submit" className="btn primary" disabled={busy}>
            {busy ? "Saving…" : "Save basic information"}
          </button>
        </div>
      </form>
    </section>
  );
}

/**
 * Reverse Proxy Configuration. Lets an administrator set the nginx host, the
 * conf file path on that host, the path to the local SSH key used to push
 * config, and the alias template (collapsed by default).
 */
function ReverseProxyConfiguration(props: {
  firstRun?: boolean;
  onConfigured?: () => void | Promise<void>;
}) {
  const [settings, setSettings] = useState<ReverseProxySettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [templateOpen, setTemplateOpen] = useState(false);

  const [host, setHost] = useState("");
  const [sshUser, setSshUser] = useState("");
  const [confPath, setConfPath] = useState("");
  const [keyId, setKeyId] = useState("");
  const [sshKeys, setSshKeys] = useState<SshKey[]>([]);
  const [appmanagerHost, setAppmanagerHost] = useState("");
  const [appmanagerPort, setAppmanagerPort] = useState("8000");
  const [template, setTemplate] = useState("");
  const [setupLog, setSetupLog] = useState("");
  const [setupStatus, setSetupStatus] = useState("");

  useEffect(() => {
    let active = true;
    api
      .getReverseProxySettings()
      .then((result) => {
        if (!active) return;
        setSettings(result);
        setHost(result.nginx_host);
        setSshUser(result.nginx_user);
        setConfPath(result.nginx_conf_path);
        setKeyId(
          result.reverse_proxy_ssh_key_id !== null
            ? String(result.reverse_proxy_ssh_key_id)
            : "",
        );
        setAppmanagerHost(result.appmanager_proxy_host || window.location.hostname);
        setAppmanagerPort(result.appmanager_proxy_port || "8000");
        setTemplate(result.alias_template);
        setSetupLog(result.protected_alias_auth_log);
        setSetupStatus(result.protected_alias_auth_status);
      })
      .catch((err) => {
        if (active) {
          setError(
            err instanceof ApiError ? err.message : "Failed to load settings.",
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    api
      .listSshKeys()
      .then((keys) => {
        if (active) setSshKeys(Array.isArray(keys) ? keys : []);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSaved(false);
    setBusy(true);
    try {
      const result = await api.updateReverseProxySettings({
        nginx_host: host.trim(),
        nginx_user: sshUser.trim(),
        nginx_conf_path: confPath.trim(),
        reverse_proxy_ssh_key_id: keyId ? Number(keyId) : null,
        appmanager_proxy_host: appmanagerHost.trim(),
        appmanager_proxy_port: appmanagerPort.trim(),
        alias_template: template,
      });
      setSettings(result);
      setSetupLog(result.protected_alias_auth_log);
      setSetupStatus(result.protected_alias_auth_status);
      setSaved(true);
      if (props.firstRun && result.protected_alias_auth_status === "ok") {
        await api.updateBrandingSettings({ configured: true });
        await props.onConfigured?.();
      }
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to save settings.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <p role="status">Loading settings…</p>;
  }

  return (
    <section className="card">
      <h2>Reverse Proxy Configuration</h2>
      <p className="muted">
        Configure the nginx server used to serve application aliases. Aliases are
        pushed when an application is approved.
      </p>

      <div className="alert warn" role="note">
        <strong>Alias authentication requirement.</strong> To protect direct alias
        links, the nginx server must call AppManager before proxying apps. Add
        this snippet inside the same <code>server</code> block that serves aliases,
        replacing <code>APPMANAGER_HOST</code> and <code>APPMANAGER_PORT</code> with
        the backend address:
        <pre className="settings-snippet"><code>{AUTH_PROXY_SNIPPET}</code></pre>
        The alias template must also include <code>auth_request /api/auth/proxy-check</code>{" "}
        and <code>error_page 401 = @appmanager_login;</code>. AppManager and aliases
        should be served from the same domain so the session cookie is sent to
        alias requests.
      </div>

      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      {saved && (
        <p className="alert success" role="status">
          Settings saved.
        </p>
      )}
      {setupStatus && (
        <div
          className={setupStatus === "ok" ? "alert success" : "alert error"}
          role="status"
        >
          <strong>
            {setupStatus === "ok"
              ? "Protected alias authentication configured."
              : "Protected alias authentication setup did not complete."}
          </strong>
          {setupLog && <pre className="settings-snippet"><code>{setupLog}</code></pre>}
        </div>
      )}

      <form className="create-form" onSubmit={onSubmit}>
        <div className="form-row">
          <label className="field">
            <span>NGINX Server Host/IP</span>
            <input
              type="text"
              value={host}
              onChange={(e) => setHost(e.target.value)}
              placeholder="proxy.example.com"
            />
          </label>
          <label className="field">
            <span>SSH user</span>
            <input
              type="text"
              value={sshUser}
              onChange={(e) => setSshUser(e.target.value)}
              placeholder="deploy"
            />
          </label>
        </div>

        <label className="field">
          <span>NGINX conf file path</span>
          <input
            type="text"
            value={confPath}
            onChange={(e) => setConfPath(e.target.value)}
            placeholder="/etc/nginx/conf.d/apps.conf"
          />
        </label>

        <label className="field">
          <span>SSH key (for management)</span>
          <select value={keyId} onChange={(e) => setKeyId(e.target.value)}>
            <option value="">None</option>
            {sshKeys.map((k) => (
              <option key={k.id} value={k.id}>
                {k.name} ({k.kind})
              </option>
            ))}
          </select>
          <span className="muted logo-hint">
            Select a key registered under Settings &rarr; Remote Access.
          </span>
        </label>

        <div className="form-row">
          <label className="field">
            <span>AppManager backend host/IP reachable from nginx</span>
            <input
              type="text"
              value={appmanagerHost}
              onChange={(e) => setAppmanagerHost(e.target.value)}
              placeholder={window.location.hostname || "127.0.0.1"}
              required={props.firstRun}
            />
            <span className="muted logo-hint">
              Suggested from this browser: {window.location.hostname || "127.0.0.1"}.
              Confirm this is the address nginx can use to reach AppManager.
            </span>
          </label>
          <label className="field">
            <span>AppManager backend port reachable from nginx</span>
            <input
              type="text"
              value={appmanagerPort}
              onChange={(e) => setAppmanagerPort(e.target.value)}
              placeholder="8000"
              required={props.firstRun}
            />
          </label>
        </div>

        <div className="field">
          <button
            type="button"
            className="btn ghost"
            aria-expanded={templateOpen}
            onClick={() => setTemplateOpen((v) => !v)}
          >
            {templateOpen ? "Hide" : "Show"} alias template
          </button>
          {templateOpen && (
            <label className="field">
              <span>Alias template</span>
              <textarea
                className="settings-template"
                value={template}
                onChange={(e) => setTemplate(e.target.value)}
                rows={18}
                spellCheck={false}
              />
              <span className="muted logo-hint">
                Placeholders <code>APPS_SERVER</code>, <code>APPS_PORT</code> and{" "}
                <code>ALIAS</code> are replaced when an alias is pushed.
              </span>
            </label>
          )}
        </div>

        <div className="row-actions">
          <button type="submit" className="btn primary" disabled={busy}>
            {busy ? "Saving…" : "Save settings"}
          </button>
          {settings && settings.nginx_host && (
            <span className="muted">
              Current target:{" "}
               {settings.nginx_user
                 ? `${settings.nginx_user}@${settings.nginx_host}`
                 : settings.nginx_host}
            </span>
          )}
        </div>
      </form>
    </section>
  );
}

/**
 * About Collaborators. An admin-managed list of names rendered on the About
 * page under "Collaborators" (separate from the git-derived development team).
 * Add a name with the textbox, remove with the x button, then Save.
 */
function AboutCollaborators() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const [collaborators, setCollaborators] = useState<string[]>([]);
  const [name, setName] = useState("");

  useEffect(() => {
    let active = true;
    api
      .getBrandingSettings()
      .then((result: BrandingSettings) => {
        if (!active) return;
        setCollaborators(result.collaborators);
      })
      .catch((err) => {
        if (active) {
          setError(
            err instanceof ApiError
              ? err.message
              : "Failed to load collaborators.",
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  function addName() {
    const trimmed = name.trim();
    if (!trimmed) return;
    // Ignore case-insensitive duplicates.
    if (collaborators.some((c) => c.toLowerCase() === trimmed.toLowerCase())) {
      setName("");
      return;
    }
    setCollaborators((prev) => [...prev, trimmed]);
    setName("");
    setSaved(false);
  }

  function removeName(target: string) {
    setCollaborators((prev) => prev.filter((c) => c !== target));
    setSaved(false);
  }

  function onInputKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    // Enter adds the name without submitting any surrounding form.
    if (event.key === "Enter") {
      event.preventDefault();
      addName();
    }
  }

  async function onSave() {
    setError(null);
    setSaved(false);
    setBusy(true);
    try {
      const result = await api.updateBrandingSettings({ collaborators });
      setCollaborators(result.collaborators);
      // Reflect on the About page immediately.
      setBranding(result);
      setSaved(true);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to save collaborators.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <p role="status">Loading collaborators…</p>;
  }

  return (
    <section className="card">
      <h2>About Collaborators</h2>
      <p className="muted">
        Names listed here appear under <strong>Collaborators</strong> on the
        About page. This is separate from the development team, which comes from
        the repository commit history.
      </p>

      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      {saved && (
        <p className="alert success" role="status">
          Collaborators saved.
        </p>
      )}

      <div className="create-form">
        <div className="collaborator-add">
          <label className="field">
            <span>Collaborator name</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={onInputKeyDown}
              placeholder="e.g. Jane Doe"
              maxLength={80}
              aria-label="Collaborator name"
            />
          </label>
          <button
            type="button"
            className="btn ghost"
            onClick={addName}
            disabled={!name.trim()}
          >
            <PlusIcon />
            <span className="btn-label">Add</span>
          </button>
        </div>

        {collaborators.length === 0 ? (
          <p className="muted">No collaborators added yet.</p>
        ) : (
          <ul className="collaborator-list" aria-label="Collaborators">
            {collaborators.map((c) => (
              <li key={c} className="collaborator-item">
                <span>{c}</span>
                <button
                  type="button"
                  className="icon-btn"
                  onClick={() => removeName(c)}
                  aria-label={`Remove ${c}`}
                  title="Remove"
                >
                  <XIcon />
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="row-actions">
          <button
            type="button"
            className="btn primary"
            onClick={onSave}
            disabled={busy}
          >
            {busy ? "Saving…" : "Save collaborators"}
          </button>
        </div>
      </div>
    </section>
  );
}
