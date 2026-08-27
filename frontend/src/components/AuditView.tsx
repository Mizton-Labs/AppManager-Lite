import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { AuditCategory, AuditEntry, NavigationActivityEntry } from "../types";

type ViewTab = AuditCategory | "navigation";

const TABS: { id: ViewTab; label: string }[] = [
  { id: "application", label: "Application Management" },
  { id: "user", label: "User activity" },
  { id: "system", label: "System" },
  { id: "navigation", label: "Navigation activity" },
];

/** issue_local_032: allowlisted navigation-activity destination per tab. */
const TAB_DESTINATION: Record<ViewTab, string> = {
  application: "audit.application",
  user: "audit.users",
  system: "audit.system",
  // The navigation-activity tab itself is not tracked as a destination (it
  // would be circular), so it is intentionally omitted from tracking below.
  navigation: "",
};

/** Render an ISO timestamp as a readable local string. */
function formatTime(iso: string): string {
  const date = new Date(iso.includes("Z") || iso.includes("+") ? iso : iso + "Z");
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

/**
 * Administrator audit log. Shows recorded actions grouped into three
 * security/administrative categories (Application Management, User activity,
 * System), plus a fourth "Navigation activity" tab showing bounded,
 * privacy-conscious browsing activity (issue_local_032) -- which sections
 * users visit, never raw URLs/query strings.
 */
export function AuditView() {
  const [tab, setTab] = useState<ViewTab>("application");
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [navEntries, setNavEntries] = useState<NavigationActivityEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    const request =
      tab === "navigation"
        ? api.listNavigationActivity().then((result) => {
            if (active) setNavEntries(result);
          })
        : api.listAuditLog(tab).then((result) => {
            if (active) setEntries(result);
          });
    request
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

  function selectTab(next: ViewTab) {
    setTab(next);
    if (TAB_DESTINATION[next]) {
      void api.recordNavigation(TAB_DESTINATION[next]).catch(() => undefined);
    }
  }

  return (
    <div className="stack wide">
      <header className="view-head">
        <h1>Audit log</h1>
        <p className="muted">
          Recent actions performed in the portal, grouped by category, plus
          bounded navigation activity (which sections users visit -- never
          raw URLs or query strings).
        </p>
      </header>

      <nav className="tabs" aria-label="Audit categories">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={tab === t.id ? "tab active" : "tab"}
            aria-current={tab === t.id}
            onClick={() => selectTab(t.id)}
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
        ) : tab === "navigation" ? (
          navEntries.length === 0 ? (
            <p className="muted">No navigation activity recorded yet.</p>
          ) : (
            <table className="audit-table">
              <thead>
                <tr>
                  <th>Last seen</th>
                  <th>User</th>
                  <th>Section</th>
                  <th>Visits</th>
                </tr>
              </thead>
              <tbody>
                {navEntries.map((entry) => (
                  <tr key={entry.id}>
                    <td className="audit-time">{formatTime(entry.last_seen_at)}</td>
                    <td>{entry.actor_username}</td>
                    <td>
                      <span className="tag">{entry.destination}</span>
                    </td>
                    <td>{entry.visit_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
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
