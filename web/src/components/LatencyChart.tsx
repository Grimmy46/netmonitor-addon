// Single-series WAN latency trend. One axis, thin 2px line, recessive grid,
// crosshair tooltip. No legend (the title names the single series).
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { MetricPoint } from "../api/client";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function LatencyChart({ data }: { data: MetricPoint[] }) {
  if (data.length === 0) {
    return <p className="hint">No WAN latency history yet — it fills in as syncs run.</p>;
  }
  const accent = cssVar("--accent") || "#2a78d6";
  const grid = cssVar("--grid") || "#e1e0d9";
  const muted = cssVar("--ink-muted") || "#898781";
  const surface = cssVar("--surface-1") || "#fff";

  const rows = data.map((d) => ({
    t: new Date(d.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    latency: d.latency_ms,
  }));

  return (
    <div style={{ width: "100%", height: 180 }}>
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
          <CartesianGrid stroke={grid} strokeDasharray="0" vertical={false} />
          <XAxis dataKey="t" tick={{ fill: muted, fontSize: 11 }} tickLine={false} axisLine={{ stroke: grid }} minTickGap={32} />
          <YAxis tick={{ fill: muted, fontSize: 11 }} tickLine={false} axisLine={false} width={40} unit="ms" />
          <Tooltip
            contentStyle={{
              background: surface,
              border: `1px solid ${grid}`,
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: muted }}
            formatter={(v: number) => [`${v} ms`, "Latency"]}
          />
          <Line
            type="monotone"
            dataKey="latency"
            stroke={accent}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
            connectNulls
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
