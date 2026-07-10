import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type {
  ApiUser,
  OwnerServers,
  ServerAccess,
  ServerStats,
  ServersOverview,
  StatsTimeframe,
  UserServer,
} from "../types";
import { Sparkline } from "./Sparkline";
import { CreateServerCard, ServerCard } from "./UserServers";

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

function ServerRow(props: {
  ownerId: number;
  server: UserServer;
  timeframe: StatsTimeframe;
  isAdmin: boolean;
  allowResourceEdit: boolean;
  canDelete: boolean;
  onChanged: () => void | Promise<void>;
}) {
  const { server } = props;
  // The ServerCard is itself the bordered card; its charts slot renders the
  // usage sparklines inline (same row as the name/specs), and its action
  // buttons appear beneath -- restoring the pre-issue_022 compact layout.
  return (
    <ServerCard
      server={server}
      userId={props.ownerId}
      isAdmin={props.isAdmin}
      allowResourceEdit={props.allowResourceEdit}
      canDelete={props.canDelete}
      onChanged={props.onChanged}
      charts={
        <ServerStatsCards
          userId={props.ownerId}
          serverId={server.id}
          timeframe={props.timeframe}
        />
      }
    />
  );
}

function OwnerGroup(props: {
  owner: OwnerServers;
  timeframe: StatsTimeframe;
  isAdmin: boolean;
  currentUser: ApiUser;
  access: ServerAccess | null;
  onChanged: () => void | Promise<void>;
}) {
  const { owner } = props;
  const isOwnGroup = owner.user_id === props.currentUser.id;
  // Own servers are editable when the account allows resource edits; an admin
  // may act on any owner's servers. Non-owners (non-admin) stay read-only.
  const allowResourceEdit =
    props.isAdmin || (isOwnGroup && (props.access?.allow_resource_edit ?? false));
  // Deletion requires self-service for a normal user (the backend enforces
  // this too); an admin may delete any server.
  const canDelete =
    props.isAdmin || (isOwnGroup && props.currentUser.self_service);
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
              isAdmin={props.isAdmin}
              allowResourceEdit={allowResourceEdit}
              canDelete={canDelete}
              onChanged={props.onChanged}
            />
          ))}
        </div>
      )}
    </section>
  );
}

/**
 * The "Servers" section. The single place to create and manage your servers:
 * a create card on top (for the signed-in user), then every server the caller
 * may see, grouped by owner, each with compact historical usage charts and —
 * for servers the caller may act on — Change resources / Reboot / Delete.
 * Administrators see and manage all users' servers; a regular user sees and
 * manages only their own.
 */
export function ServersView(props: { currentUser: ApiUser; isAdmin: boolean }) {
  const [overview, setOverview] = useState<ServersOverview | null>(null);
  const [access, setAccess] = useState<ServerAccess | null>(null);
  const [timeframe, setTimeframe] = useState<StatsTimeframe>("hour");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const loadOverview = useCallback(async () => {
    try {
      setOverview(await api.getServersOverview());
      setError(null);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to load servers.",
      );
    }
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    const accessP = api
      .getAccountServerAccess()
      .then((a) => {
        if (active) setAccess(a);
      })
      .catch(() => {
        if (active)
          setAccess({ can_create: false, reason: "", allow_resource_edit: false });
      });
    Promise.all([accessP, loadOverview()]).finally(() => {
      if (active) setLoading(false);
    });
    return () => {
      active = false;
    };
  }, [loadOverview]);

  const owners = overview?.owners ?? [];
  const hasServers = owners.some((o) => o.servers.length > 0);
  const canCreate = access?.can_create ?? false;

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

      {access && !canCreate && access.reason && (
        <p className="muted">{access.reason}</p>
      )}
      {canCreate && (
        <CreateServerCard
          userId={props.currentUser.id}
          isAdmin={props.isAdmin}
          userDerivedId={props.currentUser.user_id}
          defaultPubkeyUser={props.currentUser.user_id}
          onCreated={loadOverview}
        />
      )}

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
            <OwnerGroup
              key={o.user_id}
              owner={o}
              timeframe={timeframe}
              isAdmin={props.isAdmin}
              currentUser={props.currentUser}
              access={access}
              onChanged={loadOverview}
            />
          ))}
        </div>
      )}
    </div>
  );
}
