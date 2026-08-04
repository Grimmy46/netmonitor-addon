// Single-series latency trend. Baseline 0–100ms so normal traffic sits low and
// readable; the top adapts upward only if latency actually exceeds 100ms. Proper
// time axis (clean HH:MM ticks, no duplicates) and a soft filled area.
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { MetricPoint } from "../api/client";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

const fmtTime = (ms: number) =>
  new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

export function LatencyChart({ data }: { data: MetricPoint[] }) {
  if (data.length === 0) {
    return <p className="hint">No latency history yet — it fills in as the agent reports.</p>;
  }
  const accent = cssVar("--accent") || "#2a78d6";
  const grid = cssVar("--grid") || "#e1e0d9";
  const muted = cssVar("--ink-muted") || "#898781";
  const surface = cssVar("--surface-1") || "#fff";

  const rows = data.map((d) => ({ t: new Date(d.ts).getTime(), latency: d.latency_ms }));

  // Baseline top is 100ms; grow (to the next 50) only if a real spike exceeds it.
  const peak = Math.max(0, ...rows.map((r) => (r.latency == null ? 0 : r.latency)));
  const yMax = peak <= 100 ? 100 : Math.ceil(peak / 50) * 50;

  return (
    <div style={{ width: "100%", height: 190 }}>
      <ResponsiveContainer>
        <AreaChart data={rows} margin={{ top: 8, right: 10, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="latFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={accent} stopOpacity={0.22} />
              <stop offset="100%" stopColor={accent} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={grid} strokeDasharray="0" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={fmtTime}
            tick={{ fill: muted, fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: grid }}
            minTickGap={48}
          />
          <YAxis
            domain={[0, yMax]}
            tick={{ fill: muted, fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={46}
            tickFormatter={(v: number) => `${v}`}
            unit="ms"
          />
          <Tooltip
            contentStyle={{
              background: surface,
              border: `1px solid ${grid}`,
              borderRadius: 8,
              fontSize: 12,
            }}
            labelFormatter={(ms: number) => fmtTime(Number(ms))}
            formatter={(v: number) => [`${Math.round(v)} ms`, "Latency"]}
          />
          <Area
            type="monotone"
            dataKey="latency"
            stroke={accent}
            strokeWidth={1.8}
            fill="url(#latFill)"
            dot={false}
            activeDot={{ r: 3.5 }}
            isAnimationActive={false}
            connectNulls={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
