import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, isAdmin, type Agent, type MetricPoint, type PingPoint, type SparkPoint } from "../api/client";
import { PrinterCheck } from "./PrinterCheck";
import { downloadKioskReport } from "../lib/kioskReport";
import { LatencyChart } from "./LatencyChart";
import { Sparkline } from "./Sparkline";
import { StationsPanel } from "./StationsPanel";
import { StatusPill } from "./StatusPill";

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return `${Math.floor(secs)}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
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
          </div>
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
            Ping latency {agent.version ? `· agent v${agent.version}` : ""}
            {agent.last_ip ? ` · ${agent.last_ip}` : ""}
          </div>
          {pings === null ? <p className="hint">Loading…</p> : <LatencyChart data={pings} />}
          {isAdmin() ? <PrinterCheck agentId={agent.id} /> : null}
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
  ) : null;

  return (
    <>
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
        {isAdmin() ? <button className="btn" onClick={() => setManage(true)}>⚙ Manage stations</button> : null}
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
