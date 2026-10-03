import { useEffect, useState } from "react";
import { api, isAdmin, type Closure, type NetworkOverview, type WanLink } from "../api/client";
import { humanizeDuration } from "../lib/duration";

/**
 * The first thing on the landing page: is Main OK?
 *  - WAN1 / WAN2 straight from the gateway (up/down, which one carries traffic)
 *  - switches and APs online
 *  - every outage traced to its root: "X is down, N behind it, plugged into Y port P"
 * Refreshes every 15 s; the server re-reads UniFi every 60 s.
 */
const GOOD = "var(--good)";
const BAD = "var(--critical)";
const WARN = "var(--warning)";
const MUTED = "var(--ink-muted)";

function speed(mbps: number | null): string | null {
  if (!mbps) return null;
  return mbps >= 1000 ? `${mbps / 1000}G` : `${mbps}M`;
}

function Tile({ color, label, value, detail, onClick }: {
  color: string; label: string; value: string; detail?: string | null; onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="mh-tile"
      style={{ borderTop: `4px solid ${color}`, cursor: onClick ? "pointer" : "default" }}
    >
      <div className="mh-label">{label}</div>
      <div className="mh-value" style={{ color }}>{value}</div>
      {detail ? <div className="mh-detail">{detail}</div> : null}
    </button>
  );
}

function wanTile(l: WanLink | undefined, key: string, stale: boolean) {
  if (!l) return <Tile key={key} color={MUTED} label={key} value="—" detail="not configured" />;
  const color = stale ? MUTED : l.up ? GOOD : BAD;
  const role = l.up ? (l.active ? "carrying traffic" : "standby") : "no link";
  const bits = [role, l.latency_ms != null && l.up ? `${l.latency_ms} ms` : null, l.up ? speed(l.speed_mbps) : null]
    .filter(Boolean).join(" · ");
  return <Tile key={key} color={color} label={key} value={stale ? "?" : l.up ? "UP" : "DOWN"} detail={bits} />;
}

const fmtWhen = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

/** datetime-local value (local time) for a Date. */
const toLocalInput = (d: Date) => {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};

/**
 * Planned closure ("dark until Tuesday"): alerts pause, the dormant clock
 * freezes so gear that's off for days doesn't age out of view, and one push
 * after reopening lists anything that didn't come back.
 */
