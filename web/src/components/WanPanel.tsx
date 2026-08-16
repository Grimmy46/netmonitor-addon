import { useEffect, useMemo, useState } from "react";
import { api, type WanIncident, type WanMetricSeries, type WanStatus } from "../api/client";

/**
 * WAN / ISP health panel. Three parts:
 *   1. Live status — is there an active internet brownout right now?
 *   2. Uplinks — per-WAN latency/loss history mirrored from UniFi (shows the
 *      dual-WAN failover picture).
 *   3. Brownout incident log — every time the internet degraded while our own
 *      LAN/gateway stayed healthy. This is the evidence trail for Spectrum.
 * Opened from the Kiosks toolbar.
 */
function fmtDur(sec: number | null): string {
  if (sec == null) return "—";
  if (sec < 90) return `${Math.round(sec)}s`;
  if (sec < 5400) return `${Math.round(sec / 60)} min`;
  return `${(sec / 3600).toFixed(1)} h`;
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

/** Tiny inline latency sparkline (nulls = gaps). */
function Spark({ pts, color }: { pts: (number | null)[]; color: string }) {
  const w = 150, h = 26, pad = 2;
  const vals = pts.filter((v): v is number => v != null);
  if (vals.length < 2) return <span className="sub" style={{ fontSize: 11 }}>—</span>;
  const max = Math.max(...vals), min = Math.min(...vals);
  const span = max - min || 1;
  const step = (w - pad * 2) / Math.max(1, pts.length - 1);
  const d = pts.map((v, i) => {
    if (v == null) return null;
    const x = pad + i * step;
    const y = pad + (h - pad * 2) * (1 - (v - min) / span);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  // Break the polyline at gaps.
  const segments: string[][] = [];
  let cur: string[] = [];
  for (const p of d) {
    if (p == null) { if (cur.length) segments.push(cur); cur = []; }
    else cur.push(p);
  }
  if (cur.length) segments.push(cur);
  return (
    <svg width={w} height={h} style={{ flexShrink: 0 }}>
      {segments.map((s, i) => (
        <polyline key={i} points={s.join(" ")} fill="none" stroke={color} strokeWidth={1.5} />
      ))}
    </svg>
  );
}

function seriesStats(s: WanMetricSeries) {
  const lat = s.points.map((p) => p.latency_ms);
  const loss = s.points.map((p) => p.packet_loss_pct).filter((v): v is number => v != null);
  const latVals = lat.filter((v): v is number => v != null);
  const lastLat = [...latVals].pop() ?? null;
  const avgLoss = loss.length ? loss.reduce((a, b) => a + b, 0) / loss.length : null;
  const maxLoss = loss.length ? Math.max(...loss) : null;
  return { lat, lastLat, avgLoss, maxLoss };
}

export function WanPanel({ onClose }: { onClose: () => void }) {
  const [status, setStatus] = useState<WanStatus | null>(null);
  const [incidents, setIncidents] = useState<WanIncident[] | null>(null);
  const [series, setSeries] = useState<WanMetricSeries[] | null>(null);
  const [siteName, setSiteName] = useState<string>("");
  const [err, setErr] = useState("");

  useEffect(() => {
    let alive = true;
    setErr("");
    api.wanStatus().then((s) => alive && setStatus(s)).catch((e) => alive && setErr(String(e)));
    api.wanIncidents(30).then((r) => alive && setIncidents(r)).catch((e) => alive && setErr(String(e)));
    // Per-WAN metrics live per-site; the fleet is one site ("Main").
    api.sites().then((sites) => {
      if (!alive) return;
      const site = sites.find((s) => s.name === "Main") ?? sites[0];
      if (!site) { setSeries([]); return; }
      setSiteName(site.name);
      api.wanMetrics(site.id).then((r) => alive && setSeries(r)).catch(() => alive && setSeries([]));
    }).catch(() => alive && setSeries([]));
    return () => { alive = false; };
  }, []);

  const summary = useMemo(() => {
    const rows = incidents ?? [];
    const total = rows.reduce((a, r) => a + (r.duration_seconds ?? 0), 0);
    const ongoing = rows.filter((r) => r.ongoing).length;
    return { count: rows.length, total, ongoing };
  }, [incidents]);

  function csv() {
    const qq = (v: string | number | null) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const text = "started,ended,ongoing,duration_s,worst_target,peak_loss_pct,peak_latency_ms,detail\n" +
      (incidents ?? []).map((r) => [
        qq(new Date(r.started_at).toISOString()), qq(r.ended_at ? new Date(r.ended_at).toISOString() : ""),
        qq(r.ongoing ? "yes" : "no"), qq(r.duration_seconds), qq(r.worst_target),
        qq(r.peak_loss_pct), qq(r.peak_latency_ms), qq(r.detail),
      ].join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `wan-incidents-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const live = status?.state === "brownout";

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" style={{ width: "min(820px, 96vw)" }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <h2 style={{ margin: 0 }}>WAN / ISP health</h2>
          <div className="spacer" style={{ flex: 1 }} />
          <button className="btn" style={{ fontSize: 12 }} onClick={csv} disabled={!incidents?.length}>⤓ CSV</button>
        </div>
        <p className="sub" style={{ marginTop: 6, marginBottom: 12, fontSize: 12 }}>
          Detects when the internet degrades (payments / DNS / kiosk) while our own
          gateway stays healthy — i.e. an upstream ISP problem, not our LAN.
        </p>

        {/* Live status */}
        <div className="banner" style={{
          marginBottom: 14, borderLeft: `3px solid ${live ? "var(--critical)" : "var(--good)"}`,
          display: "flex", alignItems: "center", gap: 10,
        }}>
          <span style={{ fontSize: 20 }}>{live ? "🌐" : "🟢"}</span>
          <span style={{ flex: 1 }}>
            {status === null ? "Checking WAN status…" : live ? (
              <>
                <strong>Live WAN brownout</strong> since {fmtTime(status.since!)} —{" "}
                {status.incident?.worst_target ?? "external targets"} degraded while the LAN is fine.
                <div className="sub" style={{ fontSize: 12 }}>{status.detail}</div>
              </>
            ) : (
              <><strong>Internet healthy</strong> — no active brownout.</>
            )}
          </span>
        </div>

        {/* Per-WAN uplinks */}
        <div className="panel-title" style={{ margin: "0 0 8px", fontSize: 13 }}>
          Uplinks{siteName ? ` · ${siteName}` : ""} <span className="sub" style={{ fontSize: 11 }}>(from UniFi, ~hourly)</span>
        </div>
        <div style={{ border: "1px solid var(--border)", borderRadius: 8, marginBottom: 16 }}>
          {series === null ? (
            <p className="hint" style={{ padding: 12 }}>Loading…</p>
          ) : series.length === 0 ? (
            <p className="hint" style={{ padding: 12 }}>
              No per-WAN metrics yet — connect the UniFi Site Manager key and run a sync.
            </p>
          ) : (
            series.map((s) => {
              const st = seriesStats(s);
              return (
                <div key={s.wan} style={{ display: "flex", alignItems: "center", gap: 12, padding: "9px 12px", borderBottom: "1px solid var(--border)" }}>
                  <span style={{ width: 150, flexShrink: 0 }}>
                    <strong style={{ fontSize: 13 }}>{s.label}</strong>
                    {s.primary ? <span className="sub" style={{ fontSize: 10, marginLeft: 6 }}>active</span> : null}
                  </span>
                  <span className="sub" style={{ fontSize: 12, width: 96, flexShrink: 0 }}>
                    {st.lastLat != null ? `${Math.round(st.lastLat)} ms` : "—"} now
                  </span>
                  <span className="sub" style={{ fontSize: 12, width: 110, flexShrink: 0, color: (st.maxLoss ?? 0) >= 5 ? "var(--critical)" : undefined }}>
                    {st.avgLoss != null ? `${st.avgLoss.toFixed(1)}% loss avg` : "—"}
                  </span>
                  <div className="spacer" style={{ flex: 1 }} />
                  <Spark pts={st.lat} color={s.primary ? "var(--accent)" : "var(--ink-muted)"} />
                </div>
              );
            })
          )}
        </div>

        {/* Incident log */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <div className="panel-title" style={{ margin: 0, fontSize: 13 }}>Brownout incidents · 30 days</div>
          <div className="spacer" style={{ flex: 1 }} />
          <span className="sub" style={{ fontSize: 12 }}>
            {incidents === null ? "…" : (
              <><strong>{summary.count}</strong> event{summary.count === 1 ? "" : "s"} · {fmtDur(summary.total)} total{summary.ongoing ? ` · ${summary.ongoing} ongoing` : ""}</>
            )}
          </span>
        </div>

        {err ? <div className="banner err" style={{ marginBottom: 10 }}>{err}</div> : null}

        <div style={{ maxHeight: 300, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
          {incidents === null ? (
            <p className="hint" style={{ padding: 14 }}>Loading…</p>
          ) : incidents.length === 0 ? (
            <p className="hint" style={{ padding: 14 }}>No WAN brownouts logged in the last 30 days. 🎉</p>
          ) : (
            incidents.map((r) => (
              <div key={r.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
                <span style={{ width: 130, flexShrink: 0, fontSize: 12 }}>{fmtTime(r.started_at)}</span>
                <span style={{ width: 74, flexShrink: 0, fontSize: 13 }}>
                  {r.ongoing ? <span style={{ color: "var(--critical)" }}>● live</span> : fmtDur(r.duration_seconds)}
                </span>
                <strong style={{ width: 130, flexShrink: 0, fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {r.worst_target ?? "—"}
                </strong>
                <span className="sub" style={{ fontSize: 12, flex: 1 }}>
                  peak {r.peak_loss_pct != null ? `${Math.round(r.peak_loss_pct)}% loss` : "—"}
                  {r.peak_latency_ms != null ? ` / ${Math.round(r.peak_latency_ms)} ms` : ""}
                </span>
              </div>
            ))
          )}
        </div>

        <div className="modal-actions" style={{ marginTop: 16 }}>
          <button className="btn" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
