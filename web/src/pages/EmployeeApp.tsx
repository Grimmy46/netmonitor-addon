import { useEffect, useMemo, useState } from "react";
import { api, session, type Agent, type Closure, type StationOutage } from "../api/client";
import { GuestServices } from "../components/GuestServices";
import { LiveView } from "../components/LiveView";
import { MainHealth } from "../components/MainHealth";
import { GuestMapView } from "../components/GeoMap";
import { applySkin } from "../lib/skin";

/**
 * RCS Station Monitor — the employee (view-only) UI. Third UI option next to
 * Pro and Holo; always uses the RCS skin. No editing, adding, deleting,
 * notification settings or device control anywhere — and the server rejects
 * every write from an employee account regardless (core/auth.current_user).
 */

type View = "overview" | "map" | "kiosk" | "ticketbox" | "history";
type Filter = "all" | "online" | "offline" | "printer";

const VIEWS: { key: View; label: string; icon: string; guestOnly?: boolean }[] = [
  { key: "overview", label: "Dashboard", icon: "📈", guestOnly: true },
  { key: "map", label: "Map", icon: "🗺", guestOnly: true },
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
function stationState(a: Agent, closed = false): St {
  if (a.status === "online") return { cls: "ok", label: "Online" };
  if (a.status === "pending") return { cls: "off", label: "Not set up" };
  if (closed) return { cls: "off", label: "Closed" };
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
// Retired / test PCs that haven't reported in a month stay out of the lists.
const RETIRED_MS = 30 * 86400_000;
const isRetired = (a: Agent) => a.status !== "online" && (!a.last_seen_at || Date.now() - new Date(a.last_seen_at).getTime() > RETIRED_MS);
const fmtUntil = (iso?: string | null) => iso ? new Date(iso).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";

const printerBad = (a: Agent) => a.status === "online" && ["paper_out", "cover_open", "error"].includes(a.printer_status ?? "");

export function EmployeeApp({ onSignOut, guest = false }: { onSignOut: () => void; guest?: boolean }) {
  const views = VIEWS.filter((v) => guest || !v.guestOnly);
  const [view, setView] = useState<View>(guest ? "overview" : "kiosk");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [outages, setOutages] = useState<StationOutage[]>([]);
  const [days, setDays] = useState(7);
  const [err, setErr] = useState<string | null>(null);
  const [closure, setClosure] = useState<Closure | null>(null);
  const [dark, setDark] = useState(() => localStorage.getItem("rcs-dark") === "1");

  useEffect(() => {
    const prev = document.title;
    document.title = "RCS Station Monitor";
    return () => { document.title = prev; };
  }, []);
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
      Promise.all([api.agents(), api.stationOutages(days), api.networkOverview().catch(() => null)])
        .then(([a, o, ov]) => { if (!alive) return; setAgents(a.filter((x) => !isRetired(x))); setOutages(o); setClosure(ov?.closure ?? null); setErr(null); })
        .catch((e) => alive && setErr(String(e?.message ?? e)));
    };
    load();
    const t = window.setInterval(load, 15000);
    return () => { alive = false; window.clearInterval(t); };
  }, [days]);

  const closed = closure?.phase === "closed";
  const closedNote = closed ? (
    <div className="banner" style={{ marginBottom: 12, background: "var(--surface-2, #f1f1f1)", color: "var(--ink-secondary)", border: "1px solid var(--border)" }}>
      ⏸ <strong>Closed until {fmtUntil(closure?.end)}.</strong> Stations are switched off until we open, so grey “Closed” is expected.
    </div>
  ) : null;
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
          <div className="side-user">
            <span className="sub">Guest view · view only</span>
            <div style={{ marginTop: 6 }}>
              <button className="btn btn-xs" onClick={onSignOut}>🔑 Staff sign in</button>
            </div>
          </div>
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
              {guest ? <GuestServices closed={closed} until={closure?.end} /> : <LiveView />}
            </>
          ) : view === "map" ? (
            <GuestMapView />
          ) : view === "history" ? (
            <>{closedNote}<HistoryView outages={outages} days={days} setDays={setDays} closed={closed} /></>
          ) : (
            <>{closedNote}<StationsView key={view} guest={guest} closed={closed} group={view} agents={agents} outages={outages} onAllHistory={() => setView("history")} /></>
          )}
          {guest ? (
            <div className="mobile-only" style={{ textAlign: "center", margin: "18px 0 6px", fontSize: 12, color: "var(--ink-muted)" }}>
              RCS staff? <a href="#" onClick={(e) => { e.preventDefault(); onSignOut(); }}>Sign in</a>
            </div>
          ) : null}
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

