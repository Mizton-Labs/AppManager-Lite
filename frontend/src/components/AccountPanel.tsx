import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { ApiUser } from "../types";
import { ChangePasswordForm } from "./ChangePasswordForm";

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

      <BundleDownloadCard />
    </div>
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
