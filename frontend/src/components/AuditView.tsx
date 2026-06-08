import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { AuditCategory, AuditEntry } from "../types";

const TABS: { id: AuditCategory; label: string }[] = [
  { id: "application", label: "Application Management" },
  { id: "user", label: "User activity" },
  { id: "system", label: "System" },
];

/** Render an ISO timestamp as a readable local string. */
function formatTime(iso: string): string {
  const date = new Date(iso.includes("Z") || iso.includes("+") ? iso : iso + "Z");
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

/**
 * Administrator audit log. Shows recorded actions grouped into three tabs:
 * Application Management, User activity, and System. Each tab loads the matching
 * category from the API on selection.
 */
export function AuditView() {
  const [tab, setTab] = useState<AuditCategory>("application");
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    api
      .listAuditLog(tab)
      .then((result) => {
        if (active) setEntries(result);
      })
      .catch((err) => {
        if (active) {
          setError(
            err instanceof ApiError ? err.message : "Failed to load the audit log.",
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [tab]);

  return (
    <div className="stack wide">
      <header className="view-head">
        <h1>Audit log</h1>
        <p className="muted">
          Recent actions performed in the portal, grouped by category.
        </p>
      </header>

      <nav className="tabs" aria-label="Audit categories">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={tab === t.id ? "tab active" : "tab"}
            aria-current={tab === t.id}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <section className="card">
        {loading ? (
          <p role="status">Loading audit log…</p>
        ) : error ? (
          <p className="alert error" role="alert">
            {error}
          </p>
        ) : entries.length === 0 ? (
          <p className="muted">No activity recorded in this category yet.</p>
        ) : (
          <table className="audit-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Action</th>
                <th>Actor</th>
                <th>Target</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <td className="audit-time">{formatTime(entry.created_at)}</td>
                  <td>
                    <span className="tag">{entry.action}</span>
                  </td>
                  <td>{entry.actor_username ?? "—"}</td>
                  <td>{entry.target_name ?? "—"}</td>
                  <td className="audit-detail">{entry.detail || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