function StationsView({ group, agents, outages, onAllHistory, guest = false, closed = false }: {
  guest?: boolean; closed?: boolean; group: "kiosk" | "ticketbox"; agents: Agent[]; outages: StationOutage[]; onAllHistory: () => void;
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

  // Kiosks fold into banks by name: K1-6001 … → "Kiosk 1".
  const [openBanks, setOpenBanks] = useState<Set<string>>(new Set());
  const toggleBank = (n: string) => setOpenBanks((s) => { const x = new Set(s); if (x.has(n)) x.delete(n); else x.add(n); return x; });
  const bankOf = (name: string) => { const m = name.match(/^K(\d+)-/i); return m ? `Kiosk ${Number(m[1])}` : "Other"; };
  const banks = (() => {
    const m = new Map<string, Agent[]>();
    for (const a of shown) { const k = bankOf(a.name); if (!m.has(k)) m.set(k, []); m.get(k)!.push(a); }
    return [...m.entries()].map(([name, list]) => ({ name, list }))
      .sort((x, y) => (x.name === "Other" ? 1 : y.name === "Other" ? -1 : x.name.localeCompare(y.name, undefined, { numeric: true })));
  })();
  const row = (a: Agent) => {
    const st = stationState(a, closed);
    const pr = a.status === "online" ? printerState(a) : null;
    const lo = lastOutage.get(a.id);
    return (
      <tr key={a.id}>
        <td style={{ fontWeight: 600, paddingLeft: group === "kiosk" ? 30 : undefined }}>{a.name}</td>
        <td><span className={`rcs-badge ${st.cls}`}>{st.label}</span></td>
        <td>{pr ? <span className={`rcs-badge ${pr.cls}`}>{pr.label}</span> : <span className="muted">—</span>}</td>
        <td className="muted">{ago(a.last_seen_at)}</td>
        {guest ? null : <td className="muted">{a.printer_roll_percent != null ? `${Math.max(0, 100 - Math.round(a.printer_roll_percent))}% left` : "—"}</td>}
        {guest ? null : <td className="muted">{a.switch_name ? `${a.switch_name.replace(/^\[([^\]]+)\].*$/, "$1")}${a.switch_port ? ` · port ${a.switch_port}` : ""}` : "—"}</td>}
        <td className="muted">{lo ? (lo.ended_at ? `${when(lo.started_at)} · ${dur(lo.seconds)}` : `Since ${when(lo.started_at)}`) : "—"}</td>
      </tr>
    );
  };
  const allOpen = banks.length > 0 && banks.every((b) => openBanks.has(b.name));

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
        {stat("offline", offline, closed ? "Closed" : "Offline")}
        {stat("printer", pIssues, "Printer issues")}
      </div>
      <div className="rcs-toolbar">
        <input className="rcs-search" placeholder={`Search ${noun}…`} value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="rcs-select" value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
          <option value="all">All statuses</option>
          <option value="online">Online</option>
          <option value="offline">{closed ? "Closed / offline" : "Offline"}</option>
          <option value="printer">Printer issues</option>
        </select>
        {group === "kiosk" && banks.length > 1 ? (
          <button className="btn btn-xs" onClick={() => setOpenBanks(allOpen ? new Set() : new Set(banks.map((b) => b.name)))}>
            {allOpen ? "Collapse all" : "Expand all"}
          </button>
        ) : null}
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
            ) : group === "kiosk" ? banks.map((b) => {
              const isOpen = q !== "" || openBanks.has(b.name);
              const up = b.list.filter((a) => a.status === "online").length;
              const bad = b.list.filter((a) => a.status !== "online" && !closed && stationState(a).cls === "bad").length;
              const pBad = b.list.filter(printerBad).length;
              return [
                <tr key={`h-${b.name}`} className="rcs-bank" onClick={() => toggleBank(b.name)} style={{ cursor: "pointer", background: "var(--surface-2, #f6f6f6)" }}>
                  <td colSpan={guest ? 5 : 7} style={{ fontWeight: 700 }}>
                    <span style={{ display: "inline-block", width: 16 }}>{isOpen ? "▾" : "▸"}</span>{b.name}
                    <span className="muted" style={{ fontWeight: 400, marginLeft: 10, fontSize: 13 }}>
                      {b.list.length} station{b.list.length === 1 ? "" : "s"} · {closed && up === 0 ? "closed" : `${up}/${b.list.length} online`}
                    </span>
                    {bad ? <span className="rcs-badge bad" style={{ marginLeft: 8 }}>{bad} offline</span> : null}
                    {pBad ? <span className="rcs-badge warn" style={{ marginLeft: 8 }}>{pBad} printer</span> : null}
                  </td>
                </tr>,
                ...(isOpen ? b.list.map(row) : []),
              ];
            }) : shown.map(row)}
          </tbody>
        </table>
      </div>

      <div className="rcs-section-title" style={{ display: "flex", alignItems: "center" }}>
        Recent offline / back online
        <span className="spacer" style={{ flex: 1 }} />
        <button className="btn btn-xs" onClick={onAllHistory}>Full history →</button>
      </div>
      <OutageTable rows={recent} closed={closed} empty={`No ${noun} went offline in this period.`} />
    </>
  );
}

function OutageTable({ rows, empty, closed = false }: { rows: StationOutage[]; empty: string; closed?: boolean }) {
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
              <td>{o.ended_at ? <span className="muted">{when(o.ended_at)}</span> : (closed ? <span className="rcs-badge off">Closed</span> : <span className="rcs-badge bad">Still offline</span>)}</td>
              <td className="muted">{dur(o.seconds)}</td>
              <td className="muted">{closed && !o.ended_at ? "Switched off (closed)" : o.kind === "off" ? "Shut down" : "Stopped reporting"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HistoryView({ outages, days, setDays, closed = false }: { outages: StationOutage[]; days: number; setDays: (d: number) => void; closed?: boolean }) {
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
          {rows.length} event{rows.length === 1 ? "" : "s"}{open ? ` · ${open} ${closed ? "off for the closure" : "still offline"}` : ""}
        </span>
      </div>
      <OutageTable rows={rows} closed={closed} empty="No stations went offline in this period." />
    </>
  );
}
