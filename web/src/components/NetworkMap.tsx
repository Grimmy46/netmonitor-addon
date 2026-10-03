import { useEffect, useMemo, useRef, useState } from "react";
import { api, type SiteTopology, type TopoNode } from "../api/client";
import { humanizeDuration } from "../lib/duration";
import { splitName } from "../lib/names";

/**
 * Self-building network map for one site. The server re-reads every device's
 * uplink (parent + port) and its last check-in from UniFi every ~20 s.
 *
 *  Map  — radial live map. Each device is a dot that pulses green when a fresh
 *         heartbeat (UniFi check-in) lands and red when one is missed / it's
 *         down. Hover a dot for ~1 s to see its details.
 *  List — the same hierarchy as a tidy list: switches nested by what they plug
 *         into, APs folded under their switch.
 */
const POLL_MS = 10000;
const MISSED_AFTER_S = 120; // no check-in for this long while "online" = missed heartbeat
const HOVER_DELAY_MS = 900;

type Kids = Map<string | null, TopoNode[]>;
type Health = "up" | "missed" | "down" | "dormant" | "unreach";

function health(n: TopoNode): Health {
  if (n.dormant) return "dormant";
  if (n.is_online === false) return "down";
  if (n.seen_age_s != null && n.seen_age_s > MISSED_AFTER_S) return "missed";
  if (n.local_reachable === false) return "unreach";
  return "up";
}
const isInfra = (n: TopoNode) => n.type === "switch" || n.type === "gateway";

