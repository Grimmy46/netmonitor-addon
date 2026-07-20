import { useEffect, useState } from "react";
import { api, type Device, type MetricPoint, type Site } from "../api/client";
import { DeviceTable } from "./DeviceTable";
import { LatencyChart } from "./LatencyChart";
import { StatTile } from "./StatTile";
import { StatusPill } from "./StatusPill";

function fmt(n: number | null, digits = 0): number | null {
  if (n === null || n === undefined) return null;
  return Number(n.toFixed(digits));
}

export function SiteCard({ site }: { site: Site }) {
  const [open, setOpen] = useState(false);
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [metrics, setMetrics] = useState<MetricPoint[] | null>(null);

  useEffect(() => {
    if (open && devices === null) {
      api.devices(site.id).then(setDevices).catch(() => setDevices([]));
      api.metrics(site.id).then(setMetrics).catch(() => setMetrics([]));
    }
  }, [open, devices, site.id]);

  return (
    <div className={`card${open ? "" : " clickable"}`} onClick={open ? undefined : () => setOpen(true)}>
      <div className="card-head">
        <div>
          <div className="name">{site.name}</div>
          {site.isp_name ? <div className="isp">{site.isp_name}</div> : null}
        </div>
        <div className="spacer" />
        <StatusPill status={site.status} />
      </div>

      <div className="tiles">
        <StatTile label="Latency" value={fmt(site.latency_ms)} unit="ms" />
        <StatTile label="Loss" value={fmt(site.packet_loss_pct, 1)} unit="%" />
        <StatTile label="Uptime" value={fmt(site.uptime_pct, 1)} unit="%" />
      </div>
      <div className="tiles" style={{ marginTop: 10 }}>
        <StatTile label="Devices" value={`${site.online_device_count}/${site.device_count}`} />
        <StatTile label="Down" value={fmt(site.download_mbps, 1)} unit="Mbps" />
        <StatTile label="Up" value={fmt(site.upload_mbps, 1)} unit="Mbps" />
      </div>

      {open ? (
        <div className="detail">
          <div style={{ fontSize: 12, color: "var(--ink-muted)", marginBottom: 6 }}>
            WAN latency
          </div>
          <LatencyChart data={metrics ?? []} />
          <div style={{ fontSize: 12, color: "var(--ink-muted)", margin: "14px 0 6px" }}>
            Devices {devices ? `(${devices.length})` : ""}
          </div>
          {devices === null ? <p className="hint">Loading…</p> : <DeviceTable devices={devices} />}
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
