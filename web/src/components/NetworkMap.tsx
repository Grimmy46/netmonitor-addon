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
const HOVER_DELAY_MS = 150;

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
  const [skin, setSkinState] = useState<MapSkin>(() => (localStorage.getItem("nm-map-skin") === "pro" ? "pro" : "holo"));
  const setSkin = (v: MapSkin) => { setSkinState(v); localStorage.setItem("nm-map-skin", v); };
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
        {mode === "map" ? (
          <div className="seg">
            <button className={skin === "pro" ? "on" : ""} onClick={() => setSkin("pro")} title="Plain, labelled switches">Pro</button>
            <button className={skin === "holo" ? "on" : ""} onClick={() => setSkin("holo")} title="Holographic">Holo</button>
          </div>
        ) : null}
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
        <RadialMap roots={tree.roots} kids={tree.kids} byId={tree.byId} beats={hb.beats} poll={hb.poll} onPick={pick} topo={topo} skin={skin} />
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

/* ── Radial live map — holographic "JARVIS" style ─────────────────────── */
type Placed = { n: TopoNode; x: number; y: number; depth: number; a: number; parent?: Placed };
export type MapSkin = "holo" | "pro";

function RadialMap({ roots, kids, byId, beats, poll, onPick, topo, skin }: {
  roots: TopoNode[]; kids: Kids; byId: Map<string, TopoNode>;
  beats: Map<string, number>; poll: number; onPick: (id: string) => void; topo: SiteTopology; skin: MapSkin;
}) {
  const holo = skin === "holo";
  const [hover, setHover] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  const SIZE = 1000, C = SIZE / 2;

  const { placed, step, maxDepth } = useMemo(() => {
    // An HA standby gateway hanging off the active one is drawn as a second
    // core beside it — two AIs side by side — instead of on the first ring.
    const isTwin = (n: TopoNode) => n.type === "gateway" && !!n.parent_id &&
      byId.get(n.parent_id)?.type === "gateway";
    const kidsOf = (id: string) => (kids.get(id) ?? []).filter((k) => !isTwin(k));
    const leaves = new Map<string, number>();
    const seen = new Set<string>();
    const count = (n: TopoNode): number => {
      if (seen.has(n.id)) return 0;
      seen.add(n.id);
      const ch = kidsOf(n.id);
      const c = Math.max(ch.length ? ch.reduce((a, k) => a + count(k), 0) : 1, 1);
      leaves.set(n.id, c);
      return c;
    };
    const total = roots.reduce((a, r) => a + count(r), 0) || 1;
    let maxDepth = 1;
    const ds = new Set<string>();
    const depthOf = (n: TopoNode, d: number) => {
      if (ds.has(n.id)) return; ds.add(n.id); maxDepth = Math.max(maxDepth, d);
      for (const k of kidsOf(n.id)) depthOf(k, d + 1);
    };
    roots.forEach((r) => depthOf(r, 0));
    const step = (C - 70) / maxDepth;
    const out: Placed[] = [];
    const done = new Set<string>();
    const place = (n: TopoNode, depth: number, a0: number, a1: number, parent?: Placed) => {
      if (done.has(n.id)) return; done.add(n.id);
      const a = (a0 + a1) / 2;
      const r = depth * step;
      const p: Placed = { n, depth, a, parent, x: C + r * Math.cos(a), y: C + r * Math.sin(a) };
      out.push(p);
      if (depth === 0) {
        const twins = (kids.get(n.id) ?? []).filter(isTwin);
        twins.forEach((t, i) => {
          done.add(t.id);
          out.push({ n: t, depth: 0, a: 0, parent: p, x: C + 46 * (i + 1), y: C });
          p.x = C - 23 * twins.length; // shift the active core left so the pair is centred
        });
      }
      let cur = a0;
      for (const k of kidsOf(n.id)) {
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
    return { placed: out, step, maxDepth };
  }, [roots, kids, byId, C]);

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
  const pathIds = new Set<string>();
  for (let p = hp; p; p = p.parent) pathIds.add(p.n.id);

  const radius = (n: TopoNode) => (n.type === "gateway" ? 15 : n.type === "switch" ? 7 : 4);
  const liveNodes = placed.filter((p) => !p.n.dormant);
  const online = liveNodes.filter((p) => p.n.is_online !== false).length;
  const links = topo.wan?.links ?? [];
  const ticks = Array.from({ length: 72 }, (_, i) => (i * Math.PI * 2) / 72);
  const R = C - 30;

  return (
    <div className={holo ? "holo" : "promap"} onMouseLeave={leave}>
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className={holo ? "holo-svg" : "promap-svg"} role="img" aria-label="Live network map">
        <defs>
          <radialGradient id="hl-bg" cx="50%" cy="50%" r="60%">
            <stop offset="0%" stopColor="#06233f" />
            <stop offset="55%" stopColor="#031427" />
            <stop offset="100%" stopColor="#01060d" />
          </radialGradient>
          <radialGradient id="hl-core" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#ffffff" />
            <stop offset="35%" stopColor="#7df9ff" />
            <stop offset="100%" stopColor="#0077ff" stopOpacity="0" />
          </radialGradient>
          <radialGradient id="hl-core-red" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#fff1ec" />
            <stop offset="35%" stopColor="#ff5a3c" />
            <stop offset="100%" stopColor="#ff2200" stopOpacity="0" />
          </radialGradient>
          <linearGradient id="hl-sweep" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#00e5ff" stopOpacity="0" />
            <stop offset="100%" stopColor="#00e5ff" stopOpacity="0.22" />
          </linearGradient>
          <filter id="hl-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3.2" result="b" />
            <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <pattern id="hl-grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M40 0H0V40" fill="none" stroke="#0bd3ff" strokeOpacity="0.05" />
          </pattern>
        </defs>

        {holo ? <rect width={SIZE} height={SIZE} fill="url(#hl-bg)" /> : null}
        {holo ? <rect width={SIZE} height={SIZE} fill="url(#hl-grid)" /> : null}

        {/* Range rings (one per hop) + outer tick ring + radar sweep */}
        {Array.from({ length: maxDepth }, (_, d) => (
          <circle key={`r${d}`} cx={C} cy={C} r={(d + 1) * step} className={`hl-ring ${d % 3 === 2 ? "major" : ""}`} />
        ))}
        {holo ? <>
        <circle cx={C} cy={C} r={R} className="hl-outer" />
        {ticks.map((a, i) => (
          <line key={`t${i}`} x1={C + R * Math.cos(a)} y1={C + R * Math.sin(a)}
            x2={C + (R - (i % 6 === 0 ? 14 : 6)) * Math.cos(a)} y2={C + (R - (i % 6 === 0 ? 14 : 6)) * Math.sin(a)}
            className="hl-tick" />
        ))}
        <g className="hl-spin-slow"><circle cx={C} cy={C} r={R + 12} className="hl-arc" /></g>
        <g className="hl-sweep">
          <path d={`M${C},${C} L${C + R},${C} A${R},${R} 0 0,0 ${C + R * Math.cos(-0.6)},${C + R * Math.sin(-0.6)} Z`} fill="url(#hl-sweep)" />
          <line x1={C} y1={C} x2={C + R} y2={C} stroke="#7df9ff" strokeOpacity="0.5" />
        </g>
        </> : null}

        {/* Links: a dim base + a bright dash "data flow" moving outward */}
        <g filter={holo ? "url(#hl-glow)" : undefined}>
          {placed.filter((p) => p.parent).map((p) => {
            const h = health(p.n);
            const on = pathIds.has(p.n.id);
            const cls = h === "down" ? "down" : h === "dormant" ? "dorm" : "";
            return (
              <g key={`e${p.n.id}`}>
                <line x1={p.parent!.x} y1={p.parent!.y} x2={p.x} y2={p.y} className={`hl-edge ${cls} ${on ? "hl" : ""}`} />
                {holo && h !== "down" && h !== "dormant" ? (
                  <line x1={p.parent!.x} y1={p.parent!.y} x2={p.x} y2={p.y}
                    className={`hl-flow ${p.n.uplink_type === "wireless" ? "wl" : ""} ${on ? "hl" : ""}`} />
                ) : null}
              </g>
            );
          })}
        </g>

        {/* Devices */}
        {placed.map((p) => {
          const h = health(p.n);
          const r = radius(p.n);
          const beat = beats.get(p.n.id) ?? 0;
          const bad = h === "down" || h === "missed";
          const ringKey = bad ? `p${poll}` : `b${beat}`;
          const ring = bad || beat > 0;
          const gw = p.n.type === "gateway";
          return (
            <g key={p.n.id} transform={`translate(${p.x},${p.y})`}
              className={`hl-node hl-${h} ${gw ? "gw" : ""} ${gw && p.parent ? "twin" : ""} ${hover === p.n.id ? "hov" : ""} ${pathIds.has(p.n.id) ? "onpath" : ""}`}
              onMouseEnter={() => enter(p.n.id)} onMouseLeave={leave} onClick={() => onPick(p.n.id)}>
              <circle r={Math.max(r + 10, 14)} className="hl-hit" />
              {!holo ? null : gw ? (
                <>
                  <circle r={r * 2.6} fill={bad ? "url(#hl-core-red)" : "url(#hl-core)"} className="hl-orb" />
                  <g className="hl-spin"><circle r={r + 9} className="hl-gw-ring a" /></g>
                  <g className="hl-spin-rev"><circle r={r + 15} className="hl-gw-ring b" /></g>
                </>
              ) : (
                <circle r={r * 2.4} className="hl-halo" />
              )}
              {holo && p.n.type === "switch" ? <circle r={r + 4} className="hl-switch-ring" /> : null}
              {ring ? <circle key={ringKey} r={r} className="hl-beat" /> : null}
              <circle r={gw ? (holo ? r * 0.55 : 10) : r} className="hl-core" filter={holo ? "url(#hl-glow)" : undefined} />
              {!holo && (p.n.type === "switch" || (gw && !p.parent)) ? <PlainLabel p={p} r={gw ? 10 : r} C={C} /> : null}
            </g>
          );
        })}

        {/* HUD corners */}
        {holo ? <g className="hl-hud">
          <text x={28} y={44} className="big">{(topo.site_name || "SITE").toUpperCase()} // NETWORK</text>
          <text x={28} y={66}>NODES ONLINE {online}/{liveNodes.length}</text>
          <text x={28} y={86} className={topo.outages.length ? "bad" : ""}>
            {topo.outages.length ? `FAULTS ${topo.outages.length} · ORIGIN TRACED` : "ALL SYSTEMS NOMINAL"}
          </text>
          {links.map((l, i) => (
            <text key={l.key} x={SIZE - 28} y={44 + i * 20} textAnchor="end" className={l.up ? "" : "bad"}>
              {l.key} {l.up ? (l.active ? "ACTIVE" : "STANDBY") : "OFFLINE"}{l.up && l.latency_ms != null ? ` · ${l.latency_ms}MS` : ""}
            </text>
          ))}
          <text x={SIZE - 28} y={SIZE - 28} textAnchor="end" className="dim">
            {topo.wan?.gateway ? `CORE ${topo.wan.gateway}${topo.wan.ha_units > 1 ? " · HA PAIR" : ""}` : ""}
          </text>
          <text x={28} y={SIZE - 28} className="dim">HOVER A NODE · CLICK TO OPEN</text>
        </g> : null}

        {hp ? <HoverCard p={hp} parent={hp.n.parent_id ? byId.get(hp.n.parent_id) : undefined} size={SIZE} holo={holo} /> : null}
      </svg>
      <div className={holo ? "holo-legend" : "promap-legend"}>
        <span><i className="lg up" /> heartbeat</span>
        <span><i className="lg missed" /> missed</span>
        <span><i className="lg down" /> down</span>
        <span><i className="lg unreach" /> no kiosk ping</span>
        <span className="dim">{holo ? "large = switch · small = AP · center = core gateway" : "labelled = switch · small = AP · hover any dot for details"}</span>
      </div>
    </div>
  );
}

/** Pro mode: a switch's short name, pushed outward from the centre. */
function PlainLabel({ p, r, C }: { p: Placed; r: number; C: number }) {
  const nm = splitName(p.n.name);
  const t = nm.label.length > 18 ? nm.label.slice(0, 17) + "…" : nm.label;
  if (p.depth === 0) {
    return <text y={r + 16} textAnchor="middle" className="pm-label gw">{t}</text>;
  }
  const dx = Math.cos(p.a), dy = Math.sin(p.a);
  const off = r + 5;
  const anchor = Math.abs(dx) < 0.3 ? "middle" : dx > 0 ? "start" : "end";
  void C;
  return (
    <text x={dx * off} y={dy * off + (Math.abs(dx) < 0.3 ? (dy > 0 ? 9 : -3) : 4)} textAnchor={anchor} className="pm-label">
      {t}
    </text>
  );
}

function HoverCard({ p, parent, size, holo }: { p: Placed; parent?: TopoNode; size: number; holo: boolean }) {
  const n = p.n;
  const nm = splitName(n.name);
  const h = health(n);
  const bad = h === "down" || h === "missed";
  const rows: [string, string][] = [];
  rows.push(["TYPE", [n.type === "ap" ? "ACCESS POINT" : n.type === "gateway" ? "GATEWAY" : "SWITCH", n.model].filter(Boolean).join(" · ")]);
  if (parent) {
    const pn = splitName(parent.name);
    rows.push(["UPLINK", `${pn.label}${n.uplink_type === "wireless" ? " · MESH" : n.uplink_port ? ` · P${n.uplink_port}` : ""}${speed(n.uplink_speed_mbps) ? ` · ${speed(n.uplink_speed_mbps)}` : ""}`]);
  }
  if (n.ip) rows.push(["IP", n.ip]);
  if (n.ports_total) rows.push(["PORTS", `${n.ports_up}/${n.ports_total} UP`]);
  rows.push(["STATUS",
    h === "down" ? `DOWN ${humanizeDuration(n.down_seconds)}`
      : n.seen_age_s != null ? `BEAT ${Math.round(n.seen_age_s)}S AGO${h === "missed" ? " · MISSED" : ""}`
        : "NO BEAT DATA"]);
  const W = 400, LH = 20, H = 50 + rows.length * LH, cut = 14;
  let x = p.x + 22, y = p.y - H / 2;
  if (x + W > size - 10) x = p.x - 22 - W;
  y = Math.max(10, Math.min(size - H - 10, y));
  const shape = holo ? `M${cut},0 H${W} V${H - cut} L${W - cut},${H} H0 V${cut} Z` : `M8,0 H${W - 8} Q${W},0 ${W},8 V${H - 8} Q${W},${H} ${W - 8},${H} H8 Q0,${H} 0,${H - 8} V8 Q0,0 8,0 Z`;
  // Leader line from the node to the card edge.
  const lx = x > p.x ? x : x + W;
  return (
    <g pointerEvents="none" className={`hl-card ${bad ? "bad" : ""}`}>
      <polyline points={`${p.x},${p.y} ${(p.x + lx) / 2},${y + 22} ${lx},${y + 22}`} className="hl-leader" />
      <g transform={`translate(${x},${y})`}>
        <path d={shape} className="hl-card-bg" />
        {holo ? <path d={`M0,${cut + 10} V${cut} L${cut},0 H${cut + 30}`} className="hl-card-corner" /> : null}
        {holo ? <path d={`M${W},${H - cut - 10} V${H - cut} L${W - cut},${H} H${W - cut - 30}`} className="hl-card-corner" /> : null}
        <text x={18} y={30} className="hl-card-title">
          {(() => { const t = nm.label.length > 24 ? nm.label.slice(0, 23) + "…" : nm.label; return holo ? t.toUpperCase() : t; })()}
          {nm.tag ? <tspan className="hl-card-tag">{`  #${nm.tag}`}</tspan> : null}
        </text>
        <line x1={18} x2={W - 18} y1={40} y2={40} className="hl-card-rule" />
        {rows.map(([k, v], i) => (
          <g key={k}>
            <text x={18} y={60 + i * LH} className="hl-card-k">{k}</text>
            <text x={90} y={60 + i * LH} className={`hl-card-v ${k === "STATUS" && bad ? "bad" : ""}`}>
              {v.length > 38 ? v.slice(0, 37) + "…" : v}
            </text>
          </g>
        ))}
      </g>
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