function focusFromHash(): string | null {
  const m = window.location.hash.match(/[?&]focus=([^&]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

function speed(mbps: number | null): string | null {
  if (!mbps) return null;
  return mbps >= 1000 ? `${mbps / 1000}G` : `${mbps}M`;
}

/** Remember last_seen per device; a change between polls is a heartbeat. */
function useHeartbeats(nodes: TopoNode[] | undefined) {
  const prev = useRef(new Map<string, string | null>());
  const [beats, setBeats] = useState(new Map<string, number>());
  const [poll, setPoll] = useState(0);
  useEffect(() => {
    if (!nodes) return;
    const next = new Map(beats);
    let changed = false;
    for (const n of nodes) {
      const old = prev.current.get(n.id);
      if (old !== undefined && n.last_seen && n.last_seen !== old) {
        next.set(n.id, (next.get(n.id) ?? 0) + 1);
        changed = true;
      }
      prev.current.set(n.id, n.last_seen);
    }
    if (changed) setBeats(next);
    setPoll((p) => p + 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes]);
  return { beats, poll };
}

export function NetworkMap({ siteId }: { siteId: string }) {
  const [topo, setTopo] = useState<SiteTopology | null>(null);
  const [err, setErr] = useState("");
  const [mode, setMode] = useState<"map" | "list">(focusFromHash() ? "list" : "map");
  const [q, setQ] = useState("");
  const [showDormant, setShowDormant] = useState(false);
  const [focus, setFocus] = useState<string | null>(focusFromHash());

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.siteTopology(siteId)
        .then((t) => { if (alive) { setTopo(t); setErr(""); } })
        .catch((e) => alive && setErr(String(e instanceof Error ? e.message : e)));
    load();
    const id = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, [siteId]);

  const hb = useHeartbeats(topo?.nodes);

  const tree = useMemo(() => {
    const byId = new Map<string, TopoNode>();
    const kids: Kids = new Map();
    const nodes = (topo?.nodes ?? []).filter((n) => showDormant || !n.dormant);
    for (const n of nodes) byId.set(n.id, n);
    for (const n of nodes) {
      const p = n.parent_id && byId.has(n.parent_id) ? n.parent_id : null;
      if (!kids.has(p)) kids.set(p, []);
      kids.get(p)!.push(n);
    }
    for (const list of kids.values())
      list.sort((a, b) => (a.uplink_port ?? 999) - (b.uplink_port ?? 999) || a.name.localeCompare(b.name));
    const top = kids.get(null) ?? [];
    return {
      byId, kids,
      roots: top.filter((n) => n.type === "gateway"),
      unmapped: top.filter((n) => n.type !== "gateway"),
    };
  }, [topo, showDormant]);

  if (err && !topo) return <section className="panel"><div className="banner err">Network map unavailable: {err}</div></section>;
  if (!topo) return <section className="panel"><span className="hint">Building network map…</span></section>;
  if (!topo.topology_at) {
    return (
      <section className="panel">
        <div className="panel-title">Network map</div>
        <p className="hint">No uplink data for this site yet. It fills in within a minute once the console is reachable.</p>
      </section>
    );
  }

  const live = topo.nodes.filter((n) => !n.dormant);
  const sw = live.filter((n) => n.type === "switch");
  const missed = live.filter((n) => health(n) === "missed").length;
  const pick = (id: string) => { setFocus(id); setMode("list"); };

  return (
    <section className="panel">
      <div className="nm-toolbar">
        <div className="panel-title" style={{ margin: 0 }}>Network map</div>
        <span className="sub" style={{ fontSize: 12 }}>
          {sw.filter((s) => s.is_online).length}/{sw.length} switches up
          {missed ? ` · ${missed} missed heartbeat` : ""}
        </span>
        <div className="nm-spacer" />
        <div className="seg">
          <button className={mode === "map" ? "on" : ""} onClick={() => setMode("map")}>Map</button>
          <button className={mode === "list" ? "on" : ""} onClick={() => setMode("list")}>List</button>
        </div>
        <label className="nm-check"><input type="checkbox" checked={showDormant} onChange={(e) => setShowDormant(e.target.checked)} /> Dormant</label>
        {mode === "list" ? (
          <input className="search" type="text" placeholder="Find device, IP, MAC…" value={q} onChange={(e) => setQ(e.target.value)} />
        ) : null}
      </div>

      {topo.outages.length ? (
        <div className="nm-outages">
          {topo.outages.map((o) => {
            const r = splitName(o.root_name);
            const p = o.parent_name ? splitName(o.parent_name) : null;
            return (
              <button key={o.root_id} type="button" className="nm-outage" onClick={() => pick(o.root_id)}>
                <span className="hb-static down" />
                <strong>{r.label}</strong>{r.tag ? <span className="nm-tag">#{r.tag}</span> : null}
                {o.affected_count > 1 ? <span className="nm-count bad">+{o.affected_count - 1} behind it</span> : null}
                <span className="sub">
                  {p ? <>via {p.label}{o.uplink_type === "wireless" ? " (wireless)" : o.uplink_port ? ` port ${o.uplink_port}` : ""}</> : "no uplink on record"}
                  {" · "}down {humanizeDuration(o.down_seconds)}
                </span>
              </button>
            );
          })}
        </div>
      ) : null}

      {mode === "map" ? (
        <RadialMap roots={tree.roots} kids={tree.kids} byId={tree.byId} beats={hb.beats} poll={hb.poll} onPick={pick} />
      ) : (
        <ListView tree={tree} q={q} focus={focus} setFocus={setFocus} beats={hb.beats} poll={hb.poll} />
      )}
    </section>
  );
}

/* ── Heartbeat dot (shared by map + list) ──────────────────────────────── */
function Beat({ n, beat, poll }: { n: TopoNode; beat: number; poll: number }) {
  const h = health(n);
  // A ring that plays once per heartbeat: keyed on the beat counter (green)
  // or on the poll counter while the device is missing/down (red).
  const ringKey = h === "down" || h === "missed" ? `p${poll}` : `b${beat}`;
  const showRing = h === "down" || h === "missed" || beat > 0;
  return (
    <span className={`hb hb-${h}`}>
      {showRing ? <span key={ringKey} className="hb-ring" /> : null}
    </span>
  );
}

/* ── Radial live map ───────────────────────────────────────────────────── */
type Placed = { n: TopoNode; x: number; y: number; depth: number; parent?: Placed };

function RadialMap({ roots, kids, byId, beats, poll, onPick }: {
  roots: TopoNode[]; kids: Kids; byId: Map<string, TopoNode>;
  beats: Map<string, number>; poll: number; onPick: (id: string) => void;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  const SIZE = 1000, C = SIZE / 2;

  const placed = useMemo(() => {
    // Leaf counts → angular span; depth → ring.
    const leaves = new Map<string, number>();
    const seen = new Set<string>();
    const count = (n: TopoNode): number => {
      if (seen.has(n.id)) return 0;
      seen.add(n.id);
      const ch = kids.get(n.id) ?? [];
      const c = ch.length ? ch.reduce((a, k) => a + count(k), 0) : 1;
      leaves.set(n.id, Math.max(c, 1));
      return Math.max(c, 1);
    };
    // Active gateway first; an HA standby already hangs under it.
    const total = roots.reduce((a, r) => a + count(r), 0) || 1;
    let maxDepth = 1;
    const depthOf = (n: TopoNode, d: number, s: Set<string>) => {
      if (s.has(n.id)) return; s.add(n.id); maxDepth = Math.max(maxDepth, d);
      for (const k of kids.get(n.id) ?? []) depthOf(k, d + 1, s);
    };
    const ds = new Set<string>(); roots.forEach((r) => depthOf(r, 0, ds));
    const step = (C - 40) / maxDepth;
    const out: Placed[] = [];
    const done = new Set<string>();
    const place = (n: TopoNode, depth: number, a0: number, a1: number, parent?: Placed) => {
      if (done.has(n.id)) return; done.add(n.id);
      const a = (a0 + a1) / 2;
      const r = depth * step;
      const p: Placed = { n, depth, parent, x: C + r * Math.cos(a), y: C + r * Math.sin(a) };
      out.push(p);
      let cur = a0;
      for (const k of kids.get(n.id) ?? []) {
        const span = ((leaves.get(k.id) ?? 1) / (leaves.get(n.id) ?? 1)) * (a1 - a0);
        place(k, depth + 1, cur, cur + span, p);
        cur += span;
      }
    };
    let cur = -Math.PI / 2;
    for (const r of roots) {
      const span = ((leaves.get(r.id) ?? 1) / total) * Math.PI * 2;
      place(r, 0, cur, cur + span);
      cur += span;
    }
    return out;
  }, [roots, kids, C]);

  const enter = (id: string) => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setHover(id), HOVER_DELAY_MS);
  };
  const leave = () => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = null;
    setHover(null);
  };
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);

  const hp = hover ? placed.find((p) => p.n.id === hover) : undefined;
  // Path from the hovered device back to the gateway, highlighted.
  const pathIds = new Set<string>();
  for (let p = hp; p; p = p.parent) pathIds.add(p.n.id);

  const radius = (n: TopoNode) => (n.type === "gateway" ? 13 : n.type === "switch" ? 8 : 5);

  return (
    <div className="rm-wrap" onMouseLeave={leave}>
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="rm-svg" role="img" aria-label="Live network map">
        {placed.filter((p) => p.parent).map((p) => {
          const h = health(p.n);
          const on = pathIds.has(p.n.id);
          return (
            <line key={`e${p.n.id}`} x1={p.parent!.x} y1={p.parent!.y} x2={p.x} y2={p.y}
              className={`rm-edge ${h === "down" ? "down" : ""} ${on ? "hl" : ""} ${p.n.uplink_type === "wireless" ? "wl" : ""}`} />
          );
        })}
        {placed.map((p) => {
          const h = health(p.n);
          const r = radius(p.n);
          const beat = beats.get(p.n.id) ?? 0;
          const ringKey = h === "down" || h === "missed" ? `p${poll}` : `b${beat}`;
          const ring = h === "down" || h === "missed" || beat > 0;
          return (
            <g key={p.n.id} transform={`translate(${p.x},${p.y})`} className={`rm-node rm-${h} ${hover === p.n.id ? "hov" : ""}`}
              onMouseEnter={() => enter(p.n.id)} onMouseLeave={leave} onClick={() => onPick(p.n.id)}>
              <circle r={r + 8} className="rm-hit" />
              {ring ? <circle key={ringKey} r={r} className="rm-ring" /> : null}
              <circle r={r} className="rm-dot" />
            </g>
          );
        })}
        {hp ? <HoverCard p={hp} parent={hp.n.parent_id ? byId.get(hp.n.parent_id) : undefined} size={SIZE} /> : null}
      </svg>
      <div className="rm-legend">
        <span><i className="lg up" /> heartbeat</span>
        <span><i className="lg missed" /> missed heartbeat</span>
        <span><i className="lg down" /> down</span>
        <span><i className="lg unreach" /> no ping from kiosks</span>
        <span className="sub">Big dot = switch · small = AP · hover a dot to see it · click to open in list</span>
      </div>
    </div>
  );
}

function HoverCard({ p, parent, size }: { p: Placed; parent?: TopoNode; size: number }) {
  const n = p.n;
  const nm = splitName(n.name);
  const h = health(n);
  const lines: string[] = [];
  lines.push([n.type === "ap" ? "Access point" : n.type === "gateway" ? "Gateway" : "Switch", n.model].filter(Boolean).join(" · "));
  if (parent) {
    const pn = splitName(parent.name);
    lines.push(`Plugs into ${pn.label}${n.uplink_type === "wireless" ? " (wireless mesh)" : n.uplink_port ? ` port ${n.uplink_port}` : ""}${speed(n.uplink_speed_mbps) ? ` · ${speed(n.uplink_speed_mbps)}` : ""}`);
  }
  if (n.ip) lines.push(`IP ${n.ip}`);
  if (n.ports_total) lines.push(`${n.ports_up}/${n.ports_total} ports up`);
  lines.push(
    h === "down" ? `DOWN for ${humanizeDuration(n.down_seconds)}`
      : n.seen_age_s != null ? `Last heartbeat ${Math.round(n.seen_age_s)}s ago${h === "missed" ? " (missed)" : ""}`
        : "No heartbeat data yet",
  );
  const W = 300, LH = 19, H = 34 + lines.length * LH;
  let x = p.x + 16, y = p.y - H / 2;
  if (x + W > size - 6) x = p.x - 16 - W;
  y = Math.max(6, Math.min(size - H - 6, y));
  return (
    <g className="rm-card" transform={`translate(${x},${y})`} pointerEvents="none">
      <rect width={W} height={H} rx={10} />
      <text x={14} y={24} className="rm-card-title">
        {nm.label.length > 26 ? nm.label.slice(0, 25) + "…" : nm.label}
        {nm.tag ? <tspan className="rm-card-tag">  #{nm.tag}</tspan> : null}
      </text>
      {lines.map((l, i) => (
        <text key={i} x={14} y={24 + (i + 1) * LH} className={`rm-card-line ${i === lines.length - 1 && (h === "down" || h === "missed") ? "bad" : ""}`}>
          {l.length > 44 ? l.slice(0, 43) + "…" : l}
        </text>
      ))}
    </g>
  );
}

/* ── List view ─────────────────────────────────────────────────────────── */
function ListView({ tree, q, focus, setFocus, beats, poll }: {
  tree: { byId: Map<string, TopoNode>; kids: Kids; roots: TopoNode[]; unmapped: TopoNode[] };
  q: string; focus: string | null; setFocus: (id: string) => void;
  beats: Map<string, number>; poll: number;
}) {
  const { byId, kids, roots, unmapped } = tree;
  const [openAps, setOpenAps] = useState<Set<string>>(new Set());
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const focusRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (focus && focusRef.current) focusRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focus]);

  // Search keeps matches + their ancestors; matches inside AP lists open them.
  const term = q.trim().toLowerCase();
  const match = useMemo(() => {
    if (!term) return null;
    const keep = new Set<string>();
    for (const n of byId.values()) {
      const hay = `${n.name} ${n.model ?? ""} ${n.ip ?? ""} ${n.mac ?? ""}`.toLowerCase();
      if (!hay.includes(term)) continue;
      const s = new Set<string>();
      for (let c: TopoNode | undefined = n; c && !s.has(c.id); c = c.parent_id ? byId.get(c.parent_id) : undefined) {
        s.add(c.id); keep.add(c.id);
      }
    }
    return keep;
  }, [term, byId]);

  // A focused device that's an AP: open its switch's AP list.
  useEffect(() => {
    const f = focus ? byId.get(focus) : undefined;
    if (f && !isInfra(f) && f.parent_id) setOpenAps((s) => new Set(s).add(f.parent_id!));
  }, [focus, byId]);

  const toggle = (set: Set<string>, id: string) => { const nx = new Set(set); if (nx.has(id)) nx.delete(id); else nx.add(id); return nx; };

  const row = (n: TopoNode, depth: number, small = false) => {
    const nm = splitName(n.name);
    const h = health(n);
    const parent = n.parent_id ? byId.get(n.parent_id) : undefined;
    const isRoot = n.outage_root_id === n.id;
    return (
      <div
        key={n.id}
        ref={focus === n.id ? focusRef : undefined}
        className={`nl-row ${small ? "small" : ""} nl-${h} ${focus === n.id ? "focus" : ""} ${isRoot ? "root" : ""}`}
        style={{ paddingLeft: 12 + depth * 20 }}
        onClick={() => setFocus(n.id)}
        title={nm.full}
      >
        <Beat n={n} beat={beats.get(n.id) ?? 0} poll={poll} />
        <span className="nl-name">{nm.label}</span>
        {nm.tag ? <span className="nm-tag">#{nm.tag}</span> : null}
        {parent ? (
          <span className="nl-port">{n.uplink_type === "wireless" ? "mesh" : n.uplink_port ? `port ${n.uplink_port}` : ""}</span>
        ) : null}
        {isRoot ? <span className="nm-count bad">root cause</span> : null}
        {h === "down" ? <span className="nl-status bad">down {humanizeDuration(n.down_seconds)}</span> : null}
        {h === "missed" ? <span className="nl-status bad">no heartbeat {humanizeDuration(n.seen_age_s)}</span> : null}
        {h === "unreach" ? <span className="nl-status warn">no ping</span> : null}
        {h === "dormant" ? <span className="nl-status">dormant</span> : null}
        <span className="nm-spacer" />
        {!small && n.ports_total ? (
          <span className="nl-ports" title={`${n.ports_up}/${n.ports_total} ports up`}>
            <span className="nl-bar"><span style={{ width: `${Math.round(((n.ports_up ?? 0) / n.ports_total) * 100)}%` }} /></span>
            {n.ports_up}/{n.ports_total}
          </span>
        ) : null}
        <span className="nl-ip">{n.ip ?? ""}</span>
      </div>
    );
  };

  const seen = new Set<string>();
  const renderInfra = (n: TopoNode, depth: number): JSX.Element | null => {
    if (seen.has(n.id) || (match && !match.has(n.id))) return null;
    seen.add(n.id);
    const all = kids.get(n.id) ?? [];
    const subs = all.filter(isInfra);
    const aps = all.filter((c) => !isInfra(c) && (!match || match.has(c.id)));
    const apsDown = aps.filter((c) => health(c) === "down").length;
    const apsOpen = openAps.has(n.id) || (!!match && aps.length > 0);
    const isClosed = closed.has(n.id) && !match;
    return (
      <div key={n.id} className="nl-group">
        <div className={`nl-line ${n.outage_root_id === n.id ? "root" : ""}`}>
          <button className="nl-caret" onClick={() => setClosed((s) => toggle(s, n.id))} disabled={!subs.length}>
            {subs.length ? (isClosed ? "▸" : "▾") : ""}
          </button>
          <div style={{ flex: 1, minWidth: 0 }}>{row(n, 0)}</div>
          {aps.length ? (
            <button className={`nl-aps ${apsDown ? "bad" : ""} ${apsOpen ? "open" : ""}`} onClick={() => setOpenAps((s) => toggle(s, n.id))}>
              {aps.length} AP{aps.length > 1 ? "s" : ""}{apsDown ? ` · ${apsDown} down` : ""}
            </button>
          ) : <span className="nl-aps empty" />}
        </div>
        {apsOpen ? (
          <div className="nl-aplist">{aps.map((a) => row(a, 1, true))}</div>
        ) : null}
        {!isClosed ? subs.map((c) => renderInfra(c, depth + 1)) : null}
      </div>
    );
  };

  const loose = unmapped.filter((u) => !match || match.has(u.id));
  return (
    <div className="nl-list">
      {roots.map((r) => renderInfra(r, 0))}
      {loose.length ? (
        <>
          <div className="nm-group">Not mapped yet</div>
          {loose.map((u) => row(u, 0, true))}
        </>
      ) : null}
      {match && match.size === 0 ? <p className="hint" style={{ padding: 12 }}>Nothing matches “{q}”.</p> : null}
    </div>
  );
}
