import { useEffect, useMemo, useState } from "react";
import { api, session, type Agent, type StationOutage } from "../api/client";
import { LiveView } from "../components/LiveView";
import { MainHealth } from "../components/MainHealth";
import { applySkin } from "../lib/skin";

/**
 * RCS Station Monitor — the employee (view-only) UI. Third UI option next to
 * Pro and Holo; always uses the RCS skin. No editing, adding, deleting,
 * notification settings or device control anywhere — and the server rejects
 * every write from an employee account regardless (core/auth.current_user).
 */

type View = "overview" | "kiosk" | "ticketbox" | "history";
type Filter = "all" | "online" | "offline" | "printer";

const VIEWS: { key: View; label: string; icon: string; guestOnly?: boolean }[] = [
  { key: "overview", label: "Dashboard", icon: "📈", guestOnly: true },
  { key: "kiosk", label: "Kiosks", icon: "🖥" },
  { key: "ticketbox", label: "Ticket Boxes", icon: "🎟" },
  { key: "history", label: "Offline history", icon: "🕘" },
];

function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
function dur(sec: number): string {
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
function when(iso: string): string {
  const d = new Date(iso);
  const today = new Date().toDateString() === d.toDateString();
  const t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return today ? `Today ${t}` : `${d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })} ${t}`;
}

type St = { cls: "ok" | "bad" | "off" | "warn"; label: string };
function stationState(a: Agent): St {
  if (a.status === "online") return { cls: "ok", label: "Online" };
  if (a.status === "pending") return { cls: "off", label: "Not set up" };
  if (a.powered_off_at) return { cls: "off", label: "Shut down" };
  return { cls: "bad", label: "Offline" };
}
function printerState(a: Agent): St | null {
  switch (a.printer_status) {
    case "ok": return { cls: "ok", label: "OK" };
    case "paper_out": return { cls: "bad", label: "Paper out" };
    case "cover_open": return { cls: "warn", label: "Cover open" };
    case "error": return { cls: "bad", label: "Error" };
    default: return null;
  }
}
const printerBad = (a: Agent) => a.status === "online" && ["paper_out", "cover_open", "error"].includes(a.printer_status ?? "");

export function EmployeeApp({ onSignOut, guest = false }: { onSignOut: () => void; guest?: boolean }) {
  const views = VIEWS.filter((v) => guest || !v.guestOnly);
  const [view, setView] = useState<View>(guest ? "overview" : "kiosk");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [outages, setOutages] = useState<StationOutage[]>([]);
  const [days, setDays] = useState(7);
  const [err, setErr] = useState<string | null>(null);
  const [dark, setDark] = useState(() => localStorage.getItem("rcs-dark") === "1");

  useEffect(() => {
    applySkin("rcs");
    return () => applySkin();
  }, []);
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    try { localStorage.setItem("rcs-dark", dark ? "1" : "0"); } catch { /* private mode */ }
  }, [dark]);

  useEffect(() => {
    let alive = true;
    const load = () => {
      Promise.all([api.agents(), api.stationOutages(days)])
        .then(([a, o]) => { if (!alive) return; setAgents(a); setOutages(o); setErr(null); })
        .catch((e) => alive && setErr(String(e?.message ?? e)));
    };
    load();
    const t = window.setInterval(load, 15000);
    return () => { alive = false; window.clearInterval(t); };
  }, [days]);

  const email = guest ? "" : session.user?.email ?? "";
  const title = views.find((v) => v.key === view)?.label ?? "";

  return (
    <div className="shell">
      <aside className="side-nav">
        <div className="rcs-logo-tile"><img src="/rcs-logo.png" alt="RCS — Ray Cammack Shows" /></div>
        <div className="rcs-brand-name">RCS Station Monitor</div>
        <div className="rcs-brand-sub">Ray Cammack Shows</div>
        <nav className="side-items">
          {views.map((v) => (
            <button key={v.key} className={`side-item${view === v.key ? " active" : ""}`} onClick={() => setView(v.key)}>
              <span className="side-ico">{v.icon}</span>{v.label}
            </button>
          ))}
        </nav>
        <div className="spacer" />
        {guest ? (
          <div className="side-user"><span className="sub">Guest view · view only</span></div>
        ) : (
          <div className="side-user">
            <span className="sub" title={email}>{email}</span>
            <div style={{ marginTop: 6 }}>
              <button className="btn btn-xs" onClick={onSignOut}>⎋ Sign out</button>
            </div>
          </div>
        )}
      </aside>

      <div className="main-col">
        <header className="app-header">
          <span className="mobile-only"><img src="/rcs-logo.png" alt="RCS" style={{ height: 26, width: "auto" }} /></span>
          <h1>{title}</h1>
          <span className="rcs-viewonly" title="This account can view status only">👁 {guest ? "Guest · view only" : "View only"}</span>
          <div className="spacer" />
          <button className="rcs-icon-btn" onClick={() => setDark((d) => !d)} title={dark ? "Light theme" : "Dark theme"} aria-label="Toggle theme">
            {dark ? "☀︎" : "☾"}
          </button>
          {guest ? null : (
            <div className="rcs-acct">
              <span className="rcs-avatar">{(email[0] ?? "?").toUpperCase()}</span>
              <span className="rcs-email">{email}</span>
            </div>
          )}
        </header>

        <div className="container">
          {err ? <div className="banner err">Couldn't refresh: {err}</div> : null}
          {view === "overview" ? (
            <>
              <MainHealth />
              <LiveView />
            </>
          ) : view === "history" ? (
            <HistoryView outages={outages} days={days} setDays={setDays} />
          ) : (
            <StationsView key={view} guest={guest} group={view} agents={agents} outages={outages} onAllHistory={() => setView("history")} />
          )}
        </div>
      </div>

      <nav className="bottom-nav">
        {views.map((v) => (
          <button key={v.key} className={view === v.key ? "active" : ""} onClick={() => setView(v.key)}>
            <span className="bn-ico">{v.icon}</span><span>{v.label === "Offline history" ? "History" : v.label === "Ticket Boxes" && guest ? "TBs" : v.label}</span>
          </button>
        ))}
        {guest ? null : <button onClick={onSignOut}><span className="bn-ico">⎋</span><span>Sign out</span></button>}
      </nav>
    </div>
  );
}

