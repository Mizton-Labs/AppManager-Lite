import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Application } from "../types";
import { getAppName } from "../branding";
import { AppCard } from "./AppCard";

/**
 * Landing view. Shows two groups of applications:
 *
 * - **My Applications** -- the apps the signed-in account created that are live
 *   on the portal.
 * - **Available shared applications** -- everything the signed-in account can
 *   see by team scope (created by administrators or other users), excluding the
 *   account's own apps.
 *
 * Visibility is enforced server-side. The shared list (`GET /applications`)
 * never reveals creators, so ownership is determined by intersecting it with the
 * caller's own apps (`GET /applications/mine`) by id.
 */
export function HomeView(props: { teams: readonly string[] }) {
  const [shared, setShared] = useState<Application[]>([]);
  const [owned, setOwned] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([api.listApplications(), api.listMyApplications()])
      .then(([visible, mine]) => {
        if (!active) return;
        const ownedIds = new Set(mine.map((app) => app.id));
        // Partition the visible (approved + active) apps into owned vs shared.
        setOwned(visible.filter((app) => ownedIds.has(app.id)));
        setShared(visible.filter((app) => !ownedIds.has(app.id)));
      })
      .catch((err) => {
        if (active) {
          setError(
            err instanceof ApiError
              ? err.message
              : "Failed to load applications.",
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

  return (
    <div className="stack wide">
      <section className="home-hero card">
        <h1>Welcome to {getAppName()}</h1>
        <p className="muted">
          Central access point for your applications. Open a tool below, or
          choose a team from the sidebar.
        </p>
      </section>

      {loading ? (
        <p role="status">Loading applications…</p>
      ) : error ? (
        <p className="alert error" role="alert">
          {error}
        </p>
      ) : (
        <>
          <section>
            <h2 className="section-title">My Applications</h2>
            {owned.length === 0 ? (
              <p className="muted">
                You have not published any applications yet. Add one from
                App Manager.
              </p>
            ) : (
              <div className="card-grid">
                {owned.map((app) => (
                  <AppCard
                    key={app.id}
                    app={app}
                    editHref={`/app-manager?editApp=${app.id}`}
                  />
                ))}
              </div>
            )}
          </section>

          <section>
            <h2 className="section-title">Available shared applications</h2>
            {shared.length === 0 ? (
              <p className="muted">
                {props.teams.length === 0
                  ? "No teams are assigned to your account yet. Contact an administrator."
                  : "No shared applications are available for your teams yet."}
              </p>
            ) : (
              <div className="card-grid">
                {shared.map((app) => (
                  <AppCard key={app.id} app={app} />
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
