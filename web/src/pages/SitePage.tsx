import { useEffect, useMemo, useState } from "react";
import { api, type Device, type MetricPoint, type Site } from "../api/client";
import { DeviceTable } from "../components/DeviceTable";
import { LatencyChart } from "../components/LatencyChart";
import { StatusPill } from "../components/StatusPill";

function fmt(n: number | null | undefined, digits = 0): number | null {
  if (n === null || n === undefined) return null;
  return Number(n.toFixed(digits));
}

type Filter = "all" | "online" | "offline" | "dormant" | "ap" | "switch" | "gateway";

const CHIPS: { key: Filter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "online", label: "Online" },
  { key: "offline", label: "Offline" },
  { key: "ap", label: "Access Points" },
  { key: "switch", label: "Switches" },
  { key: "gateway", label: "Gateways" },
];

function matchesFilter(d: Device, filter: Filter): boolean {
  switch (filter) {
    case "online":
      return d.is_online === true;
    case "offline":
      return d.is_online === false && !d.dormant;
    case "dormant":
      return d.dormant;
    case "ap":
    case "switch":
    case "gateway":
      return d.device_type === filter;
    default:
      return true;
  }
}

/**
 * Dedicated per-site landing page: a clickable summary banner (each stat filters
 * the list below), the WAN latency trend, and a searchable device list with
 * offline devices sorted to the top so faults are the first thing you see.
 */
