import { useEffect, useMemo, useState } from "react";
import { api, type Device, type MetricPoint, type Site } from "../api/client";
import { DeviceTable } from "../components/DeviceTable";
import { LatencyChart } from "../components/LatencyChart";
import { StatusPill } from "../components/StatusPill";

function fmt(n: number | null | undefined, digits = 0): number | null {
  if (n === null || n === undefined) return null;
  return Number(n.toFixed(digits));
}

type Filter = "all" | "offline" | "ap" | "switch" | "gateway";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "offline", label: "Offline" },
  { key: "ap", label: "Access Points" },
  { key: "switch", label: "Switches" },
  { key: "gateway", label: "Gateways" },
];

/**
 * Dedicated per-site landing page: a summary banner (device counts + WAN
 * health) followed by the WAN latency trend and a searchable device list with
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
    api.site(siteId).then((s) => alive && setSite(s)).catch((e) =>
      alive && setError(String(e instanceof Error ? e.message : e)),
    );
    api.devices(siteId).then((d) => alive && setDevices(d)).catch(() => alive && setDevices([]));
    api.metrics(siteId).then((m) => alive && setMetrics(m)).catch(() => alive && setMetrics([]));
    // Live-ish refresh of counts + statuses while the page is open.
    const id = setInterval(() => {
      api.site(siteId).then((s) => alive && setSite(s)).catch(() => {});
      api.devices(siteId).then((d) => alive && setDevices(d)).catch(() => {});
    }, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [siteId]);

  const offlineCount = useMemo(
    () => (devices ?? []).filter((d) => d.is_online === false).length,
    [devices],
  );

  const shown = useMemo(() => {
    let list = devices ?? [];
    if (filter === "offline") list = list.filter((d) => d.is_online === false);
    else if (filter !== "all") list = list.filter((d) => d.device_type === filter);
    const needle = q.trim().toLowerCase();
    if (needle) {
      list = list.filter((d) =>
        [d.name, d.model, d.ip, d.mac]
          .filter(Boolean)
          .some((v) => String(v).toLowerCase().includes(needle)),
      );
    }
    // Offline first (faults up top), then by name.
    const rank = (d: Device) => (d.is_online === false ? 0 : d.is_online === true ? 2 : 1);
    return [...list].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
  }, [devices, filter, q]);

  const total = site?.device_count ?? 0;
  const online = site?.online_device_count ?? 0;
  const offline = Math.max(0, total - online);

  return (
    <div className="site-page">
      <button className="link-back" onClick={onBack}>← All sites</button>

      {error ? <div className="banner err">{error}</div> : null}

      {/* ── Summary banner ─────────────────────────────────────────────── */}
      <div className={`site-hero ${site?.status ?? "unknown"}`}>
        <div className="hero-head">
          <div>
            <h1 className="hero-title">{site?.name ?? "…"}</h1>
            {site?.isp_name ? <div className="hero-isp">{site.isp_name}</div> : null}
          </div>
          {site ? <StatusPill status={site.status} /> : null}
        </div>

        <div className="hero-stats">
          <div className="hstat">
            <div className="hstat-val">{total}</div>
            <div className="hstat-lbl">Devices</div>
          </div>
          <div className="hstat good">
            <div className="hstat-val">{online}</div>
            <div className="hstat-lbl">Online</div>
          </div>
          <div className={`hstat${offline > 0 ? " bad" : ""}`}>
            <div className="hstat-val">{offline}</div>
            <div className="hstat-lbl">Offline</div>
          </div>
          <div className="hstat">
            <div className="hstat-val">
              {fmt(site?.latency_ms) ?? "—"}
              {fmt(site?.latency_ms) != null ? <span className="hstat-unit"> ms</span> : null}
            </div>
            <div className="hstat-lbl">WAN latency</div>
          </div>
          <div className="hstat">
            <div className="hstat-val">
              {fmt(site?.uptime_pct, 1) ?? "—"}
              {fmt(site?.uptime_pct, 1) != null ? <span className="hstat-unit"> %</span> : null}
            </div>
            <div className="hstat-lbl">Uptime</div>
          </div>
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
            Devices {devices ? `(${shown.length}${shown.length !== total ? ` of ${total}` : ""})` : ""}
            {offlineCount > 0 ? <span className="down-badge">{offlineCount} down</span> : null}
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
          {FILTERS.map((f) => (
            <button
              key={f.key}
              className={`chip ${filter === f.key ? "active" : ""}`}
              onClick={() => setFilter(f.key)}
            >
              {f.label}
              {f.key === "offline" && offlineCount > 0 ? ` (${offlineCount})` : ""}
            </button>
          ))}
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
