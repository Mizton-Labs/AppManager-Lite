import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { ApplicationStatistics } from "../types";

export function AppStatisticsView() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<ApplicationStatistics | null>(null);
  const [showCards, setShowCards] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { void load(); }, [days]);
  async function load() {
    setError(null);
    try {
      const [stats, settings] = await Promise.all([api.getApplicationStatistics(days), api.getApplicationStatisticsSettings()]);
      setData(stats); setShowCards(settings.show_app_statistics);
    } catch (err) { setError(err instanceof ApiError ? err.message : "Unable to load application statistics."); }
  }
  async function toggle() {
    const next = !showCards; setShowCards(next);
    try { await api.updateApplicationStatisticsSettings(next); }
    catch (err) { setShowCards(!next); setError(err instanceof ApiError ? err.message : "Unable to save statistics setting."); }
  }
  return <div className="stack wide app-statistics-view">
    <div className="view-head"><div><h1>App Statistics</h1><p className="muted">Authenticated portal launches and favorites. Launches indicate card activations, not destination HTTP requests.</p></div>
      <label className="stats-toggle"><input type="checkbox" checked={showCards} onChange={toggle} /> Show 7-day launch counts on app cards</label></div>
    <div className="stats-period">{[7,30,90].map(value => <button type="button" className={days===value ? "btn active" : "btn ghost"} onClick={() => setDays(value)} key={value}>{value} days</button>)}</div>
    {error && <p className="alert error">{error}</p>}
    {!data ? <p role="status">Loading application statistics...</p> : <>
      <div className="stats-kpis"><Kpi label="Launches" value={data.launches}/><Kpi label="Unique users" value={data.unique_users}/><Kpi label="Favorites" value={data.favorites}/></div>
      <section className="card stats-panel"><h2>Launch trend</h2><div className="stats-bars">{data.trend.map(point => <div key={point.date} title={`${point.date}: ${point.launches} launches`} style={{height: `${Math.max(4, data.trend.length ? (point.launches / Math.max(...data.trend.map(x=>x.launches),1))*100 : 4)}%`}} />)}</div></section>
      <section className="card stats-panel"><h2>Applications</h2>{data.applications.length === 0 ? <p className="muted">No launches recorded yet.</p> : <div className="stats-table">{data.applications.map(app => <div className="stats-row" key={app.application_id}><strong>{app.name}</strong><span>{app.launches} launches</span><span>{app.unique_users} users</span><span>★ {app.favorites}</span></div>)}</div>}</section>
    </>}
  </div>;
}
function Kpi({label,value}:{label:string;value:number}) { return <section className="card stats-kpi"><span>{label}</span><strong>{value}</strong></section>; }