export function SitePage({ siteId, onBack }: { siteId: string; onBack: () => void }) {
  const [site, setSite] = useState<Site | null>(null);
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [metrics, setMetrics] = useState<MetricPoint[]>([]);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    let alive = true;
    setSite(null);
    setDevices(null);
    setMetrics([]);
    setError("");
    setFilter("all");
    api.site(siteId).then((s) => alive && setSite(s)).catch((e) =>
      alive && setError(String(e instanceof Error ? e.message : e)),
    );
    // Fetch every device (incl. dormant) so all the banner filters work client-side.
    api.devices(siteId, "all").then((d) => alive && setDevices(d)).catch(() => alive && setDevices([]));
    api.metrics(siteId).then((m) => alive && setMetrics(m)).catch(() => alive && setMetrics([]));
    const id = setInterval(() => {
      api.site(siteId).then((s) => alive && setSite(s)).catch(() => {});
      api.devices(siteId, "all").then((d) => alive && setDevices(d)).catch(() => {});
    }, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [siteId]);

  // Counts derived from the fetched devices so the banner and the list always agree.
  const counts = useMemo(() => {
    const ds = devices ?? [];
    return {
      total: ds.length,
      online: ds.filter((d) => d.is_online === true).length,
      offline: ds.filter((d) => d.is_online === false && !d.dormant).length,
      dormant: ds.filter((d) => d.dormant).length,
    };
  }, [devices]);

  const shown = useMemo(() => {
    let list = (devices ?? []).filter((d) => matchesFilter(d, filter));
    const needle = q.trim().toLowerCase();
    if (needle) {
      list = list.filter((d) =>
        [d.name, d.model, d.ip, d.mac]
          .filter(Boolean)
          .some((v) => String(v).toLowerCase().includes(needle)),
      );
    }
    // Ordering: a fresh fault jumps to the very top so it's the first thing you
    // see — a device that just went offline (down < 12h), or one that's up in
    // UniFi but not answering a LAN ping ("unreachable"). Then all online
    // devices; then anything offline 12h+ (or dormant) drops below the list.
    const RECENT_DOWN = 12 * 3600; // seconds
    const rank = (d: Device) => {
      if (d.is_online === false) {
        return !d.dormant && (d.down_seconds ?? 0) < RECENT_DOWN ? 0 : 3;
      }
      if (d.is_online === true) {
        return d.local_reachable === false ? 0 : 1; // unreachable is a fault → top
      }
      return 2; // unknown, just below online
    };
    return [...list].sort((a, b) => {
      const ra = rank(a);
      const rb = rank(b);
      if (ra !== rb) return ra - rb;
      // Within "just went down", newest fault first; otherwise alphabetical.
      if (ra === 0) return (a.down_seconds ?? 0) - (b.down_seconds ?? 0);
      return a.name.localeCompare(b.name);
    });
  }, [devices, filter, q]);

  // A banner card: big number + label that also filters the list when clicked.
  function StatCard({
    value,
    label,
    to,
    tone,
    unit,
  }: {
    value: number | string | null;
    label: string;
    to?: Filter;
    tone?: "good" | "bad";
    unit?: string;
  }) {
    const clickable = to !== undefined;
    const active = clickable && filter === to;
    return (
      <button
        type="button"
        className={`hstat${tone ? " " + tone : ""}${clickable ? " clickable" : " static"}${active ? " active" : ""}`}
        onClick={clickable ? () => setFilter(to!) : undefined}
        aria-pressed={active}
      >
        <div className="hstat-val">
          {value ?? "—"}
          {value != null && unit ? <span className="hstat-unit"> {unit}</span> : null}
        </div>
        <div className="hstat-lbl">{label}</div>
      </button>
    );
  }

  return (
    <div className="site-page">
      <button className="link-back" onClick={onBack}>← All sites</button>

      {error ? <div className="banner err">{error}</div> : null}

      {/* ── Summary banner (each stat filters the list) ─────────────────── */}
      <div className={`site-hero ${site?.status ?? "unknown"}`}>
        <div className="hero-head">
          <div>
            <h1 className="hero-title">{site?.name ?? "…"}</h1>
            {site?.isp_name ? <div className="hero-isp">{site.isp_name}</div> : null}
          </div>
          {site ? <StatusPill status={site.status} /> : null}
        </div>

        <div className="hero-stats">
          <StatCard value={counts.total} label="Devices" to="all" />
          <StatCard value={counts.online} label="Online" to="online" tone="good" />
          <StatCard value={counts.offline} label="Offline" to="offline" tone={counts.offline > 0 ? "bad" : undefined} />
          {counts.dormant > 0 ? (
            <StatCard value={counts.dormant} label="Dormant" to="dormant" />
          ) : null}
          <StatCard value={fmt(site?.latency_ms)} label="WAN latency" unit="ms" />
          <StatCard value={fmt(site?.uptime_pct, 1)} label="Uptime" unit="%" />
        </div>
      </div>

      {/* ── WAN latency trend ──────────────────────────────────────────── */}
      <section className="panel">
        <div className="panel-title">WAN latency</div>
        <LatencyChart data={metrics} />
      </section>

      {/* ── Devices ────────────────────────────────────────────────────── */}
      <section className="panel">
        <div className="devices-toolbar">
          <div className="panel-title" style={{ margin: 0 }}>
            Devices {devices ? `(${shown.length}${shown.length !== counts.total ? ` of ${counts.total}` : ""})` : ""}
            {counts.offline > 0 ? <span className="down-badge">{counts.offline} down</span> : null}
          </div>
          <div className="spacer" />
          <input
            className="search"
            type="text"
            placeholder="Search name, model, IP, MAC…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <div className="filter-chips">
          {CHIPS.map((f) => (
            <button
              key={f.key}
              className={`chip ${filter === f.key ? "active" : ""}`}
              onClick={() => setFilter(f.key)}
            >
              {f.label}
              {f.key === "offline" && counts.offline > 0 ? ` (${counts.offline})` : ""}
            </button>
          ))}
          {counts.dormant > 0 ? (
            <button
              className={`chip ${filter === "dormant" ? "active" : ""}`}
              onClick={() => setFilter("dormant")}
            >
              Dormant ({counts.dormant})
            </button>
          ) : null}
        </div>
        {devices === null ? (
          <p className="hint">Loading devices…</p>
        ) : (
          <DeviceTable devices={shown} />
        )}
      </section>
    </div>
  );
}
