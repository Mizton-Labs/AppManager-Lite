import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { ApplicationStatistics, ApplicationStatisticsDetail, ApplicationTrendSeries } from "../types";

const COLORS = ["#5b8cff", "#f3a754", "#65c7a5", "#c78cff", "#ef758b", "#4ab6d7", "#c7c65d", "#e18c5c", "#7b9ddb", "#91b56b"];

/** issue_local_032: the tab bar shown below the main trend chart. */
type StatsTab = "applications" | "launch_users" | "favorites" | "alias_visits";

const TABS: { id: StatsTab; label: string }[] = [
  { id: "applications", label: "Applications" },
  { id: "launch_users", label: "Launch users" },
  { id: "favorites", label: "Favorites" },
  { id: "alias_visits", label: "Alias visits" },
];

/** issue_local_032 (follow-up): alias-user rows are paginated at this size. */
const ALIAS_USERS_PER_PAGE = 10;

export function AppStatisticsView() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<ApplicationStatistics | null>(null);
  const [showCards, setShowCards] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [details, setDetails] = useState<Record<number, ApplicationStatisticsDetail>>({});
  const [detailErrors, setDetailErrors] = useState<Record<number, string>>({});
  const [tab, setTab] = useState<StatsTab>("applications");
  const [aliasPage, setAliasPage] = useState(0);
  const [expandedAliasUser, setExpandedAliasUser] = useState<string | null>(null);
  useEffect(() => { void load(); }, [days]);
  async function load() { setError(null); try { const [stats, settings] = await Promise.all([api.getApplicationStatistics(days), api.getApplicationStatisticsSettings()]); setData(stats); setShowCards(settings.show_app_statistics); setExpanded(null); setDetails({}); setDetailErrors({}); setAliasPage(0); setExpandedAliasUser(null); } catch (err) { setError(err instanceof ApiError ? err.message : "Unable to load application statistics."); } }
  async function toggle() { const next = !showCards; setShowCards(next); try { await api.updateApplicationStatisticsSettings(next); } catch (err) { setShowCards(!next); setError(err instanceof ApiError ? err.message : "Unable to save statistics setting."); } }
  async function expand(id: number) { if (expanded === id) return setExpanded(null); setExpanded(id); if (!details[id]) { setDetailErrors(prev => ({...prev, [id]: ""})); try { const detail = await api.getApplicationStatisticsUsers(id, days); setDetails(prev => ({...prev, [id]: detail})); } catch (err) { setDetailErrors(prev => ({...prev, [id]: err instanceof ApiError ? err.message : "Unable to load user activity."})); } } }

  return <div className="stack wide app-statistics-view">
    <div className="view-head"><div><h1>App Statistics</h1><p className="muted">Authenticated portal launches and favorites. Launches indicate card activations, not destination HTTP requests. Authorized alias visits separately count direct/deep-linked/embedded navigation to a managed alias via nginx; anonymous visits (public aliases, or auth disabled) count toward the total but never toward unique alias users, and upstream success is not measured -- only that the request was authorized. Current favorites are a live snapshot, not bounded by the selected period.</p></div><label className="stats-toggle"><input type="checkbox" checked={showCards} onChange={toggle} /> Show 7-day launch counts on app cards</label></div>
    <div className="stats-period">{[7,30,90].map(value => <button type="button" className={days===value ? "btn active" : "btn ghost"} onClick={() => setDays(value)} key={value}>{value} days</button>)}</div>
    {error && <p className="alert error">{error}</p>}
    {!data ? <p role="status">Loading application statistics...</p> : <>
      <div className="stats-kpis">
        <KpiButton label="Launches" value={data.launches} onClick={() => setTab("applications")} />
        <KpiButton label="Unique launch users" value={data.unique_users} onClick={() => setTab("launch_users")} />
        <KpiButton label="Current favorites" value={data.favorites} onClick={() => setTab("favorites")} />
        <KpiButton label="Authorized alias visits" value={data.alias_visits} onClick={() => setTab("alias_visits")} />
        <KpiButton label="Unique alias users" value={data.unique_alias_users} onClick={() => setTab("alias_visits")} />
        <KpiButton label="Anonymous alias visits" value={data.anonymous_alias_visits} onClick={() => setTab("alias_visits")} />
      </div>
      <section className="card stats-panel"><h2>Activity by application</h2><MultiLineChart series={data.app_trends}/></section>
      <nav className="tabs stats-tabs" aria-label="Statistics detail">
        {TABS.map(t => <button type="button" key={t.id} className={tab===t.id ? "tab active" : "tab"} aria-current={tab===t.id} onClick={() => setTab(t.id)}>{t.label}</button>)}
      </nav>
      {tab === "applications" && (
        <section className="card stats-panel"><h2>Applications</h2>{data.applications.length === 0 ? <p className="muted">No applications available.</p> : <div className="stats-table">{data.applications.map(app => <div key={app.application_id}><button type="button" className="stats-row stats-expand" aria-expanded={expanded===app.application_id} onClick={() => void expand(app.application_id)}><strong>{app.name}</strong><span>{app.launches} launches</span><span>{app.unique_users} users</span><span>★ {app.favorites}</span><span title="Authorized alias visits (direct/deep-link/iframe), separate from launches">{app.alias_visits} alias visits</span></button>{expanded===app.application_id && <Detail detail={details[app.application_id]} error={detailErrors[app.application_id]} onRetry={() => void expand(app.application_id)}/>}</div>)}</div>}</section>
      )}
      {tab === "launch_users" && (
        <section className="card stats-panel">
          <h2>Launch users</h2>
          <UserBars rows={data.user_activity}/>
          {data.launch_users.length === 0 ? <p className="muted">No launches recorded in this period.</p> : (
            <div className="stats-table">
              {data.launch_users.map(u => (
                <div className="stats-row" key={u.user_id}>
                  <strong><code>{u.user_id}</code></strong>
                  <span>{u.launches} launches</span>
                  <span>{u.applications_used} apps</span>
                  <span>{u.active_days} active days</span>
                </div>
              ))}
            </div>
          )}
        </section>
      )}
      {tab === "favorites" && (
        <section className="card stats-panel">
          <h2>Favorites</h2>
          <p className="muted">Current snapshot -- not affected by the selected period.</p>
          {data.favorite_entries.length === 0 ? <p className="muted">No current favorites.</p> : (
            <div className="stats-table">
              {data.favorite_entries.map((f, i) => (
                <div className="stats-row" key={`${f.application_id}-${f.user_id}-${i}`}>
                  <strong>{f.application_name}</strong>
                  <span><code>{f.user_id}</code></span>
                  <span>{f.starred_at}</span>
                </div>
              ))}
            </div>
          )}
        </section>
      )}
      {tab === "alias_visits" && (
        <section className="card stats-panel">
          <h2>Alias visits</h2>
          <p className="muted">
            {data.anonymous_alias_visits} anonymous visit{data.anonymous_alias_visits === 1 ? "" : "s"} (public
            aliases, or auth disabled) counted toward the total above but never
            attributable to an individual user.
          </p>
          {data.alias_users.length === 0 ? <p className="muted">No authenticated alias visits in this period.</p> : (() => {
            const aliasPageCount = Math.max(1, Math.ceil(data.alias_users.length / ALIAS_USERS_PER_PAGE));
            const clampedAliasPage = Math.min(aliasPage, aliasPageCount - 1);
            const pageUsers = data.alias_users.slice(
              clampedAliasPage * ALIAS_USERS_PER_PAGE,
              clampedAliasPage * ALIAS_USERS_PER_PAGE + ALIAS_USERS_PER_PAGE,
            );
            return (
              <>
                <div className="stats-table">
                  {pageUsers.map(u => {
                    const isOpen = expandedAliasUser === u.user_id;
                    const contentId = `alias-user-apps-${u.user_id}`;
                    return (
                      <div key={u.user_id}>
                        <button
                          type="button"
                          className="stats-row stats-expand"
                          aria-expanded={isOpen}
                          aria-controls={contentId}
                          onClick={() => setExpandedAliasUser(isOpen ? null : u.user_id)}
                        >
                          <strong><code>{u.user_id}</code></strong>
                          <span>{u.alias_visits} visits</span>
                          <span>{u.applications_visited} apps</span>
                          <span>{u.active_days} active days</span>
                        </button>
                        {isOpen && (
                          <div id={contentId} className="stats-detail alias-user-apps">
                            {u.applications.length === 0 ? (
                              <p className="muted">No per-application detail available.</p>
                            ) : (
                              u.applications.map(a => (
                                <p key={a.application_id}>
                                  {a.application_name} · {a.alias_visits} visits
                                </p>
                              ))
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
                {aliasPageCount > 1 && (
                  <nav className="pagination" aria-label="Alias users pagination">
                    <button
                      type="button"
                      className="btn ghost"
                      onClick={() => setAliasPage(p => Math.max(0, p - 1))}
                      disabled={clampedAliasPage === 0}
                    >
                      Previous
                    </button>
                    <span className="muted">Page {clampedAliasPage + 1} of {aliasPageCount}</span>
                    <button
                      type="button"
                      className="btn ghost"
                      onClick={() => setAliasPage(p => Math.min(aliasPageCount - 1, p + 1))}
                      disabled={clampedAliasPage >= aliasPageCount - 1}
                    >
                      Next
                    </button>
                  </nav>
                )}
              </>
            );
          })()}
        </section>
      )}
    </>}
  </div>;
}
function KpiButton({label,value,onClick}:{label:string;value:number;onClick:()=>void}) {
  return <button type="button" className="card stats-kpi stats-kpi-button" onClick={onClick} aria-label={`Show ${label.toLowerCase()} details, ${value}`}>
    <span>{label}</span><strong>{value}</strong>
  </button>;
}
export function MultiLineChart({series}:{series:ApplicationTrendSeries[]}) {
  if (series.length === 0) return <p className="muted">No application activity in this period.</p>;
  const width = 760, height = 260;
  const pad = { left: 48, right: 16, top: 14, bottom: 34 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const points = series[0]?.points ?? [];
  const max = Math.max(1, ...series.flatMap(item => item.points.map(point => point.launches)));
  const x = (index: number) => pad.left + (index / Math.max(1, points.length - 1)) * plotWidth;
  const y = (value: number) => pad.top + (1 - value / max) * plotHeight;
  const path = (item: ApplicationTrendSeries) => item.points.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(point.launches).toFixed(1)}`).join(" ");
  const dateIndexes = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])].filter(index => index >= 0);
  const ticks = [...new Set(
    max <= 4
      ? Array.from({ length: max + 1 }, (_, index) => max - index)
      : [max, Math.round(max * .75), Math.round(max * .5), Math.round(max * .25), 0],
  )].sort((a, b) => b - a);
  return <>
    <div className="stats-legend">{series.map((item,index)=><span key={item.application_id}><i style={{background:COLORS[index]}}/>{item.name}</span>)}</div>
    <svg className="stats-lines" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Top application launches over time">
      {ticks.map(value => { const lineY = y(value); return <g key={value}><line className="stats-grid-line" x1={pad.left} x2={width-pad.right} y1={lineY} y2={lineY}/><text className="stats-axis-label" x={pad.left-8} y={lineY+4} textAnchor="end">{value}</text></g>; })}
      {dateIndexes.map(index => <text className="stats-axis-label" key={index} x={x(index)} y={height-10} textAnchor={index===0?"start":index===points.length-1?"end":"middle"}>{points[index]?.date.slice(5)}</text>)}
      {series.map((item,index)=><g key={item.application_id}>
        <path className="stats-series-line" d={path(item)} stroke={COLORS[index]}/>
        {item.points.map((point,pointIndex)=>point.launches>0?<circle className="stats-series-point" key={point.date} cx={x(pointIndex)} cy={y(point.launches)} r="4" fill={COLORS[index]}><title>{item.name}: {point.launches} launches on {point.date}</title></circle>:null)}
      </g>)}
    </svg>
  </>;
}
function UserBars({rows}:{rows:ApplicationStatistics["user_activity"]}) { if (rows.length === 0) return null; const max=Math.max(1,...rows.map(r=>r.launches)); return <div className="user-bars">{rows.map(row=><div key={row.user_id}><span>{row.user_id}</span><div><i style={{width:`${row.launches/max*100}%`}}/></div><b>{row.launches} · {row.applications_used} apps</b></div>)}</div>; }
function Detail({detail,error,onRetry}:{detail?:ApplicationStatisticsDetail;error?:string;onRetry:()=>void}) { if (error) return <p className="alert error">{error} <button type="button" className="btn ghost" onClick={onRetry}>Retry</button></p>; if (!detail) return <p className="muted">Loading user detail...</p>; return <div className="stats-detail"><div><h3>Activity users</h3>{detail.activity_users.length ? detail.activity_users.map(u=><p key={u.user_id}><code>{u.user_id}</code> · {u.launches} launches · {u.active_days} active days · {u.last_activity}</p>) : <p className="muted">No activity in this period.</p>}</div><div><h3>Starred by</h3>{detail.favorite_users.length ? detail.favorite_users.map(u=><p key={u.user_id}><code>{u.user_id}</code> · {u.starred_at}</p>) : <p className="muted">No current favorites.</p>}</div></div>; }
