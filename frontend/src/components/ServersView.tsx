import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type {
  OwnerServers,
  ServerStats,
  ServersOverview,
  StatsTimeframe,
  UserServer,
} from "../types";
import { Sparkline } from "./Sparkline";

const TIMEFRAMES: { id: StatsTimeframe; label: string }[] = [
  { id: "hour", label: "Last hour" },
  { id: "day", label: "Last day" },
  { id: "week", label: "Last week" },
];

/** Human-readable bytes (base 1024). */
function fmtBytes(n: number): string {
  if (!n || n < 1) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/** Human-readable bytes/second. */
function fmtRate(n: number): string {
  return `${fmtBytes(n)}/s`;
}

function last<T>(arr: T[]): T | undefined {
  return arr.length ? arr[arr.length - 1] : undefined;
}

/**
 * The 4 compact usage sparklines for one server (CPU, memory, disk, network).
 * Fetches its own stats lazily (per card) so a long list renders progressively
 * and one slow/failed server never blocks the others.
 */
function ServerStatsCards(props: {
  userId: number;
  serverId: number;
  timeframe: StatsTimeframe;
}) {
  const [stats, setStats] = useState<ServerStats | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    setStats(null);
    setFailed(false);
    api
      .getServerStats(props.userId, props.serverId, props.timeframe)
      .then((s) => {
        if (active) setStats(s);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [props.userId, props.serverId, props.timeframe]);

  if (failed) {
    return <p className="muted stats-note">Stats unavailable.</p>;
  }
  if (!stats) {
    return (
      <p className="muted stats-note" role="status">
        Loading stats…
      </p>
    );
  }
  if (!stats.available) {
    return <p className="muted stats-note">{stats.detail || "No stats."}</p>;
  }
  if (stats.points.length === 0) {
    return (
      <p className="muted stats-note">No usage data for this timeframe yet.</p>
    );
  }

  const pts = stats.points;
  const cpu = pts.map((p) => p.cpu_pct);
  const mem = pts.map((p) => p.mem);
  const disk = pts.map((p) => p.disk);
  const net = pts.map((p) => p.netin + p.netout);
  const latest = last(pts);
  const maxmem = latest?.maxmem || Math.max(...mem, 1);
  const maxdisk = latest?.maxdisk || Math.max(...disk, 1);
  const memPct = latest && maxmem ? (latest.mem / maxmem) * 100 : 0;
  const diskPct = latest && maxdisk ? (latest.disk / maxdisk) * 100 : 0;

  const tone = (pct: number): "ok" | "warn" | "full" =>
    pct > 90 ? "full" : pct >= 70 ? "warn" : "ok";

  return (
    <div className="stats-cards">
      <Sparkline
        label="CPU"
        values={cpu}
        max={100}
        valueLabel={`${(latest?.cpu_pct ?? 0).toFixed(0)}%`}
        tone={tone(latest?.cpu_pct ?? 0)}
      />
      <Sparkline
        label="Memory"
        values={mem}
        max={maxmem}
        valueLabel={fmtBytes(latest?.mem ?? 0)}
        tone={tone(memPct)}
      />
      <Sparkline
        label="Disk"
        values={disk}
        max={maxdisk}
        valueLabel={fmtBytes(latest?.disk ?? 0)}
        tone={tone(diskPct)}
      />
      <Sparkline
        label="Network"
        values={net}
        valueLabel={fmtRate((latest?.netin ?? 0) + (latest?.netout ?? 0))}
      />
    </div>
  );
}

function statusBadge(server: UserServer) {
  if (server.deletion_failed) {
    return <span className="status-badge rejected">deletion failed</span>;
  }
  if (server.deletion_pending) {
    return <span className="status-badge warn">deletion pending</span>;
  }
  if (server.status === "failed") {
    return <span className="status-badge warn">failed</span>;
  }
  if (server.status === "reference") {
    return <span className="status-badge ok">reference</span>;
  }
  return <span className="status-badge ok">running</span>;
}

function ServerRow(props: {
  ownerId: number;
  server: UserServer;
  timeframe: StatsTimeframe;
}) {
  const { server } = props;
  return (
    <article className="overview-server">
      <div className="overview-server-info">
        <div className="overview-server-head">
          <span className="server-name">{server.name}</span>
          <span className="role-badge">{server.kind.toUpperCase()}</span>
          {statusBadge(server)}
        </div>
        <p className="muted overview-server-meta">
          {server.ip_address || "no IP"}
          {" · "}
          {server.cpus} CPU · {server.memory_gb} GB RAM · {server.disk_gb} GB
          disk
        </p>
      </div>
      <ServerStatsCards
        userId={props.ownerId}
        serverId={server.id}
        timeframe={props.timeframe}
      />
    </article>
  );
}

function OwnerGroup(props: { owner: OwnerServers; timeframe: StatsTimeframe }) {
  const { owner } = props;
  return (
    <section className="overview-owner">
      <h3 className="overview-owner-head">
        {owner.username}
        {owner.derived_user_id && (
          <span className="muted"> ({owner.derived_user_id})</span>
        )}
        <span className="muted overview-owner-count">
          {" · "}
          {owner.servers.length} server{owner.servers.length === 1 ? "" : "s"}
        </span>
      </h3>
      {owner.servers.length === 0 ? (
        <p className="muted">No servers.</p>
      ) : (
        <div className="overview-server-list">
          {owner.servers.map((s) => (
            <ServerRow
              key={s.id}
              ownerId={owner.user_id}
              server={s}
              timeframe={props.timeframe}
            />
          ))}
        </div>
      )}
    </section>
  );
}

/**
 * The "Servers" section (issue_015-r5 F2). Lists every server the caller may
 * see, grouped by owner, with compact historical usage charts per server.
 * Administrators see all users; regular users see only their own servers.
 */
export function ServersView() {
  const [overview, setOverview] = useState<ServersOverview | null>(null);
  const [timeframe, setTimeframe] = useState<StatsTimeframe>("hour");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    api
      .getServersOverview()
      .then((o) => {
        if (active) setOverview(o);
      })
      .catch((err) => {
        if (active) {
          setError(
            err instanceof ApiError ? err.message : "Failed to load servers.",
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

  const owners = overview?.owners ?? [];
  const hasServers = owners.some((o) => o.servers.length > 0);

  return (
    <div className="view servers-view">
      <div className="view-head">
        <h2>Servers</h2>
        <label className="timeframe-select">
          <span className="muted">Timeframe</span>
          <select
            value={timeframe}
            onChange={(e) => setTimeframe(e.target.value as StatsTimeframe)}
            aria-label="Stats timeframe"
          >
            {TIMEFRAMES.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      {loading ? (
        <p role="status">Loading servers...</p>
      ) : !hasServers ? (
        <p className="muted">No servers to show.</p>
      ) : (
        <div className="overview-owners">
          {owners.map((o) => (
            <OwnerGroup key={o.user_id} owner={o} timeframe={timeframe} />
          ))}
        </div>
      )}
    </div>
  );
}
