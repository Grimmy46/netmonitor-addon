import { useEffect, useState } from "react";
import { api, type Agent, type MetricPoint, type PingPoint } from "../api/client";
import { LatencyChart } from "./LatencyChart";
import { StatusPill } from "./StatusPill";

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return `${Math.floor(secs)}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function AgentCard({ agent }: { agent: Agent }) {
  const [open, setOpen] = useState(false);
  const [pings, setPings] = useState<MetricPoint[] | null>(null);

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
    <div className={`card${open ? "" : " clickable"}`} onClick={open ? undefined : () => setOpen(true)}>
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

      {open ? (
        <div className="detail">
          <div style={{ fontSize: 12, color: "var(--ink-muted)", marginBottom: 6 }}>
            Ping latency {agent.version ? `· agent v${agent.version}` : ""}
            {agent.last_ip ? ` · ${agent.last_ip}` : ""}
          </div>
          {pings === null ? <p className="hint">Loading…</p> : <LatencyChart data={pings} />}
          <div style={{ marginTop: 12, textAlign: "right" }}>
            <button className="btn" onClick={(e) => { e.stopPropagation(); setOpen(false); }}>
              Collapse
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Kiosks tab: every site agent with live online/offline + latency. Click a card
 * to expand its ping-latency trend.
 */
export function AgentsView() {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .agents()
        .then((a) => alive && setAgents(a))
        .catch((e) => alive && setError(String(e instanceof Error ? e.message : e)));
    load();
    const id = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  if (error) return <div className="banner err">{error}</div>;
  if (agents === null) return <p className="hint">Loading…</p>;

  if (agents.length === 0) {
    return (
      <div className="empty">
        <p>No kiosk agents yet.</p>
        <p className="sub">
          Add one in <strong>Settings → Agents</strong>, then run the agent on a kiosk.
          It appears here within a minute.
        </p>
      </div>
    );
  }

  const online = agents.filter((a) => a.online).length;
  return (
    <>
      <p className="sub" style={{ marginBottom: 12 }}>
        {agents.length} agent{agents.length === 1 ? "" : "s"} · {online} online
      </p>
      <div className="grid">
        {agents.map((a) => (
          <AgentCard key={a.id} agent={a} />
        ))}
      </div>
    </>
  );
}
