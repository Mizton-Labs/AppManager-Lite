import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type {
  ApiUser,
  BundleOption,
  ServerKeyRotation,
  SshKeyInfo,
} from "../types";
import { ChangePasswordForm } from "./ChangePasswordForm";
import { ThemePicker } from "./ThemePicker";

function saveTextFile(content: string, filename: string) {
  const blob = new Blob([content], { type: "text/plain" });
  saveBlob(blob, filename);
}

function saveBlob(blob: Blob, filename: string) {
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(href);
}

export function AccountPanel(props: {
  user: ApiUser;
  onPasswordChanged: () => void | Promise<void>;
}) {
  const { user } = props;
  return (
    <div className="account-layout">
      <div className="account-row-top">
        <ProfileCard user={user} onPasswordChanged={props.onPasswordChanged} />
        <SshKeyCard />
        <BundleDownloadCard />
      </div>
    </div>
  );
}

function ProfileCard(props: {
  user: ApiUser;
  onPasswordChanged: () => void | Promise<void>;
}) {
  const { user } = props;
  return (
    <section className="card">
      <h2>Profile</h2>
      <dl className="detail-list">
        <div>
          <dt>Username</dt>
          <dd>{user.username}</dd>
        </div>
        <div>
          <dt>User ID</dt>
          <dd>
            <code className="user-id">{user.user_id}</code>
          </dd>
        </div>
        <div>
          <dt>Role</dt>
          <dd>
            <span className="role-badge">{user.role}</span>
          </dd>
        </div>
        <div>
          <dt>Teams</dt>
          <dd>
            {user.teams.length > 0 ? (
              <span className="tag-row">
                {user.teams.map((team) => (
                  <span key={team} className="tag">
                    {team}
                  </span>
                ))}
              </span>
            ) : (
              <span className="muted">No teams assigned</span>
            )}
          </dd>
        </div>
        <div>
          <dt>Self-service</dt>
          <dd>
            {user.self_service ? (
              <span className="status-badge ok">enabled</span>
            ) : (
              <span className="muted">
                Disabled &mdash; new applications need administrator approval
              </span>
            )}
          </dd>
        </div>
        <div>
          <dt>Theme</dt>
          <dd>
            <ThemePicker compact hideLabel />
          </dd>
        </div>
      </dl>
      <div className="card-subsection">
        <h3>Change password</h3>
        <ChangePasswordForm onChanged={props.onPasswordChanged} />
      </div>
    </section>
  );
}

function SshKeyCard() {
  const [info, setInfo] = useState<SshKeyInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [rotation, setRotation] = useState<ServerKeyRotation[]>([]);

  useEffect(() => {
    api
      .getAccountSshKey()
      .then(setInfo)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) return;
        setError(
          err instanceof ApiError ? err.message : "Failed to load SSH key.",
        );
      })
      .finally(() => setLoading(false));
  }, []);

  async function download(part: "private" | "public") {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const result = await api.downloadAccountSshKey(part);
      saveTextFile(result.content, result.filename);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Unable to download the key.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function regenerate() {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const next = await api.regenerateAccountSshKey();
      setInfo(next);
      setRotation(next.rotation);
      setConfirming(false);
      const updated = next.rotation.filter((r) => r.status === "updated").length;
      const failed = next.rotation.filter((r) => r.status === "failed").length;
      setNotice(
        "A new SSH keypair was generated. Download the new private key; " +
          "the old key is no longer available." +
          (next.rotation.length > 0
            ? ` Key rotation on your servers: ${updated} updated` +
              (failed ? `, ${failed} failed` : "") +
              ` of ${next.rotation.length}.`
            : ""),
      );
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Unable to regenerate the key.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>SSH key</h2>
      <p className="muted">
        A personal SSH keypair generated for your account. Use it to access
        your provisioned servers.
      </p>
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
      {loading ? (
        <p role="status">Loading SSH key...</p>
      ) : info === null ? (
        <p className="muted">No SSH key is available for this account.</p>
      ) : (
        <>
          <label className="field">
            <span>Public key</span>
            <pre className="settings-snippet ssh-public-key">{info.public_key}</pre>
          </label>
          {info.generated_at && (
            <p className="muted">Generated {info.generated_at} (UTC)</p>
          )}
          <div className="row-actions row-actions-equal">
            <button
              type="button"
              className="btn primary"
              onClick={() => download("private")}
              disabled={busy}
            >
              Download private key
            </button>
            <button
              type="button"
              className="btn ghost"
              onClick={() => download("public")}
              disabled={busy}
            >
              Download public key
            </button>
            {!confirming ? (
              <button
                type="button"
                className="btn ghost"
                onClick={() => setConfirming(true)}
                disabled={busy}
              >
                Regenerate key
              </button>
            ) : (
              <button
                type="button"
                className="btn danger"
                onClick={regenerate}
                disabled={busy}
              >
                {busy ? "Regenerating..." : "Confirm regenerate"}
              </button>
            )}
          </div>
          {confirming && (
            <p className="alert error" role="alert">
              Regenerating replaces your keypair immediately. The current
              private key stops working; AppManager will attempt to remove
              the old public key from your servers and install the new one,
              and will show a per-server summary. This cannot be undone.
            </p>
          )}
          {rotation.length > 0 && (
            <div className="rotation-summary">
              <h3>Key rotation summary</h3>
              <ul>
                {rotation.map((entry) => (
                  <li key={entry.server}>
                    <span
                      className={
                        entry.status === "updated"
                          ? "status-badge ok"
                          : entry.status === "failed"
                            ? "status-badge warn"
                            : "status-badge off"
                      }
                    >
                      {entry.status}
                    </span>{" "}
                    {entry.server}
                    {entry.ip_address ? ` (${entry.ip_address})` : ""}
                    {entry.detail ? (
                      <span className="muted"> — {entry.detail}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function BundleDownloadCard() {
  const [bundles, setBundles] = useState<BundleOption[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = bundles.find((b) => String(b.id) === selectedId);

  useEffect(() => {
    api
      .listAccountBundles()
      .then((next) => {
        setBundles(next);
        setSelectedId(next[0] ? String(next[0].id) : "");
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Failed to load bundles."),
      )
      .finally(() => setLoading(false));
  }, []);

  async function download() {
    if (!selectedId) return;
    setError(null);
    setBusy(true);
    try {
      const result = await api.downloadAccountBundle(Number(selectedId));
      saveBlob(result.blob, result.filename);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to download bundle.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Bundle Downloads</h2>
      <p className="muted">
        Download a zip with your personal SSH configuration, your private and
        public keys, and a <code>connect_server_&lt;name&gt;.sh</code> script per
        server. Unzip it into your <code>~/.ssh</code> directory and run a
        connect script to reach a server without typing the full ssh command.
      </p>
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      {loading ? (
        <p role="status">Loading configuration files...</p>
      ) : bundles.length === 0 ? (
        <p className="muted">No configuration files are available yet.</p>
      ) : (
        <div className="create-form">
          <label className="field">
            <span>Available configuration files</span>
            <select value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>
              {bundles.map((bundle) => (
                <option key={bundle.id} value={bundle.id}>
                  {bundle.name}
                </option>
              ))}
            </select>
          </label>
          {selected?.description && (
            <p className="muted bundle-description">{selected.description}</p>
          )}
          <button
            type="button"
            className="btn primary"
            onClick={download}
            disabled={busy || !selectedId}
          >
            {busy ? "Preparing..." : "Download file"}
          </button>
        </div>
      )}
    </section>
  );
}