function StationsView({ group, agents, outages, onAllHistory, guest = false }: {
  guest?: boolean; group: "kiosk" | "ticketbox"; agents: Agent[]; outages: StationOutage[]; onAllHistory: () => void;
}) {
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const list = useMemo(
    () => agents.filter((a) => (a.station_group ?? "kiosk") === group && a.status !== "pending")
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true })),
    [agents, group],
  );
  const lastOutage = useMemo(() => {
    const m = new Map<string, StationOutage>();
    for (const o of outages) if (!m.has(o.agent_id)) m.set(o.agent_id, o); // newest first
    return m;
  }, [outages]);
  const online = list.filter((a) => a.status === "online").length;
  const offline = list.length - online;
  const pIssues = list.filter(printerBad).length;
  const shown = list.filter((a) => {
    if (q && !`${a.name} ${a.switch_name ?? ""}`.toLowerCase().includes(q.toLowerCase())) return false;
    if (filter === "online") return a.status === "online";
    if (filter === "offline") return a.status !== "online";
    if (filter === "printer") return printerBad(a);
    return true;
  });
  const noun = group === "kiosk" ? "kiosks" : "ticket boxes";
  const recent = outages.filter((o) => o.group === group).slice(0, 8);

  const stat = (f: Filter, v: number, l: string) => (
    <button className={`rcs-stat${filter === f ? " on" : ""}`} onClick={() => setFilter(filter === f && f !== "all" ? "all" : f)}>
      <div className="v">{v}</div><div className="l">{l}</div>
    </button>
  );

  return (
    <>
      <div className="rcs-stats">
        {stat("all", list.length, `All ${noun}`)}
        {stat("online", online, "Online")}
        {stat("offline", offline, "Offline")}
        {stat("printer", pIssues, "Printer issues")}
      </div>
      <div className="rcs-toolbar">
        <input className="rcs-search" placeholder={`Search ${noun}…`} value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="rcs-select" value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
          <option value="all">All statuses</option>
          <option value="online">Online</option>
          <option value="offline">Offline</option>
          <option value="printer">Printer issues</option>
        </select>
        <span className="sub" style={{ color: "var(--ink-muted)", fontSize: 13 }}>{shown.length} shown · updates every 15s</span>
      </div>
      <div className="rcs-table-wrap">
        <table className="rcs-table">
          <thead>
            {guest ? (
              <tr><th>Station</th><th>Status</th><th>Printer</th><th>Last report</th><th>Last offline</th></tr>
            ) : (
              <tr><th>Station</th><th>Status</th><th>Printer</th><th>Last report</th><th>Paper</th><th>Location</th><th>Last offline</th></tr>
            )}
          </thead>
          <tbody>
            {shown.length === 0 ? (
              <tr><td colSpan={7} className="rcs-empty">No {noun} match.</td></tr>
            ) : shown.map((a) => {
              const st = stationState(a);
              const pr = a.status === "online" ? printerState(a) : null;
              const lo = lastOutage.get(a.id);
              return (
                <tr key={a.id}>
                  <td style={{ fontWeight: 600 }}>{a.name}</td>
                  <td><span className={`rcs-badge ${st.cls}`}>{st.label}</span></td>
                  <td>{pr ? <span className={`rcs-badge ${pr.cls}`}>{pr.label}</span> : <span className="muted">—</span>}</td>
                  <td className="muted">{ago(a.last_seen_at)}</td>
                  {guest ? null : <td className="muted">{a.printer_roll_percent != null ? `${Math.max(0, 100 - Math.round(a.printer_roll_percent))}% left` : "—"}</td>}
                  {guest ? null : <td className="muted">{a.switch_name ? `${a.switch_name.replace(/^\[([^\]]+)\].*$/, "$1")}${a.switch_port ? ` · port ${a.switch_port}` : ""}` : "—"}</td>}
                  <td className="muted">{lo ? (lo.ended_at ? `${when(lo.started_at)} · ${dur(lo.seconds)}` : `Since ${when(lo.started_at)}`) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="rcs-section-title" style={{ display: "flex", alignItems: "center" }}>
        Recent offline / back online
        <span className="spacer" style={{ flex: 1 }} />
        <button className="btn btn-xs" onClick={onAllHistory}>Full history →</button>
      </div>
      <OutageTable rows={recent} empty={`No ${noun} went offline in this period.`} />
    </>
  );
}

function OutageTable({ rows, empty }: { rows: StationOutage[]; empty: string }) {
  return (
    <div className="rcs-table-wrap">
      <table className="rcs-table">
        <thead><tr><th>Station</th><th>Went offline</th><th>Back online</th><th>Duration</th><th>Type</th></tr></thead>
        <tbody>
          {rows.length === 0 ? (
            <tr><td colSpan={5} className="rcs-empty">{empty}</td></tr>
          ) : rows.map((o, i) => (
            <tr key={`${o.agent_id}-${o.started_at}-${i}`}>
              <td style={{ fontWeight: 600 }}>{o.name}</td>
              <td className="muted">{when(o.started_at)}</td>
              <td>{o.ended_at ? <span className="muted">{when(o.ended_at)}</span> : <span className="rcs-badge bad">Still offline</span>}</td>
              <td className="muted">{dur(o.seconds)}</td>
              <td className="muted">{o.kind === "off" ? "Shut down" : "Stopped reporting"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HistoryView({ outages, days, setDays }: { outages: StationOutage[]; days: number; setDays: (d: number) => void }) {
  const [q, setQ] = useState("");
  const [grp, setGrp] = useState<"all" | "kiosk" | "ticketbox">("all");
  const rows = outages.filter((o) => (grp === "all" || o.group === grp) && (!q || o.name.toLowerCase().includes(q.toLowerCase())));
  const open = rows.filter((o) => !o.ended_at).length;
  return (
    <>
      <div className="rcs-toolbar">
        <input className="rcs-search" placeholder="Search stations…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="rcs-select" value={grp} onChange={(e) => setGrp(e.target.value as typeof grp)}>
          <option value="all">Kiosks &amp; ticket boxes</option>
          <option value="kiosk">Kiosks</option>
          <option value="ticketbox">Ticket boxes</option>
        </select>
        <select className="rcs-select" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={1}>Last 24 hours</option>
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
        </select>
        <span className="sub" style={{ color: "var(--ink-muted)", fontSize: 13 }}>
          {rows.length} event{rows.length === 1 ? "" : "s"}{open ? ` · ${open} still offline` : ""}
        </span>
      </div>
      <OutageTable rows={rows} empty="No stations went offline in this period." />
    </>
  );
}
