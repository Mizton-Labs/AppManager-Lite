import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import type { Application } from "../types";
import { teamFromSlug } from "../teams";
import { AppCard } from "./AppCard";

/**
 * Per-team view: the applications configured for a single team. The team is
 * resolved from the URL slug against all configured teams the account can see in
 * the sidebar. Each team page shows apps shared to that team.
 */
export function TeamView(props: { teams: readonly string[] }) {
  const { slug } = useParams();
  const team = slug ? teamFromSlug(slug, props.teams) : null;

  if (!team) {
    return (
      <section className="card">
        <h1>Unknown team</h1>
        <p className="muted">That team does not exist.</p>
        <Link to="/" className="btn ghost">
          Back to Home
        </Link>
      </section>
    );
  }

  return <TeamApplications team={team} />;
}

function TeamApplications(props: { team: string }) {
  const { team } = props;
  const [apps, setApps] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    api
      .listApplications(team)
      .then((result) => {
        if (active) setApps(result);
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
  }, [team]);

  return (
    <div className="stack wide">
      <header className="view-head">
        <h1>{team}</h1>
        <p className="muted">Applications available to this team.</p>
      </header>

      {loading ? (
        <p role="status">Loading applications…</p>
      ) : error ? (
        <p className="alert error" role="alert">
          {error}
        </p>
      ) : apps.length === 0 ? (
        <section className="card">
          <p className="muted">
            No applications have been configured for this team yet. An
            administrator can add them from Settings &rarr; Application
            Manager.
          </p>
        </section>
      ) : (
        <div className="card-grid">
          {apps.map((app) => (
            <AppCard key={app.id} app={app} />
          ))}
        </div>
      )}
    </div>
  );
}
