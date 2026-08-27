import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { ApplicationStatistics, ApplicationStatisticsDetail, ApplicationTrendSeries } from "../types";

const COLORS = ["#5b8cff", "#f3a754", "#65c7a5", "#c78cff", "#ef758b", "#4ab6d7", "#c7c65d", "#e18c5c", "#7b9ddb", "#91b56b"];

export function AppStatisticsView() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<ApplicationStatistics | null>(null);
  const [showCards, setShowCards] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [details, setDetails] = useState<Record<number, ApplicationStatisticsDetail>>({});
  const [detailErrors, setDetailErrors] = useState<Record<number, string>>({});
  useEffect(() => { void load(); }, [days]);
  async function load() { setError(null); try { const [stats, settings] = await Promise.all([api.getApplicationStatistics(days), api.getApplicationStatisticsSettings()]); setData(stats); setShowCards(settings.show_app_statistics); setExpanded(null); setDetails({}); setDetailErrors({}); } catch (err) { setError(err instanceof ApiError ? err.message : "Unable to load application statistics."); } }
  async function toggle() { const next = !showCards; setShowCards(next); try { await api.updateApplicationStatisticsSettings(next); } catch (err) { setShowCards(!next); setError(err instanceof ApiError ? err.message : "Unable to save statistics setting."); } }
  async function expand(id: number) { if (expanded === id) return setExpanded(null); setExpanded(id); if (!details[id]) { setDetailErrors(prev => ({...prev, [id]: ""})); try { const detail = await api.getApplicationStatisticsUsers(id, days); setDetails(prev => ({...prev, [id]: detail})); } catch (err) { setDetailErrors(prev => ({...prev, [id]: err instanceof ApiError ? err.message : "Unable to load user activity."})); } } }
  return <div className="stack wide app-statistics-view">
    <div className="view-head"><div><h1>App Statistics</h1><p className="muted">Authenticated portal launches and favorites. Launches indicate card activations, not destination HTTP requests. Authorized alias visits (below) separately count direct/deep-linked/embedded navigation to a managed alias via nginx; anonymous visits (public aliases, or auth disabled) count toward the total but never toward unique alias users, and upstream success is not measured -- only that the request was authorized.</p></div><label className="stats-toggle"><input type="checkbox" checked={showCards} onChange={toggle} /> Show 7-day launch counts on app cards</label></div>
    <div className="stats-period">{[7,30,90].map(value => <button type="button" className={days===value ? "btn active" : "btn ghost"} onClick={() => setDays(value)} key={value}>{value} days</button>)}</div>
    {error && <p className="alert error">{error}</p>}
    {!data ? <p role="status">Loading application statistics...</p> : <>
      <div className="stats-kpis"><Kpi label="Launches" value={data.launches}/><Kpi label="Unique users" value={data.unique_users}/><Kpi label="Favorites" value={data.favorites}/><Kpi label="Authorized alias visits" value={data.alias_visits}/><Kpi label="Unique alias users" value={data.unique_alias_users}/></div>
      <section className="card stats-panel"><h2>Activity by application</h2><MultiLineChart series={data.app_trends}/></section>
      <section className="card stats-panel"><h2>Activity by user</h2><UserBars rows={data.user_activity}/></section>
      <section className="card stats-panel"><h2>Applications</h2>{data.applications.length === 0 ? <p className="muted">No launches recorded yet.</p> : <div className="stats-table">{data.applications.map(app => <div key={app.application_id}><button type="button" className="stats-row stats-expand" aria-expanded={expanded===app.application_id} onClick={() => void expand(app.application_id)}><strong>{app.name}</strong><span>{app.launches} launches</span><span>{app.unique_users} users</span><span>★ {app.favorites}</span><span title="Authorized alias visits (direct/deep-link/iframe), separate from launches">{app.alias_visits} alias visits</span></button>{expanded===app.application_id && <Detail detail={details[app.application_id]} error={detailErrors[app.application_id]} onRetry={() => void expand(app.application_id)}/>}</div>)}</div>}</section>
    </>}
  </div>;
}
function Kpi({label,value}:{label:string;value:number}) { return <section className="card stats-kpi"><span>{label}</span><strong>{value}</strong></section>; }
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
function UserBars({rows}:{rows:ApplicationStatistics["user_activity"]}) { const max=Math.max(1,...rows.map(r=>r.launches)); return <div className="user-bars">{rows.map(row=><div key={row.user_id}><span>{row.user_id}</span><div><i style={{width:`${row.launches/max*100}%`}}/></div><b>{row.launches} · {row.applications_used} apps</b></div>)}</div>; }
function Detail({detail,error,onRetry}:{detail?:ApplicationStatisticsDetail;error?:string;onRetry:()=>void}) { if (error) return <p className="alert error">{error} <button type="button" className="btn ghost" onClick={onRetry}>Retry</button></p>; if (!detail) return <p className="muted">Loading user detail...</p>; return <div className="stats-detail"><div><h3>Activity users</h3>{detail.activity_users.length ? detail.activity_users.map(u=><p key={u.user_id}><code>{u.user_id}</code> · {u.launches} launches · {u.active_days} active days · {u.last_activity}</p>) : <p className="muted">No activity in this period.</p>}</div><div><h3>Starred by</h3>{detail.favorite_users.length ? detail.favorite_users.map(u=><p key={u.user_id}><code>{u.user_id}</code> · {u.starred_at}</p>) : <p className="muted">No current favorites.</p>}</div></div>; }
