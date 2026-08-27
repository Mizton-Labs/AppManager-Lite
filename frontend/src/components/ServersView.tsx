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
import { PlusIcon } from "./icons";

/** Case-insensitive substring match of a server against a query, across its
 * searchable fields plus the owning group's identity. */
function serverMatches(
  server: UserServer,
  owner: OwnerServers,
  query: string,
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hay = [
    server.name,
    server.hostname,
    server.ip_address,
    server.template_name,
    server.kind,
    server.status,
    owner.username,
    owner.derived_user_id,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return hay.includes(q);
}

const TIMEFRAMES: { id: StatsTimeframe; label: string }[] = [
  { id: "hour", label: "Last hour" },
  { id: "day", label: "Last day" },
  { id: "week", label: "Last week" },
];

/** issue_local_032: owner cards are paginated, at most this many per page. */
const OWNERS_PER_PAGE = 10;

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
 * and one slow/failed server never blocks the others. Only ever mounted for
 * an expanded owner card (see OwnerGroup), so a collapsed card issues no
 * stats requests at all.
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
  allowAccessReset: boolean;
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
      allowAccessReset={props.allowAccessReset}
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

/** issue_local_032: one owner's card, collapsed by default. The collapsed
 * card shows only identity + matching server count; expanding it mounts the
 * server list (and therefore its usage-chart requests) for the first time --
 * a collapsed card never issues a stats request. */
function OwnerGroup(props: {
  owner: OwnerServers;
  timeframe: StatsTimeframe;
  isAdmin: boolean;
  currentUser: ApiUser;
  access: ServerAccess | null;
  filter: string;
  onChanged: () => void | Promise<void>;
}) {
  const { owner } = props;
  const [manuallyCollapsed, setManuallyCollapsed] = useState(true);
  const isFiltering = props.filter.trim().length > 0;
  // A group only ever renders (see the early return below) while filtering
  // if it has a match, so auto-expand it -- that's the point of searching.
  // Manual collapse state is preserved once the filter is cleared.
  const collapsed = isFiltering ? false : manuallyCollapsed;
  const isOwnGroup = owner.user_id === props.currentUser.id;
  // Own servers are editable when the account allows resource edits; an admin
  // may act on any owner's servers. Non-owners (non-admin) stay read-only.
  const allowResourceEdit =
    props.isAdmin || (isOwnGroup && (props.access?.allow_resource_edit ?? false));
  // Deletion requires self-service for a normal user (the backend enforces
  // this too); an admin may delete any server.
  const canDelete =
    props.isAdmin || (isOwnGroup && props.currentUser.self_service);
  const allowAccessReset =
    props.isAdmin || (isOwnGroup && props.currentUser.self_service);
  const visible = owner.servers.filter((s) =>
    serverMatches(s, owner, props.filter),
  );
  // With an active filter, a group with no matching servers is hidden entirely.
  if (props.filter.trim() && visible.length === 0) return null;
  const contentId = `owner-servers-${owner.user_id}`;
  return (
    <section className="overview-owner">
      <button
        type="button"
        className="overview-owner-toggle"
        aria-expanded={!collapsed}
        aria-controls={contentId}
        onClick={() => setManuallyCollapsed((c) => !c)}
      >
        <h3 className="overview-owner-head">
          {owner.username}
          {owner.derived_user_id && (
            <span className="muted"> ({owner.derived_user_id})</span>
          )}
          <span className="muted overview-owner-count">
            {" · "}
            {visible.length} server{visible.length === 1 ? "" : "s"}
          </span>
        </h3>
        <span className="overview-owner-chevron" aria-hidden="true">
          {collapsed ? "▸" : "▾"}
        </span>
      </button>
      {!collapsed && (
        <div id={contentId} role="region" aria-label={`${owner.username} servers`}>
          {visible.length === 0 ? (
            <p className="muted">No servers.</p>
          ) : (
            <div className="overview-server-list">
              {visible.map((s) => (
                <ServerRow
                  key={s.id}
                  ownerId={owner.user_id}
                  server={s}
                  timeframe={props.timeframe}
                  isAdmin={props.isAdmin}
                  allowResourceEdit={allowResourceEdit}
                  allowAccessReset={allowAccessReset}
                  canDelete={canDelete}
                  onChanged={props.onChanged}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

/**
 * The "Servers" section. The single place to create and manage your servers:
 * a create card on top (for the signed-in user), then every server the caller
 * may see, grouped by owner (each owner card collapsed by default), each with
 * compact historical usage charts and — for servers the caller may act on —
 * Change resources / Reboot / Delete. Administrators see and manage all
 * users' servers (paginated, 10 owner cards per page) and get top summary
 * cards for total users/servers; a regular user sees and manages only their
 * own.
 */
export function ServersView(props: { currentUser: ApiUser; isAdmin: boolean }) {
  const [overview, setOverview] = useState<ServersOverview | null>(null);
  const [access, setAccess] = useState<ServerAccess | null>(null);
  const [timeframe, setTimeframe] = useState<StatsTimeframe>("hour");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [creating, setCreating] = useState(false);
  const [page, setPage] = useState(0);

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
  // How many servers survive the active filter (for a no-match message).
  const visibleCount = owners.reduce(
    (n, o) => n + o.servers.filter((s) => serverMatches(s, o, filter)).length,
    0,
  );
  const filtering = filter.trim().length > 0;

  // issue_024: for an admin, surface the signed-in user's own servers first
  // ("My servers"), then everyone else's ("Users' servers"). Non-admins only
  // ever have their own group, so the split collapses to a single section.
  const groupVisibleCount = (o: OwnerServers) =>
    o.servers.filter((s) => serverMatches(s, o, filter)).length;
  const myGroups = owners.filter((o) => o.user_id === props.currentUser.id);
  const otherGroups = owners.filter((o) => o.user_id !== props.currentUser.id);
  const showSubsectionHeadings = props.isAdmin && otherGroups.length > 0;

  // issue_local_032: paginate the *filtered*, ownership-ordered (mine-first)
  // owner list at OWNERS_PER_PAGE. When everything fits on one page, keep the
  // existing "My servers" / "Users' servers" subsection headings; a
  // multi-page result renders as one flat, paginated list instead (mixing
  // independent per-page pagination with two disjoint subsections would be
  // needlessly confusing).
  const orderedOwners = [...myGroups, ...otherGroups];
  const filteredOwners = orderedOwners.filter(
    (o) => !filtering || groupVisibleCount(o) > 0,
  );
  const pageCount = Math.max(1, Math.ceil(filteredOwners.length / OWNERS_PER_PAGE));
  const clampedPage = Math.min(page, pageCount - 1);
  useEffect(() => {
    if (page !== clampedPage) setPage(clampedPage);
  }, [page, clampedPage]);
  const pageOwners = filteredOwners.slice(
    clampedPage * OWNERS_PER_PAGE,
    clampedPage * OWNERS_PER_PAGE + OWNERS_PER_PAGE,
  );
  const singlePage = pageCount <= 1;

  const renderGroups = (groups: OwnerServers[]) =>
    groups.map((o) => (
      <OwnerGroup
        key={o.user_id}
        owner={o}
        timeframe={timeframe}
        isAdmin={props.isAdmin}
        currentUser={props.currentUser}
        access={access}
        filter={filter}
        onChanged={loadOverview}
      />
    ));

  return (
    <div className="view servers-view">
      <div className="view-head">
        <h2>Servers</h2>
        <div className="view-head-controls">
          <input
            type="search"
            className="list-filter"
            placeholder="Filter servers…"
            aria-label="Filter servers"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setPage(0);
            }}
          />
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
      </div>

      {!loading && overview && (
        <div className="stats-kpis servers-summary-kpis">
          {props.isAdmin && (
            <section className="card stats-kpi">
              <span>Total users</span>
              <strong>{overview.total_users ?? 0}</strong>
            </section>
          )}
          <section className="card stats-kpi">
            <span>Total servers</span>
            <strong>{overview.total_servers ?? 0}</strong>
          </section>
        </div>
      )}

      {access && !canCreate && access.reason && (
        <p className="muted">{access.reason}</p>
      )}
      {canCreate &&
        (creating ? (
          <CreateServerCard
            userId={props.currentUser.id}
            isAdmin={props.isAdmin}
            userDerivedId={props.currentUser.user_id}
            defaultPubkeyUser={props.currentUser.user_id}
            /* Keep the card open after a create so its success/warning notice
               (incl. VM "enter its IP" guidance) stays readable; the refreshed
               server appears in the list below. The user collapses via Cancel
               (labeled Close once something has been created). */
            onCreated={loadOverview}
            onCancel={() => setCreating(false)}
          />
        ) : (
          <div className="manager-toolbar">
            <button
              type="button"
              className="btn accent"
              onClick={() => setCreating(true)}
            >
              <PlusIcon />
              <span className="btn-label">Add server</span>
            </button>
          </div>
        ))}

      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      {loading ? (
        <p role="status">Loading servers...</p>
      ) : !hasServers ? (
        <p className="muted">No servers to show.</p>
      ) : filtering && visibleCount === 0 ? (
        <p className="muted">No servers match the filter.</p>
      ) : (
        <>
          <div className="overview-owners">
            {singlePage && showSubsectionHeadings ? (
              <>
                {(!filtering || myGroups.some((o) => groupVisibleCount(o) > 0)) && (
                  <>
                    <h3 className="servers-subhead">My servers</h3>
                    {myGroups.length ? (
                      renderGroups(myGroups)
                    ) : (
                      <p className="muted">You have no servers.</p>
                    )}
                  </>
                )}
                {(!filtering ||
                  otherGroups.some((o) => groupVisibleCount(o) > 0)) && (
                  <>
                    <h3 className="servers-subhead">Users' servers</h3>
                    {renderGroups(otherGroups)}
                  </>
                )}
              </>
            ) : (
              renderGroups(pageOwners)
            )}
          </div>
          {pageCount > 1 && (
            <nav
              className="pagination"
              aria-label="Server owners pagination"
            >
              <button
                type="button"
                className="btn ghost"
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={clampedPage === 0}
              >
                Previous
              </button>
              <span className="muted">
                Page {clampedPage + 1} of {pageCount}
              </span>
              <button
                type="button"
                className="btn ghost"
                onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
                disabled={clampedPage >= pageCount - 1}
              >
                Next
              </button>
            </nav>
          )}
        </>
      )}
    </div>
  );
}
