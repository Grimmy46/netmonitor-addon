import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, isAdmin, type Agent, type MetricPoint, type PingPoint, type SparkPoint, type TeardownStatus, type WanStatus } from "../api/client";
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
import { StatusPill } from "./StatusPill";

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

function AgentCard({
  agent,
  spark,
  open,
  onToggle,
}: {
  agent: Agent;
  spark: SparkPoint[];
  open: boolean;
  onToggle: () => void;
}) {
  const [pings, setPings] = useState<MetricPoint[] | null>(null);

  // Detailed history only loads while this card's row is expanded.
  useEffect(() => {
    if (!open) return;
    let alive = true;
    const load = () =>
      api
        .agentPings(agent.id)
        .then((ps: PingPoint[]) =>
          alive &&
          setPings(ps.map((p) => ({
            ts: p.ts,
            latency_ms: p.rtt_ms,
            packet_loss_pct: null,
            download_mbps: null,
            upload_mbps: null,
          }))),
        )
        .catch(() => alive && setPings([]));
    load();
    const id = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [open, agent.id]);

  return (
    <div className="card clickable" onClick={open ? undefined : onToggle}>
      <div className="card-head">
        <div>
          <div className="name">{agent.name}</div>
          <div className="isp">
            {agent.hostname ?? "—"}
            {agent.os ? ` · ${agent.os}` : ""}
            {agent.site_name ? ` · ${agent.site_name}` : ""}
            {` · agent ${agent.bootstrap_version ?? "—"}`}
          </div>
          {agent.printer_status ? (
            <div style={{ marginTop: 4 }}><PrinterChip agent={agent} /></div>
          ) : null}
          <PaperGauge agent={agent} />
        </div>
        <div className="spacer" />
        <StatusPill status={agent.online ? "online" : agent.last_seen_at ? "offline" : "unknown"} />
      </div>

      <div className="tiles">
        <div className="tile">
          <span className={`val${agent.latest_rtt_ms == null ? " muted" : ""}`}>
            {agent.latest_rtt_ms == null ? "—" : Math.round(agent.latest_rtt_ms)}
            {agent.latest_rtt_ms != null ? <span style={{ fontSize: 12, color: "var(--ink-muted)" }}> ms</span> : null}
          </span>
          <span className="lbl">Latency</span>
        </div>
        <div className="tile">
          <span className="val" style={{ fontSize: 15 }}>{timeAgo(agent.last_seen_at)}</span>
          <span className="lbl">Last seen</span>
        </div>
        <div className="tile">
          <span className="val" style={{ fontSize: 15 }}>{agent.last_target ?? "—"}</span>
          <span className="lbl">Target</span>
        </div>
      </div>

      {/* Always-on mini trend; the expanded chart below carries the labels. */}
      <Sparkline points={spark} />

      {open ? (
        <div className="detail">
          <div style={{ fontSize: 12, color: "var(--ink-muted)", margin: "10px 0 6px" }}>
            Ping latency · agent {agent.bootstrap_version ?? "—"}
            {agent.version ? ` · build ${agent.version}` : ""}
            {agent.last_ip ? ` · ${agent.last_ip}` : ""}
          </div>
          {pings === null ? <p className="hint">Loading…</p> : <LatencyChart data={pings} />}
          {isAdmin() ? <PrinterCheck agentId={agent.id} /> : null}
          {isAdmin() ? <PrinterTestButton agentId={agent.id} label={agent.name} /> : null}
          {isAdmin() && agent.printer_cut_count != null ? (
            <button
              className="btn"
              style={{ fontSize: 12, padding: "4px 10px", marginLeft: 8 }}
              title="Tell the tracker a fresh roll was just loaded (resets the paper gauge)"
              onClick={(e) => { e.stopPropagation(); api.markNewRoll(agent.id).catch(() => {}); }}
            >
              🧻 New roll
            </button>
          ) : null}
          {isAdmin() ? <PrinterDeep agentId={agent.id} /> : null}
          <div style={{ marginTop: 12, textAlign: "right" }}>
            <button className="btn" onClick={(e) => { e.stopPropagation(); onToggle(); }}>
              Collapse
            </button>
          </div>
        </div>
      ) : null}
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
  const [wan, setWan] = useState<WanStatus | null>(null);
  const [teardown, setTeardown] = useState<TeardownStatus | null>(null);
  const [notice, setNotice] = useState<{ notice: string | null; at: string | null } | null>(null);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [cols, setCols] = useState(1);
  const [openRows, setOpenRows] = useState<Set<number>>(new Set());
  const gridRef = useRef<HTMLDivElement | null>(null);

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

  // Track how many columns the responsive grid currently renders, so "expand
  // the row" matches what the user actually sees at this window size.
  useLayoutEffect(() => {
    const el = gridRef.current;
    if (!el) return;
    const measure = () => {
      const n = getComputedStyle(el).gridTemplateColumns.split(" ").filter(Boolean).length || 1;
      setCols((prev) => {
        if (prev !== n) setOpenRows(new Set()); // row membership changed — reset
        return n;
      });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  });

  const live = (agents ?? []).filter((a) => (a.claimed || a.last_seen_at) && a.station_group === group);
  const online = live.filter((a) => a.online).length;

  function toggleRow(row: number) {
    setOpenRows((prev) => {
      const next = new Set(prev);
      if (next.has(row)) next.delete(row);
      else next.add(row);
      return next;
    });
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
      <div className="devices-toolbar" style={{ marginBottom: 14 }}>
        <div className="panel-title" style={{ margin: 0 }}>
          {agents === null ? "Loading…" : `${live.length} ${live.length === 1 ? noun : nounPlural} · ${online} online`}
        </div>
        <div className="spacer" />
        <button
          className="btn"
          onClick={makePdf}
          disabled={pdfBusy || live.length === 0}
          title="Download a 24-hour ping report (one page per kiosk)"
        >
          {pdfBusy ? "Building PDF…" : "⤓ PDF report"}
        </button>
        <button className="btn" onClick={() => setShowPrinterLog(true)} title="View the ticket-printer status-change log">
          🖨 Printer log
        </button>
        <button className="btn" onClick={() => setShowWan(true)} title="WAN / ISP health — brownout detection + per-WAN metrics"
          style={wan?.state === "brownout" ? { borderColor: "var(--critical)", color: "var(--critical)" } : undefined}>
          🌐 WAN health
        </button>
        {isAdmin() ? (
          <button className="btn" onClick={toggleTeardown}
            title="Teardown mode — pause all fault alerts while packing up a venue"
            style={teardown?.active ? { borderColor: "var(--warn, #b7791f)", color: "var(--warn, #b7791f)" } : undefined}>
            🧰 {teardown?.active ? "Teardown ON" : "Teardown"}
          </button>
        ) : null}
        {isAdmin() ? <button className="btn" onClick={() => setManage(true)}>⚙ Manage stations</button> : null}
        {isAdmin() ? <button className="btn" onClick={() => setShowUpdate(true)} title="Upload the agent exe and stage a rollout">⬆ Agent update</button> : null}
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
        <div className="grid" ref={gridRef}>
          {live.map((a, i) => {
            const row = Math.floor(i / cols);
            return (
              <AgentCard
                key={a.id}
                agent={a}
                spark={sparks[a.id] ?? []}
                open={openRows.has(row)}
                onToggle={() => toggleRow(row)}
              />
            );
          })}
        </div>
      )}
      {panel}
    </>
  );
}
