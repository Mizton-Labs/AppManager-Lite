import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { BrandingSettings, ReverseProxySettings } from "../types";
import { setBranding } from "../branding";
import { fileToLogoDataUrl } from "../lib/image";
import { resolveIconSrc } from "../lib/links";
import { PlusIcon, XIcon } from "./icons";

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
export function GeneralSettings(props: { onConfigured?: () => void } = {}) {
  return (
    <>
      <ApplicationBasicInformation onSaved={props.onConfigured} />
      <ReverseProxyConfiguration />
      <AboutCollaborators />
    </>
  );
}

/** Configurable application name + logo (generic branding). */
function ApplicationBasicInformation(props: { onSaved?: () => void }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const [appName, setAppName] = useState("");
  const [appLogo, setAppLogo] = useState("");
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    let active = true;
    api
      .getBrandingSettings()
      .then((result: BrandingSettings) => {
        if (!active) return;
        setAppName(result.app_name);
        setAppLogo(result.app_logo);
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
        // Saving the basic information completes first-time setup.
        configured: true,
      });
      setAppName(result.app_name);
      setAppLogo(result.app_logo);
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
function ReverseProxyConfiguration() {
  const [settings, setSettings] = useState<ReverseProxySettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [templateOpen, setTemplateOpen] = useState(false);

  const [host, setHost] = useState("");
  const [sshUser, setSshUser] = useState("");
  const [confPath, setConfPath] = useState("");
  const [keyPath, setKeyPath] = useState("");
  const [template, setTemplate] = useState("");

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
        setKeyPath(result.ssh_key_path);
        setTemplate(result.alias_template);
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
        ssh_key_path: keyPath.trim(),
        alias_template: template,
      });
      setSettings(result);
      setSaved(true);
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
          <span>Local SSH key path (for management)</span>
          <input
            type="text"
            value={keyPath}
            onChange={(e) => setKeyPath(e.target.value)}
            placeholder="/data/keys/proxy_ed25519"
          />
          <span className="muted logo-hint">
            Path to a private key file on this server. The key is never stored in
            the database or shown here.
          </span>
        </label>

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
