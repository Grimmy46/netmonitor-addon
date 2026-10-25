import { useEffect, useState } from "react";
import { api, type IpIssues, type SiteTopology, type TopoNode } from "../api/client";
import { splitName } from "../lib/names";

/**
 * "Link & IP health" — the things that look fine at a glance but aren't:
 *  - degraded links: device is up, but the cable feeding it is slow / erroring /
 *    dropping pings (the Ticket Box 1 case)
 *  - IP problems: live duplicate IPs, self-assigned 169.254 clients, wired
 *    clients with no IP (checked every 10 min)
 */
export default function HealthPanel({ siteId }: { siteId: string }) {
  const [topo, setTopo] = useState<SiteTopology | null>(null);
  useEffect(() => {
    let live = true;
    const load = () => api.siteTopology(siteId).then((t) => { if (live) setTopo(t); }).catch(() => {});
    load();
    const id = window.setInterval(load, 30000);
    return () => { live = false; window.clearInterval(id); };
  }, [siteId]);
  if (!topo) return null;
  const byId = new Map(topo.nodes.map((n) => [n.id, n] as const));
  const degraded = topo.nodes.filter((n) => !n.dormant && !n.closed_down && n.type !== "gateway" &&
    (n.link_status === "degraded" || (n.is_online === false && n.seen_age_s != null && n.seen_age_s <= 90)));
  const ip: IpIssues | null = topo.ip_issues ?? null;
  const ipAt = topo.ip_issues_at ?? null;
  const label = (n?: TopoNode) => { if (!n) return ""; const s = splitName(n.name); return s.tag ? `${s.label} #${s.tag}` : s.label; };
  const noIp = [...(ip?.no_ip ?? []), ...(ip?.apipa ?? []).map((a) => ({ ...a, network: "", sw_port: null, minutes: 0 }))];
  return (
    <section className="panel ipchk">
      <div className="panel-title">Link &amp; IP health</div>
      <div className="ipchk-row">
        {degraded.length === 0 ? <span className="ipchk-ok">✓ No degraded links</span>
          : <span className="ipchk-warn">⚠ {degraded.length} degraded link{degraded.length > 1 ? "s" : ""}</span>}
        <span className="muted">— up and powered, but the cable feeding it is slow, erroring or dropping pings</span>
      </div>
      {degraded.map((n) => {
        const p = n.parent_id ? byId.get(n.parent_id) : undefined;
        return (
          <div key={n.id} className="ipchk-row">
            <b>{label(n)}</b>
            {p ? <span className="muted">fed from {label(p)}{n.uplink_port ? `, port ${n.uplink_port}` : ""}</span> : null}
            <span className="ipchk-warn">{n.link_reason ?? "up in UniFi, but not answering kiosk pings"}</span>
          </div>
        );
      })}
      <div className="ipchk-row">
        {!ip ? <span className="muted">IP check: waiting for the first run…</span>
          : ip.conflicts.length === 0 ? <span className="ipchk-ok">✓ No IP conflicts</span>
          : <span className="ipchk-bad">⚠ {ip.conflicts.length} IP conflict{ip.conflicts.length > 1 ? "s" : ""}</span>}
        {ip ? <span className="muted">— {ip.clients} clients, {ip.devices} devices checked{ipAt ? ` · ${new Date(ipAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : ""}</span> : null}
      </div>
      {(ip?.conflicts ?? []).map((c) => (
        <div key={c.ip} className="ipchk-row">
          <b>{c.ip}</b>
          <span className="ipchk-bad">{c.entries.map((e) => `${e.name} (${e.mac})`).join("  vs  ")}</span>
        </div>
      ))}
      {noIp.length ? (
        <div className="ipchk-row">
          <span className="ipchk-warn">⚠ {noIp.length} device{noIp.length > 1 ? "s" : ""} without a proper IP</span>
          <span className="muted">— static IP in the wrong range, or DHCP not answering</span>
        </div>
      ) : null}
      {noIp.map((x) => (
        <div key={x.mac} className="ipchk-row">
          <b>{x.name}</b>
          <span className="muted">{x.mac}{x.where ? ` · ${x.where}` : ""}{x.sw_port ? ` port ${x.sw_port}` : ""}{"ip" in x && x.ip ? ` · ${x.ip}` : ""}</span>
        </div>
      ))}
    </section>
  );
}
