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
    <div className="view-head"><div><h1>App Statistics</h1><p className="muted">Authenticated portal launches and favorites. Launches indicate card activations, not destination HTTP requests.</p></div><label className="stats-toggle"><input type="checkbox" checked={showCards} onChange={toggle} /> Show 7-day launch counts on app cards</label></div>
    <div className="stats-period">{[7,30,90].map(value => <button type="button" className={days===value ? "btn active" : "btn ghost"} onClick={() => setDays(value)} key={value}>{value} days</button>)}</div>
    {error && <p className="alert error">{error}</p>}
    {!data ? <p role="status">Loading application statistics...</p> : <>
      <div className="stats-kpis"><Kpi label="Launches" value={data.launches}/><Kpi label="Unique users" value={data.unique_users}/><Kpi label="Favorites" value={data.favorites}/></div>
      <section className="card stats-panel"><h2>Activity by application</h2><MultiLineChart series={data.app_trends}/></section>
      <section className="card stats-panel"><h2>Activity by user</h2><UserBars rows={data.user_activity}/></section>
      <section className="card stats-panel"><h2>Applications</h2>{data.applications.length === 0 ? <p className="muted">No launches recorded yet.</p> : <div className="stats-table">{data.applications.map(app => <div key={app.application_id}><button type="button" className="stats-row stats-expand" aria-expanded={expanded===app.application_id} onClick={() => void expand(app.application_id)}><strong>{app.name}</strong><span>{app.launches} launches</span><span>{app.unique_users} users</span><span>★ {app.favorites}</span></button>{expanded===app.application_id && <Detail detail={details[app.application_id]} error={detailErrors[app.application_id]} onRetry={() => void expand(app.application_id)}/>}</div>)}</div>}</section>
    </>}
  </div>;
}
function Kpi({label,value}:{label:string;value:number}) { return <section className="card stats-kpi"><span>{label}</span><strong>{value}</strong></section>; }
function MultiLineChart({series}:{series:ApplicationTrendSeries[]}) { const max=Math.max(1,...series.flatMap(s=>s.points.map(p=>p.launches))); const n=Math.max(1,...series.map(s=>s.points.length)); const path=(s:ApplicationTrendSeries)=>s.points.map((p,i)=>`${i?"L":"M"}${(i/(n-1||1))*100},${100-(p.launches/max)*100}`).join(" "); return <><div className="stats-legend">{series.map((s,i)=><span key={s.application_id}><i style={{background:COLORS[i]}}/>{s.name}</span>)}</div><svg className="stats-lines" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Top application launches over time">{series.map((s,i)=><path key={s.application_id} d={path(s)} stroke={COLORS[i]} />)}</svg></>; }
function UserBars({rows}:{rows:ApplicationStatistics["user_activity"]}) { const max=Math.max(1,...rows.map(r=>r.launches)); return <div className="user-bars">{rows.map(row=><div key={row.user_id}><span>{row.user_id}</span><div><i style={{width:`${row.launches/max*100}%`}}/></div><b>{row.launches} · {row.applications_used} apps</b></div>)}</div>; }
function Detail({detail,error,onRetry}:{detail?:ApplicationStatisticsDetail;error?:string;onRetry:()=>void}) { if (error) return <p className="alert error">{error} <button type="button" className="btn ghost" onClick={onRetry}>Retry</button></p>; if (!detail) return <p className="muted">Loading user detail...</p>; return <div className="stats-detail"><div><h3>Activity users</h3>{detail.activity_users.length ? detail.activity_users.map(u=><p key={u.user_id}><code>{u.user_id}</code> · {u.launches} launches · {u.active_days} active days · {u.last_activity}</p>) : <p className="muted">No activity in this period.</p>}</div><div><h3>Starred by</h3>{detail.favorite_users.length ? detail.favorite_users.map(u=><p key={u.user_id}><code>{u.user_id}</code> · {u.starred_at}</p>) : <p className="muted">No current favorites.</p>}</div></div>; }
