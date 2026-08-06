import type { SparkPoint } from "../api/client";

/**
 * Always-on mini latency trend for a kiosk card. Deliberately quiet: one thin
 * accent line over a recessive baseline, red ticks where a minute saw packet
 * loss, no axes (the expanded chart carries labels). The x-domain is anchored
 * to "now", so an offline kiosk shows its line stopping short of the right
 * edge — the gap IS the signal.
 */
export function Sparkline({ points, minutes = 45 }: { points: SparkPoint[]; minutes?: number }) {
  const W = 100;
  const H = 34;
  const end = Date.now();
  const start = end - minutes * 60_000;

  const pts = (points ?? [])
    .map((p) => ({ t: Date.parse(p.ts), rtt: p.rtt, loss: p.loss }))
    .filter((p) => Number.isFinite(p.t) && p.t >= start);

  if (pts.length === 0) {
    return (
      <div className="spark spark-empty">
        <span>no recent data</span>
      </div>
    );
  }

  // Quiet baseline scale: 50ms floor so healthy 3ms traffic reads as calm,
  // growing only when real spikes need the headroom.
  const peak = Math.max(50, ...pts.map((p) => p.rtt ?? 0));
  const x = (t: number) => ((t - start) / (end - start)) * W;
  const y = (v: number) => H - 4 - (Math.min(v, peak) / peak) * (H - 9);

  let d = "";
  let started = false;
  for (const p of pts) {
    if (p.rtt == null) {
      started = false;
      continue;
    }
    d += `${started ? "L" : "M"}${x(p.t).toFixed(2)},${y(p.rtt).toFixed(2)} `;
    started = true;
  }

  return (
    <div className="spark" title={`Latency — last ${minutes} min (click for detail)`}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ width: "100%", height: 38, display: "block" }}
        aria-hidden="true"
      >
        <line
          x1={0}
          y1={H - 3}
          x2={W}
          y2={H - 3}
          stroke="var(--grid)"
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />
        <path
          d={d}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={1.6}
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {pts
          .filter((p) => p.loss)
          .map((p, i) => (
            <line
              key={i}
              x1={x(p.t)}
              x2={x(p.t)}
              y1={H - 1}
              y2={H - 7}
              stroke="var(--critical)"
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
            />
          ))}
      </svg>
    </div>
  );
}
