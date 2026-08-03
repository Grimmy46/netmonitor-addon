import type { Site } from "../api/client";
import { StatTile } from "./StatTile";
import { StatusPill } from "./StatusPill";

function fmt(n: number | null, digits = 0): number | null {
  if (n === null || n === undefined) return null;
  return Number(n.toFixed(digits));
}

/**
 * Compact fleet card. Clicking (or Cmd/middle-clicking) opens the site's own
 * page at #/site/<id> — a real URL, so it can be opened in a new tab.
 */
export function SiteCard({ site }: { site: Site }) {
  // "Down" = offline and still actionable (dormant gear is excluded).
  const activeDown = Math.max(
    0,
    site.device_count - site.online_device_count - site.dormant_device_count,
  );
  return (
    <a className="card card-link" href={`#/site/${site.id}`}>
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
        <StatTile label="Down" value={activeDown} />
        <StatTile
          label="Dormant"
          value={site.dormant_device_count > 0 ? site.dormant_device_count : "0"}
        />
      </div>
    </a>
  );
}