function ClosureBar({ c, onChange }: { c: Closure | null | undefined; onChange: () => void }) {
  const admin = isAdmin();
  const [editing, setEditing] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const openForm = () => {
    const now = new Date();
    now.setSeconds(0, 0);
    const e = new Date(now.getTime() + 3 * 86400000);
    e.setHours(10, 0, 0, 0);
    setStart(c ? toLocalInput(new Date(c.start)) : toLocalInput(now));
    setEnd(c ? toLocalInput(new Date(c.end)) : toLocalInput(e));
    setNote(c?.note ?? "");
    setErr("");
    setEditing(true);
  };
  const save = async () => {
    setBusy(true); setErr("");
    try {
      await api.setClosure(new Date(start).toISOString(), new Date(end).toISOString(), note);
      setEditing(false); onChange();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  };
  const end_ = async () => {
    const msg = c?.phase === "closed"
      ? "Reopen now? Alerts resume after the reopen check, and you'll get one summary of anything still down."
      : c?.phase === "scheduled" ? "Cancel this scheduled closure?" : "Finish the reopen check now?";
    if (!window.confirm(msg)) return;
    setBusy(true);
    try { await api.endClosure(); onChange(); } finally { setBusy(false); }
  };

  if (editing) {
    return (
      <div className="mh-closure edit">
        <strong>Plan a closure</strong>
        <label>Closes <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label>Reopens <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        <input type="text" placeholder="Note (optional), e.g. Fair closed Mon–Wed" value={note} onChange={(e) => setNote(e.target.value)} />
        <div className="mh-closure-help sub">
          Alerts pause, and days spent closed don't count toward "dormant", so nothing quietly drops off the map.
          About 3 h after reopening you get one push listing anything that didn't come back.
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-primary" disabled={busy || !start || !end} onClick={save}>Save</button>
          <button className="btn" onClick={() => setEditing(false)}>Cancel</button>
          {err ? <span style={{ color: BAD, fontSize: 12, alignSelf: "center" }}>{err}</span> : null}
        </div>
      </div>
    );
  }
  if (!c) {
    return admin ? (
      <div className="mh-closure-link">
        <button className="btn" style={{ fontSize: 12, padding: "3px 10px" }} onClick={openForm}>Plan a closure…</button>
      </div>
    ) : null;
  }
  const text =
    c.phase === "scheduled" ? <>Closure planned: <strong>{fmtWhen(c.start)}</strong> → <strong>{fmtWhen(c.end)}</strong>. Alerts will pause automatically.</>
    : c.phase === "closed" ? <><strong>Closed until {fmtWhen(c.end)}.</strong> Alerts paused · dormant clock frozen · gear switched off shows grey.</>
    : <><strong>Reopen check</strong>: waiting for gear to power up until {fmtWhen(c.reopen_until)}, then one summary push of anything still down. Anything red below hasn't come back yet.</>;
  return (
    <div className={`mh-closure ${c.phase}`}>
      <span className="mh-closure-icon">{c.phase === "reopening" ? "↻" : "⏸"}</span>
      <div style={{ flex: 1 }}>
        {text}
        {c.note ? <div className="sub" style={{ fontSize: 12 }}>{c.note}</div> : null}
      </div>
      {admin ? (
        <div style={{ display: "flex", gap: 6 }}>
          {c.phase !== "reopening" ? <button className="btn" style={{ fontSize: 12, padding: "3px 10px" }} onClick={openForm}>Edit</button> : null}
          <button className="btn" style={{ fontSize: 12, padding: "3px 10px" }} disabled={busy} onClick={end_}>
            {c.phase === "closed" ? "Reopen now" : c.phase === "scheduled" ? "Cancel" : "Finish"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function MainHealth() {
  const [ov, setOv] = useState<NetworkOverview | null>(null);
  const [err, setErr] = useState("");

  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = () =>
      api.networkOverview()
        .then((o) => { if (alive) { setOv(o); setErr(""); } })
        .catch((e) => alive && setErr(String(e instanceof Error ? e.message : e)));
    load();
    const id = setInterval(load, 15000);
    return () => { alive = false; clearInterval(id); };
  }, [tick]);

  if (err && !ov) return <div className="banner err" style={{ marginBottom: 14 }}>Main status unavailable: {err}</div>;
  if (!ov) return <div className="panel mh-panel"><span className="hint">Checking Main…</span></div>;

  const open = (id?: string) => {
    window.location.hash = `#/site/${ov.site_id}${id ? `?focus=${id}` : ""}`;
  };
  const links = ov.wan?.links ?? [];
  const stale = ov.wan?.stale ?? true;
  const sw = ov.counts.switch;
  const ap = ov.counts.ap;
  const closed = ov.closure?.phase === "closed";
  const closedOut = ov.outages.filter((o) => o.closed_down);
  const outages = ov.outages.filter((o) => !o.closed_down);
  const closedCount = closedOut.reduce((a, o) => a + o.affected_count, 0);
  const swColor = sw.total === 0 ? MUTED : sw.online === sw.total ? GOOD : closed && !outages.length ? MUTED : BAD;
  const apColor = ap.total === 0 ? MUTED : ap.online === ap.total ? GOOD : closed && !outages.length ? MUTED : WARN;
  const allGood = !stale && links.every((l) => l.up) && ov.outages.length === 0 && ov.unreachable.length === 0;

  return (
    <section className="panel mh-panel">
      <div className="mh-head">
        <span className="panel-title" style={{ margin: 0 }}>{ov.site_name} network</span>
        {ov.wan?.gateway ? <span className="sub">{ov.wan.gateway}{ov.wan.ha_units > 1 ? " (HA pair)" : ""}</span> : null}
        <div className="spacer" />
        {ov.wan?.age_seconds != null ? (
          <span className="sub" style={{ fontSize: 12 }}>
            {stale ? "⚠ stale · " : ""}updated {humanizeDuration(ov.wan.age_seconds)}{ov.wan.age_seconds >= 60 ? " ago" : ""}
          </span>
        ) : null}
        <button className="btn" style={{ fontSize: 12, padding: "3px 10px" }} onClick={() => open()}>Network map →</button>
      </div>

      <div className="mh-tiles">
        {wanTile(links.find((l) => l.key === "WAN1"), "WAN1", stale)}
        {wanTile(links.find((l) => l.key === "WAN2"), "WAN2", stale)}
        <Tile color={swColor} label="Switches" value={`${sw.online}/${sw.total}`}
          detail={sw.online === sw.total ? "all online" : `${sw.total - sw.online} down`} onClick={() => open()} />
        <Tile color={apColor} label="Access points" value={`${ap.online}/${ap.total}`}
          detail={ap.online === ap.total ? "all online" : `${ap.total - ap.online} down`} onClick={() => open()} />
      </div>

      <ClosureBar c={ov.closure} onChange={() => setTick((t) => t + 1)} />

      {closed && closedCount ? (
        <div className="mh-ok" style={{ color: MUTED }}>⏸ {closedCount} device{closedCount === 1 ? "" : "s"} switched off for the closure (not counted as faults).</div>
      ) : null}

      {allGood ? (
        <div className="mh-ok">● Everything on {ov.site_name} is up.</div>
      ) : null}

      {outages.map((o) => (
        <button key={o.root_id} type="button" className="mh-issue" style={{ borderLeftColor: BAD }} onClick={() => open(o.root_id)}>
          <div className="mh-issue-title">
            <span style={{ color: BAD }}>●</span> {o.root_name}
            {o.affected_count > 1 ? <span className="mh-badge">+{o.affected_count - 1} behind it</span> : null}
            <span className="sub" style={{ marginLeft: "auto", fontSize: 12 }}>
              down {humanizeDuration(o.down_seconds)}
            </span>
          </div>
          <div className="mh-issue-body">
            {o.parent_name
              ? <>Plugs into <strong>{o.parent_name}</strong>{o.uplink_type === "wireless" ? " (wireless mesh)" : o.uplink_port ? <> on <strong>port {o.uplink_port}</strong></> : null}
                  {o.parent_online === false ? " (also down)" : ""}. Check power at the device and that link.</>
              : "No uplink on record for this device."}
            {o.affected.length ? (
              <div className="sub" style={{ fontSize: 12, marginTop: 2 }}>
                Also down: {o.affected.slice(0, 6).map((a) => a.name).join(", ")}{o.affected.length > 6 ? "…" : ""}
              </div>
            ) : null}
          </div>
        </button>
      ))}

      {ov.unreachable.length && !closed ? (
        <button type="button" className="mh-issue" style={{ borderLeftColor: WARN }} onClick={() => open()}>
          <div className="mh-issue-title">
            <span style={{ color: WARN }}>●</span> {ov.unreachable.length} up in UniFi but not answering kiosks' pings
          </div>
          <div className="mh-issue-body sub" style={{ fontSize: 12 }}>
            {ov.unreachable.slice(0, 6).map((u) => u.name).join(", ")}{ov.unreachable.length > 6 ? "…" : ""}
          </div>
        </button>
      ) : null}
    </section>
  );
}
