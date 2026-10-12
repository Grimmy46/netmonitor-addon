import { useEffect, useState } from "react";
import { api, type ServerStats } from "../api/client";

const GB = 1024 ** 3;
const fmtBytes = (b: number | null | undefined) =>
  b == null ? "—" : b >= GB ? `${(b / GB).toFixed(1)} GB` : `${Math.round(b / 1024 ** 2)} MB`;
const fmtDur = (s: number | null | undefined) => {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
};
const level = (pct: number | null | undefined, warn: number, bad: number) =>
  pct == null ? "" : pct >= bad ? "bad" : pct >= warn ? "warn" : "good";

function Meter({ label, pct, detail, warn = 75, bad = 90 }: { label: string; pct: number | null | undefined; detail: string; warn?: number; bad?: number }) {
  const lv = level(pct, warn, bad);
  return (
    <div className="srv-meter">
      <div className="srv-meter-top">
        <span className="srv-lbl">{label}</span>
        <span className={`srv-val ${lv}`}>{pct == null ? "—" : `${Math.round(pct)}%`}</span>
      </div>
      <div className="srv-bar"><div className={`srv-fill ${lv}`} style={{ width: `${Math.min(100, Math.max(0, pct ?? 0))}%` }} /></div>
      <div className="srv-detail">{detail}</div>
    </div>
  );
}

/** 24 h line chart of one history series (0–max). */
function Trend({ data, k, label, max, unit }: { data: ServerStats["history"]; k: "cpu" | "mem" | "load"; label: string; max?: number; unit: string }) {
  const W = 300, H = 70;
  const pts = data.filter((p) => p[k] != null);
  const top = max ?? Math.max(1, ...pts.map((p) => p[k] as number)) * 1.15;
  const t1 = Date.now() / 1000;
  const t0 = pts.length ? Math.min(pts[0].t, t1 - 600) : t1 - 24 * 3600; // fill the width with what we have
  const x = (t: number) => ((t - t0) / (t1 - t0)) * W;
  const y = (v: number) => H - 2 - (Math.min(v, top) / top) * (H - 6);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p[k] as number).toFixed(1)}`).join(" ");
  const last = pts.length ? (pts[pts.length - 1][k] as number) : null;
  const first = pts.length ? pts[0].t : null;
  return (
    <div className="panel srv-trend">
      <div className="srv-meter-top">
        <span className="srv-lbl">{label}</span>
        <span className="srv-val">{last == null ? "—" : `${k === "load" ? last.toFixed(2) : Math.round(last)}${unit}`}</span>
      </div>
      {pts.length < 2 ? (
        <div className="srv-detail" style={{ padding: "18px 0" }}>Collecting — one point a minute (resets after a deploy).</div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: "100%", height: 70, display: "block" }} aria-hidden="true">
          <line x1={0} y1={H - 2} x2={W} y2={H - 2} stroke="var(--grid)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          <path d={d} fill="none" stroke="var(--accent)" strokeWidth={1.6} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        </svg>
      )}
      <div className="srv-detail">{first ? `Since ${new Date(first * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : "Last 24 hours"}</div>
    </div>
  );
}

