import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { ApiUser, ServerAccess, SshKeyInfo } from "../types";
import { ChangePasswordForm } from "./ChangePasswordForm";
import { UserServersPanel } from "./UserServers";

function saveTextFile(content: string, filename: string) {
  const blob = new Blob([content], { type: "text/plain" });
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
    <div className="grid">
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
        </dl>
      </section>

      <section className="card">
        <h2>Change password</h2>
        <ChangePasswordForm onChanged={props.onPasswordChanged} />
      </section>

      <SshKeyCard />

      <MyServersCard user={user} />

      <BundleDownloadCard />
    </div>
  );
}

function MyServersCard(props: { user: ApiUser }) {
  const [access, setAccess] = useState<ServerAccess | null>(null);

  useEffect(() => {
    api
      .getAccountServerAccess()
      .then(setAccess)
      .catch(() => setAccess({ can_create: false, reason: "" }));
  }, []);

  return (
    <section className="card">
      <h2>My servers</h2>
      <p className="muted">
        Your provisioned servers. LXC servers receive their IP automatically;
        for a VM, configure it in Proxmox and enter its IP here.
      </p>
      {access && !access.can_create && access.reason && (
        <p className="muted">{access.reason}</p>
      )}
      <UserServersPanel
        userId={props.user.id}
        canCreate={access?.can_create ?? false}
        canDelete={props.user.self_service || props.user.role === "admin"}
        defaultPubkeyUser={props.user.user_id}
      />
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
      setConfirming(false);
      setNotice(
        "A new SSH keypair was generated. Download the new private key; the old key is no longer available.",
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
          <div className="row-actions">
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
              private key stops working and servers that trust the old public
              key must be updated. This cannot be undone.
            </p>
          )}
        </>
      )}
    </section>
  );
}

function BundleDownloadCard() {
  const [bundles, setBundles] = useState<{ id: number; name: string }[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      const blob = new Blob([result.content], { type: "text/plain" });
      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = result.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(href);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to download bundle.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Bundles</h2>
      <p className="muted">
        Download a personal configuration bundle generated from administrator
        templates and your account details.
      </p>
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      {loading ? (
        <p role="status">Loading bundles...</p>
      ) : bundles.length === 0 ? (
        <p className="muted">No bundles are available yet.</p>
      ) : (
        <div className="create-form">
          <label className="field">
            <span>Available bundles</span>
            <select value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>
              {bundles.map((bundle) => (
                <option key={bundle.id} value={bundle.id}>
                  {bundle.name}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn primary"
            onClick={download}
            disabled={busy || !selectedId}
          >
            {busy ? "Preparing..." : "Download bundle"}
          </button>
        </div>
      )}
    </section>
  );
}
