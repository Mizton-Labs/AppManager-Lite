import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { CreateSshKeyInput, SshKey, UpdateSshKeyInput } from "../types";

/**
 * Settings -> Remote Access (administrators only).
 *
 * A registry of SSH keys used across the app. A key is either a reference to
 * a key file on the server (kind='path') or a private key pasted by the admin
 * and stored encrypted at rest in the database (kind='stored'). Other
 * sections select keys from this registry by name.
 */
export function RemoteAccessConfig() {
  const [keys, setKeys] = useState<SshKey[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setKeys(await api.listSshKeys());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load keys.");
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  return (
    <div className="grid">
      <AddKeyCard onAdded={refresh} />
      <section className="card">
        <h2>Registered SSH keys</h2>
        <p className="muted">
          Keys available to reverse-proxy, jump-server, and server-provisioning
          settings. Pasted private keys are stored encrypted and never shown
          again.
        </p>
        {error && (
          <p className="alert error" role="alert">
            {error}
          </p>
        )}
        {keys === null ? (
          <p role="status">Loading SSH keys...</p>
        ) : keys.length === 0 ? (
          <p className="muted">No SSH keys registered yet.</p>
        ) : (
          <div className="user-list">
            {keys.map((key) => (
              <KeyRow key={key.id} sshKey={key} onChanged={refresh} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function AddKeyCard(props: { onAdded: () => void | Promise<void> }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"path" | "stored">("path");
  const [path, setPath] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const input: CreateSshKeyInput =
        kind === "path"
          ? { name: name.trim(), kind, path: path.trim() }
          : { name: name.trim(), kind, private_key: privateKey };
      await api.createSshKey(input);
      setName("");
      setPath("");
      setPrivateKey("");
      await props.onAdded();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to add the key.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Add SSH key</h2>
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      <form className="create-form" onSubmit={onSubmit}>
        <label className="field">
          <span>Name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={60}
            required
          />
        </label>
        <fieldset className="field">
          <legend>Key source</legend>
          <label className="checkbox-field">
            <input
              type="radio"
              name="ssh-key-kind"
              checked={kind === "path"}
              onChange={() => setKind("path")}
            />
            <span>Reference a key file on the server (path)</span>
          </label>
          <label className="checkbox-field">
            <input
              type="radio"
              name="ssh-key-kind"
              checked={kind === "stored"}
              onChange={() => setKind("stored")}
            />
            <span>Paste a private key (stored encrypted in the database)</span>
          </label>
        </fieldset>
        {kind === "path" ? (
          <label className="field">
            <span>Key file path (absolute)</span>
            <input
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="/data/keys/id_ed25519"
            />
          </label>
        ) : (
          <label className="field">
            <span>Private key (unencrypted OpenSSH, no passphrase)</span>
            <textarea
              value={privateKey}
              onChange={(e) => setPrivateKey(e.target.value)}
              rows={8}
              placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;..."
            />
          </label>
        )}
        <div className="row-actions">
          <button
            type="submit"
            className="btn primary"
            disabled={
              busy ||
              !name.trim() ||
              (kind === "path" ? !path.trim() : !privateKey.trim())
            }
          >
            {busy ? "Adding..." : "Add key"}
          </button>
        </div>
      </form>
    </section>
  );
}

function KeyRow(props: { sshKey: SshKey; onChanged: () => void | Promise<void> }) {
  const { sshKey } = props;
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await api.deleteSshKey(sshKey.id);
      await props.onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to delete.");
      setBusy(false);
    }
  }

  return (
    <article className="user-card">
      <div className="user-card-head">
        <div className="user-identity">
          <span className="user-name">{sshKey.name}</span>
          <span className="role-badge">
            {sshKey.kind === "path" ? "PATH" : "STORED"}
          </span>
        </div>
        <div className="row-actions">
          {!editing && (
            <button
              type="button"
              className="btn ghost"
              onClick={() => {
                setEditing(true);
                setConfirming(false);
              }}
            >
              Edit
            </button>
          )}
          {confirming ? (
            <button
              type="button"
              className="btn danger"
              onClick={remove}
              disabled={busy}
            >
              Confirm delete
            </button>
          ) : (
            <button
              type="button"
              className="btn ghost"
              onClick={() => setConfirming(true)}
            >
              Delete
            </button>
          )}
        </div>
      </div>
      {sshKey.kind === "path" ? (
        <p className="muted">
          Path: <code>{sshKey.path}</code>
        </p>
      ) : (
        <p className="muted">
          {sshKey.fingerprint}
          {sshKey.public_key ? ` · ${sshKey.public_key.split(" ")[0]}` : ""}
        </p>
      )}
      {editing && (
        <EditKeyForm
          sshKey={sshKey}
          onCancel={() => setEditing(false)}
          onSaved={async () => {
            setEditing(false);
            await props.onChanged();
          }}
        />
      )}
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
    </article>
  );
}

function EditKeyForm(props: {
  sshKey: SshKey;
  onCancel: () => void;
  onSaved: () => void | Promise<void>;
}) {
  const { sshKey } = props;
  const [name, setName] = useState(sshKey.name);
  const [kind, setKind] = useState<"path" | "stored">(sshKey.kind);
  const [path, setPath] = useState(sshKey.path);
  const [privateKey, setPrivateKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const input: UpdateSshKeyInput = { name: name.trim(), kind };
      if (kind === "path") {
        input.path = path.trim();
      } else if (privateKey.trim()) {
        // Only send a new private key when one was entered.
        input.private_key = privateKey;
      }
      await api.updateSshKey(sshKey.id, input);
      await props.onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save the key.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="create-form field-group" onSubmit={onSubmit}>
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      <label className="field">
        <span>Name</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={60}
          required
        />
      </label>
      <fieldset className="field">
        <legend>Key source</legend>
        <label className="checkbox-field">
          <input
            type="radio"
            name={`edit-kind-${sshKey.id}`}
            checked={kind === "path"}
            onChange={() => setKind("path")}
          />
          <span>Reference a key file on the server (path)</span>
        </label>
        <label className="checkbox-field">
          <input
            type="radio"
            name={`edit-kind-${sshKey.id}`}
            checked={kind === "stored"}
            onChange={() => setKind("stored")}
          />
          <span>Paste a private key (stored encrypted)</span>
        </label>
      </fieldset>
      {kind === "path" ? (
        <label className="field">
          <span>Key file path (absolute)</span>
          <input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/data/keys/id_ed25519"
          />
        </label>
      ) : (
        <label className="field">
          <span>
            {sshKey.has_private_key
              ? "Replace private key (leave blank to keep the current one)"
              : "Private key (unencrypted OpenSSH, no passphrase)"}
          </span>
          <textarea
            value={privateKey}
            onChange={(e) => setPrivateKey(e.target.value)}
            rows={6}
            placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;..."
          />
        </label>
      )}
      <div className="row-actions">
        <button
          type="submit"
          className="btn primary"
          disabled={
            busy ||
            !name.trim() ||
            (kind === "path"
              ? !path.trim()
              : !sshKey.has_private_key && !privateKey.trim())
          }
        >
          {busy ? "Saving..." : "Save changes"}
        </button>
        <button
          type="button"
          className="btn ghost"
          onClick={props.onCancel}
          disabled={busy}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