export function ServerView() {
  const [s, setS] = useState<ServerStats | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let alive = true;
    const load = () => api.serverStats().then((d) => { if (alive) { setS(d); setErr(""); } })
      .catch((e) => alive && setErr(String(e instanceof Error ? e.message : e)));
    load();
    const id = setInterval(load, 15000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  if (err && !s) return <div className="banner err">{err}</div>;
  if (!s) return <p className="hint">Loading…</p>;

  const m = s.memory, dk = s.disk;
  const load1 = s.load[0];
  const loadPct = load1 != null && s.cpus ? (100 * load1) / s.cpus : null;
  const allOk = s.services.every((x) => x.ok);

  return (
    <div className="srv">
      {err ? <div className="banner err">Last refresh failed: {err}</div> : null}

      <div className="panel">
        <div className="srv-head">
          <span className={`pill ${allOk ? "online" : "offline"}`}><span className="dot" />{allOk ? "All services up" : "A service is down"}</span>
          <span className="sub">Server up {fmtDur(s.host_uptime_s)} · app restarted {fmtDur(s.app_uptime_s)} ago · {s.cpus ?? "?"} CPU · v{s.version}</span>
        </div>
        <div className="srv-meters">
          <Meter label="CPU" pct={s.cpu_pct} detail={`Load ${s.load.map((v) => v.toFixed(2)).join(" / ")} (1 / 5 / 15 min)`} />
          <Meter label="Memory" pct={m.used_pct} detail={`${fmtBytes(m.used)} of ${fmtBytes(m.total)} · app ${fmtBytes(s.app_memory)}${m.swap_total ? ` · swap ${fmtBytes(m.swap_used)}` : " · no swap"}`} />
          <Meter label="Disk" pct={dk.used_pct} detail={`${fmtBytes(dk.used)} of ${fmtBytes(dk.total)} · ${fmtBytes(dk.free)} free`} warn={80} bad={90} />
          <Meter label="Load vs CPUs" pct={loadPct} detail={`1-min load ${load1?.toFixed(2) ?? "—"} on ${s.cpus ?? "?"} CPU`} warn={70} bad={100} />
        </div>
      </div>

      <div className="srv-trends">
        <Trend data={s.history} k="cpu" label="CPU" max={100} unit="%" />
        <Trend data={s.history} k="mem" label="Memory" max={100} unit="%" />
        <Trend data={s.history} k="load" label="Load" unit="" />
      </div>

      <div className="srv-cols">
        <div className="panel">
          <div className="panel-title">Services</div>
          <table className="devices">
            <tbody>
              {s.services.map((x) => (
                <tr key={x.key}>
                  <td><span className={`pill ${x.ok ? "online" : "offline"}`}><span className="dot" />{x.name}</span></td>
                  <td className="mono" style={{ textAlign: "right" }}>{x.ok ? (x.ms != null ? `${x.ms} ms` : "up") : (x.error ?? `HTTP ${x.code}`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="panel">
          <div className="panel-title">Camera relay</div>
          {s.relay ? (
            <table className="devices">
              <tbody>
                <tr><td>Relay kiosk</td><td style={{ textAlign: "right" }}><span className={`pill ${s.relay.status === "online" ? "online" : "offline"}`}><span className="dot" />{s.relay.name}</span></td></tr>
                <tr><td>Asking for work</td><td style={{ textAlign: "right" }}>{s.relay.polling ? `yes · ${s.relay.last_poll_s}s ago` : s.relay.last_poll_s != null ? `no · ${fmtDur(s.relay.last_poll_s)} ago` : "not since restart"}</td></tr>
                <tr><td>Live streams now</td><td style={{ textAlign: "right" }}>{s.relay.streams_live}</td></tr>
                <tr><td>Agent version</td><td className="mono" style={{ textAlign: "right" }}>{s.relay.version ?? "—"}</td></tr>
              </tbody>
            </table>
          ) : <p className="hint">No relay kiosk set.</p>}
          <p className="hint" style={{ marginTop: 8 }}>Streams only run while someone is watching.</p>
        </div>

        <div className="panel">
          <div className="panel-title">Database · {fmtBytes(s.database.size)}</div>
          <table className="devices">
            <tbody>
              {s.database.tables.map((t) => (
                <tr key={t.name}><td className="mono">{t.name}</td><td style={{ textAlign: "right" }}>{fmtBytes(t.bytes)}</td><td className="mono" style={{ textAlign: "right" }}>{t.rows.toLocaleString()} rows</td></tr>
              ))}
            </tbody>
          </table>
          <p className="hint" style={{ marginTop: 8 }}>{s.database.connections} connections · stations {s.agents.online}/{s.agents.total} online</p>
        </div>
      </div>
    </div>
  );
}
