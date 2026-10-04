import { useEffect, useState } from "react";
import { API_BASE, api, isAdmin, type Agent, type MetricPoint, type PingPoint, type SparkPoint, type TeardownStatus, type WanStatus } from "../api/client";
import { PrinterCheck } from "./PrinterCheck";
import { PrinterDeep } from "./PrinterDeep";
import { PrinterTestButton } from "./PrinterTestButton";
import { downloadKioskReport } from "../lib/kioskReport";
import { LatencyChart } from "./LatencyChart";
import { Sparkline } from "./Sparkline";
import { StationsPanel } from "./StationsPanel";
import { AgentUpdatePanel } from "./AgentUpdatePanel";
import { PrinterLogPanel } from "./PrinterLogPanel";
import { WanPanel } from "./WanPanel";
import { TeardownPlanner } from "./TeardownPlanner";
import { StatusPill } from "./StatusPill";
import { FleetActions } from "./FleetActions";

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return `${Math.floor(secs)}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

const PRINTER_CHIP: Record<string, { label: string; color: string; bg: string }> = {
  ok: { label: "🖨️ Paper OK", color: "var(--good)", bg: "rgba(74,222,128,0.12)" },
  paper_out: { label: "🧻 Paper OUT", color: "var(--critical)", bg: "rgba(248,113,113,0.14)" },
  cover_open: { label: "🔧 Cover open", color: "var(--critical)", bg: "rgba(248,113,113,0.14)" },
  error: { label: "⚠️ Printer error", color: "var(--critical)", bg: "rgba(248,113,113,0.14)" },
  unknown: { label: "🖨️ No reply", color: "var(--ink-muted)", bg: "rgba(127,127,127,0.10)" },
};

function PrinterChip({ agent }: { agent: Agent }) {
  if (!agent.printer_status) return null;
  const c = PRINTER_CHIP[agent.printer_status] ?? PRINTER_CHIP.unknown;
  return (
    <span
      title={agent.printer_detail ?? undefined}
      style={{
        display: "inline-block", fontSize: 11, fontWeight: 600, lineHeight: 1.6,
        padding: "1px 8px", borderRadius: 999, color: c.color, background: c.bg,
      }}
    >
      {c.label}
    </span>
  );
}

function PaperGauge({ agent }: { agent: Agent }) {
  const pct = agent.printer_roll_percent;
  if (pct == null) return null;
  const left = agent.printer_cuts_remaining;
  const color = pct >= 85 ? "var(--critical)" : pct >= 70 ? "var(--warn, #b7791f)" : "var(--good)";
  const basis = agent.printer_roll_learned ? "learned roll size" : "estimate — learning";
  const tip = `${Math.round(pct)}% of the roll used${left != null ? ` · ~${left} tickets left` : ""} · ${basis}`;
  return (
    <div style={{ marginTop: 6, maxWidth: 240 }} title={tip}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--ink-muted)" }}>
        <span>🧻 Paper {Math.round(pct)}% used{agent.printer_roll_partial ? " ~" : ""}</span>
        {left != null ? <span>~{left} left</span> : null}
      </div>
      <div style={{ height: 5, borderRadius: 999, background: "rgba(127,127,127,0.18)", marginTop: 2, overflow: "hidden" }}>
        <div style={{ width: `${Math.min(100, Math.max(2, pct))}%`, height: "100%", background: color }} />
      </div>
    </div>
  );
}

type Filter = "all" | "online" | "offline" | "printer" | "off" | "stale";

function printerBad(a: Agent) {
  return a.printer_status === "paper_out" || a.printer_status === "cover_open" || a.printer_status === "error";
}
function stateOf(a: Agent): "online" | "off" | "offline" | "stale" {
  if (a.online) return "online";
  if (a.stale) return "stale";
  if (a.powered_off_at) return "off";
  return "offline";
}
const STATE_LABEL = { online: "Online", off: "Shut down", offline: "Offline", stale: "Stale" };

/** Compact kiosk tile — one glance: status, printer, paper, latency, port. */
function KioskTile({ agent, spark, onOpen }: { agent: Agent; spark: SparkPoint[]; onOpen: () => void }) {
  const st = stateOf(agent);
  return (
    <button className={`ktile ktile-${st}${printerBad(agent) ? " ktile-printer" : ""}`} onClick={onOpen}>
      <div className="ktile-top">
        <span className={`kdot kdot-${st}`} />
        <span className="ktile-name">{agent.name}</span>
        <span className="spacer" />
        <span className="ktile-rtt">
          {agent.online && agent.latest_rtt_ms != null ? `${Math.round(agent.latest_rtt_ms)} ms` : st === "online" ? "—" : timeAgo(agent.last_seen_at)}
        </span>
      </div>
      <div className="ktile-sub">
        {agent.switch_port != null ? <span>port {agent.switch_port}</span> : null}
        {agent.printer_status && agent.online ? <PrinterChip agent={agent} /> : null}
        {st !== "online" ? <span className="ktile-state">{STATE_LABEL[st]}</span> : null}
      </div>
      <PaperGauge agent={agent} />
      <div className="ktile-spark"><Sparkline points={spark} height={34} /></div>
    </button>
  );
}

/** Full detail for one kiosk — a right-side drawer on desktop, a sheet on phones. */
function KioskSheet({ agent, onClose }: { agent: Agent; onClose: () => void }) {
  const [pings, setPings] = useState<MetricPoint[] | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      api.agentPings(agent.id)
        .then((ps: PingPoint[]) => alive && setPings(ps.map((p) => ({
          ts: p.ts, latency_ms: p.rtt_ms, packet_loss_pct: null, download_mbps: null, upload_mbps: null,
        }))))
        .catch(() => alive && setPings([]));
    load();
    const id = setInterval(load, 15000);
    return () => { alive = false; clearInterval(id); };
  }, [agent.id]);
  const st = stateOf(agent);
  return (
    <div className="overlay sheet-overlay" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <span className={`kdot kdot-${st}`} />
          <h2>{agent.name}</h2>
          <StatusPill status={agent.online ? "online" : agent.last_seen_at ? "offline" : "unknown"} />
          <span className="spacer" />
          <button className="btn" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="kv">
          <div><span>Last seen</span><b>{timeAgo(agent.last_seen_at)}</b></div>
          <div><span>Latency</span><b>{agent.latest_rtt_ms == null ? "—" : `${Math.round(agent.latest_rtt_ms)} ms`}</b></div>
          <div><span>Switch</span><b>{agent.switch_name ?? "—"}{agent.switch_port != null ? ` · port ${agent.switch_port}` : ""}</b></div>
          <div><span>LAN IP</span><b>{agent.lan_ip ?? "—"}</b></div>
          <div><span>Hostname</span><b>{agent.hostname ?? "—"}</b></div>
          <div><span>Agent</span><b>{agent.bootstrap_version ?? "—"}{agent.version ? ` · ${agent.version}` : ""}</b></div>
          {agent.powered_off_at ? <div><span>Shut down</span><b>{new Date(agent.powered_off_at).toLocaleString()}</b></div> : null}
        </div>
        {agent.printer_status ? (
          <div style={{ margin: "10px 0" }}>
            <PrinterChip agent={agent} />
            <span className="sub" style={{ fontSize: 12, marginLeft: 8 }}>{agent.printer_detail}</span>
            <PaperGauge agent={agent} />
          </div>
        ) : null}
        <div className="sub" style={{ fontSize: 12, margin: "12px 0 6px" }}>Ping latency</div>
        {pings === null ? <p className="hint">Loading…</p> : <LatencyChart data={pings} />}
        {isAdmin() ? (
          <div className="sheet-tools">
            <PrinterCheck agentId={agent.id} />
            <PrinterTestButton agentId={agent.id} label={agent.name} />
            {agent.printer_cut_count != null ? (
              <button className="btn" style={{ fontSize: 12, padding: "4px 10px", marginLeft: 8 }}
                title="Tell the tracker a fresh roll was just loaded (resets the paper gauge)"
                onClick={() => { api.markNewRoll(agent.id).catch(() => {}); }}>
                🧻 New roll
              </button>
            ) : null}
            <PrinterDeep agentId={agent.id} />
          </div>
        ) : null}
      </div>
    </div>
  );
}

/**
 * Kiosks tab: every site agent as a card with a live sparkline. Clicking a card
 * expands its whole VISUAL ROW — every kiosk in that row shows its detailed,
 * labeled chart together (no more one tall card dragging empty neighbors).
 */
export function AgentsView({ group = "kiosk" }: { group?: "kiosk" | "ticketbox" }) {
  const noun = group === "ticketbox" ? "ticket box" : "kiosk";
  const nounPlural = group === "ticketbox" ? "ticket boxes" : "kiosks";
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [sparks, setSparks] = useState<Record<string, SparkPoint[]>>({});
  const [error, setError] = useState("");
  const [manage, setManage] = useState(false);
  const [showUpdate, setShowUpdate] = useState(false);
  const [showPrinterLog, setShowPrinterLog] = useState(false);
  const [showWan, setShowWan] = useState(false);
  const [showTeardown, setShowTeardown] = useState(false);
  const [wan, setWan] = useState<WanStatus | null>(null);
  const [teardown, setTeardown] = useState<TeardownStatus | null>(null);
  const [notice, setNotice] = useState<{ notice: string | null; at: string | null } | null>(null);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const [openId, setOpenId] = useState<string | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);

  const load = () =>
    api.agents().then(setAgents).catch((e) => setError(String(e instanceof Error ? e.message : e)));

  useEffect(() => {
    let alive = true;
    const tick = () => {
      api.agents().then((a) => alive && setAgents(a)).catch(() => {});
      api.agentSparklines().then((s) => alive && setSparks(s)).catch(() => {});
      api.getNotice().then((n) => alive && setNotice(n.notice ? n : null)).catch(() => {});
      api.wanStatus().then((w) => alive && setWan(w)).catch(() => {});
      api.getTeardown().then((t) => alive && setTeardown(t)).catch(() => {});
    };
    tick();
    const id = setInterval(tick, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const live = (agents ?? []).filter((a) => (a.claimed || a.last_seen_at) && a.station_group === group);
  const online = live.filter((a) => a.online).length;

  const counts: Record<Filter, number> = {
    all: live.filter((a) => !a.stale).length,
    online,
    offline: live.filter((a) => stateOf(a) === "offline").length,
    printer: live.filter((a) => a.online && printerBad(a)).length,
    off: live.filter((a) => stateOf(a) === "off").length,
    stale: live.filter((a) => a.stale).length,
  };
  const shown = live.filter((a) =>
    filter === "all" ? !a.stale :
    filter === "online" ? a.online :
    filter === "printer" ? a.online && printerBad(a) :
    stateOf(a) === filter);
  // Group by the switch each kiosk is plugged into (from UniFi), so a dead
  // bank reads as one problem: "Kiosk 1 switch · 0/8 online".
  const groups = (() => {
    const m = new Map<string, Agent[]>();
    for (const a of shown) {
      const k = a.switch_name || "Not located yet";
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(a);
    }
    const out = [...m.entries()].map(([name, list]) => ({
      name, list: list.sort((x, y) => x.name.localeCompare(y.name, undefined, { numeric: true, sensitivity: "base" })),
    }));
    // Order banks by the kiosks in them (K1-… first), not by the switch's
    // name — switch names start with brackets / asset tags in any order.
    const cmp = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
    const first = (l: Agent[]) => l.map((a) => a.name).sort(cmp)[0] ?? "";
    out.sort((x, y) => (x.name === "Not located yet" ? 1 : y.name === "Not located yet" ? -1 : cmp(first(x.list), first(y.list))));
    return out;
  })();
  const openAgent = live.find((a) => a.id === openId) ?? null;
  async function retireStale() {
    const stale = live.filter((a) => a.stale);
    if (!stale.length) return;
    if (!window.confirm(`Remove ${stale.length} stale station${stale.length === 1 ? "" : "s"} (no check-in for 7+ days)?\n\n${stale.map((a) => a.name).join(", ")}\n\nA kiosk that comes back can simply re-enroll.`)) return;
    for (const a of stale) await api.deleteAgent(a.id).catch(() => {});
    setFilter("all");
    load();
  }

  async function makePdf() {
    if (!live.length || pdfBusy) return;
    setPdfBusy(true);
    setError("");
    try {
      await downloadKioskReport(live, 24);
    } catch (e) {
      setError(`PDF failed: ${String(e instanceof Error ? e.message : e)}`);
    } finally {
      setPdfBusy(false);
    }
  }


  const panel = manage ? (
    <StationsPanel onClose={() => setManage(false)} onChanged={load} />
  ) : showUpdate ? (
    <AgentUpdatePanel onClose={() => setShowUpdate(false)} onChanged={load} />
  ) : showPrinterLog ? (
    <PrinterLogPanel onClose={() => setShowPrinterLog(false)} />
  ) : showWan ? (
    <WanPanel onClose={() => setShowWan(false)} />
  ) : showTeardown ? (
    <TeardownPlanner onClose={() => setShowTeardown(false)} />
  ) : null;

  const toggleTeardown = () => {
    const next = !(teardown?.active);
    if (next && !window.confirm("Start teardown mode? All fault alerts pause while you pack up (auto-off in 18h).")) return;
    api.setTeardown(next).then(setTeardown).catch(() => {});
  };

  return (
    <>
      {teardown?.active ? (
        <div className="banner" style={{ marginBottom: 14, display: "flex", alignItems: "center", gap: 10, borderLeft: "3px solid var(--warn, #b7791f)" }}>
          <span style={{ fontSize: 18 }}>🧰</span>
          <span style={{ flex: 1 }}>
            <strong>Teardown mode — fault alerts paused.</strong>{" "}
            <span className="sub" style={{ fontSize: 12 }}>
              Packing up: {teardown.offline}/{teardown.total} offline
              {teardown.since ? ` · since ${new Date(teardown.since).toLocaleString(undefined, { hour: "numeric", minute: "2-digit" })}` : ""}
              {teardown.auto_off_at ? ` · auto-off ${new Date(teardown.auto_off_at).toLocaleString(undefined, { hour: "numeric", minute: "2-digit" })}` : ""}
            </span>
          </span>
          {isAdmin() ? (
            <button className="btn" style={{ fontSize: 12, padding: "3px 10px" }} onClick={toggleTeardown}>End teardown</button>
          ) : null}
        </div>
      ) : null}
      {wan?.state === "brownout" ? (
        <div className="banner" style={{ marginBottom: 14, display: "flex", alignItems: "center", gap: 10, borderLeft: "3px solid var(--critical)", cursor: "pointer" }}
          onClick={() => setShowWan(true)} title="Open WAN / ISP health">
          <span style={{ fontSize: 18 }}>🌐</span>
          <span style={{ flex: 1 }}>
            <strong>WAN brownout in progress</strong> — the internet is degraded ({wan.incident?.worst_target ?? "external targets"}) while the LAN is healthy. Likely an ISP/Spectrum issue.
            {wan.since ? <span className="sub" style={{ fontSize: 12 }}> · since {new Date(wan.since).toLocaleString(undefined, { hour: "numeric", minute: "2-digit" })}</span> : null}
          </span>
          <span className="sub" style={{ fontSize: 12 }}>View →</span>
        </div>
      ) : null}
      {notice?.notice ? (
        <div className="banner" style={{ marginBottom: 14, display: "flex", alignItems: "center", gap: 10, borderLeft: "3px solid var(--accent)" }}>
          <span style={{ flex: 1 }}>
            {notice.notice}
            {notice.at ? <span className="sub" style={{ fontSize: 12 }}> · {new Date(notice.at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</span> : null}
          </span>
          {isAdmin() ? (
            <button className="btn" style={{ fontSize: 12, padding: "3px 10px" }}
              onClick={() => { api.dismissNotice().then(() => setNotice(null)).catch(() => {}); }}>
              Dismiss
            </button>
          ) : null}
        </div>
      ) : null}
      {group === "kiosk" && live.length ? <FleetActions agents={live} onChanged={load} /> : null}
      <div className="kiosk-toolbar">
        <div className="filter-chips">
          {([
            ["all", "All"], ["online", "Online"], ["offline", "Offline"], ["printer", "Printer issues"],
            ["off", "Shut down"], ["stale", "Stale"],
          ] as [Filter, string][]).filter(([k]) => k === "all" || k === "online" || counts[k] > 0).map(([k, l]) => (
            <button key={k} className={`chip ${filter === k ? "active" : ""}${k === "printer" || k === "offline" ? " chip-warn" : ""}`}
              onClick={() => setFilter(k)}>
              {l} <b>{counts[k]}</b>
            </button>
          ))}
        </div>
        <div className="spacer" />
        {filter === "stale" && isAdmin() ? (
          <button className="btn" onClick={retireStale}>🗑 Retire {counts.stale} stale</button>
        ) : null}
        <div className="more-wrap">
          <button className="btn" onClick={() => setMoreOpen(!moreOpen)} aria-expanded={moreOpen}>More ▾</button>
          {moreOpen ? (
            <div className="more-menu" onClick={() => setMoreOpen(false)}>
              <button onClick={makePdf} disabled={pdfBusy || live.length === 0}>{pdfBusy ? "Building PDF…" : "⤓ 24 h PDF report"}</button>
              <button onClick={() => setShowPrinterLog(true)}>🖨 Printer log</button>
              <button onClick={() => setShowWan(true)}>🌐 WAN health</button>
              {isAdmin() ? <button onClick={toggleTeardown}>🧰 {teardown?.active ? "End teardown" : "Teardown mode"}</button> : null}
              {isAdmin() ? <button onClick={() => setShowTeardown(true)}>🗓 Teardown planner</button> : null}
              {isAdmin() ? <button onClick={() => setManage(true)}>⚙ Manage stations</button> : null}
              {isAdmin() ? <button onClick={() => setShowUpdate(true)}>⬆ Agent update</button> : null}
              {isAdmin() ? <button onClick={() => { window.location.href = `${API_BASE}/agents/install-kit`; }}>⤓ Install kit (new kiosk)</button> : null}
            </div>
          ) : null}
        </div>
      </div>
      {error ? <div className="banner err">{error}</div> : null}

      {agents !== null && live.length === 0 ? (
        <div className="empty">
          <p>No {nounPlural} reporting yet.</p>
          <p className="sub">
            Add stations under <strong>Manage stations</strong> (set their group to
            &ldquo;{noun}&rdquo;), then run the agent on the machine — it appears here
            within a minute.
          </p>
        </div>
      ) : (
        <div className="kgroups">
          {groups.map((g) => {
            const up = g.list.filter((a) => a.online).length;
            const dead = up === 0 && g.list.length > 1;
            return (
              <section key={g.name} className={`kgroup${dead ? " kgroup-dead" : ""}`}>
                <div className="kgroup-head">
                  <span className="kgroup-name" title={g.name}>{g.name.replace(/\s+US[WLX]\b.*$/, "")}</span>
                  <span className={`kgroup-count${up < g.list.length ? " warn" : ""}`}>{up}/{g.list.length} online</span>
                  {dead ? <span className="kgroup-hint">whole bank silent — check this switch / its power</span> : null}
                </div>
                <div className="ktiles">
                  {g.list.map((a) => (
                    <KioskTile key={a.id} agent={a} spark={sparks[a.id] ?? []} onOpen={() => setOpenId(a.id)} />
                  ))}
                </div>
              </section>
            );
          })}
          {!shown.length && agents !== null ? <p className="hint">Nothing matches this filter.</p> : null}
        </div>
      )}
      {openAgent ? <KioskSheet agent={openAgent} onClose={() => setOpenId(null)} /> : null}
      {panel}
    </>
  );
}
