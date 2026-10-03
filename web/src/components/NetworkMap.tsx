import { useEffect, useMemo, useRef, useState } from "react";
import { api, type SiteTopology, type TopoNode } from "../api/client";
import { humanizeDuration } from "../lib/duration";

/**
 * Self-building network map for one site. The server re-reads every device's
 * uplink (parent + port) from UniFi each minute, so this tree draws itself —
 * nothing to maintain by hand. Outages are grouped by the device where the
 * failure starts; everything behind it is marked as "behind" that root.
 */
type Kids = Map<string | null, TopoNode[]>;
const TYPE_LABEL: Record<string, string> = { gateway: "Gateway", switch: "Switch", ap: "AP" };

function focusFromHash(): string | null {
  const m = window.location.hash.match(/[?&]focus=([^&]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

function speed(mbps: number | null): string | null {
  if (!mbps) return null;
  return mbps >= 1000 ? `${mbps / 1000}G` : `${mbps}M`;
}

function state(n: TopoNode): "up" | "down" | "dormant" | "unreach" {
  if (n.dormant) return "dormant";
  if (n.is_online === false) return "down";
  if (n.local_reachable === false) return "unreach";
  return "up";
}

function linkText(n: TopoNode, parent: TopoNode | undefined): string {
  if (!parent) return "";
  if (n.uplink_type === "wireless") return "wireless mesh";
  const parts = [];
  if (n.uplink_port) parts.push(`port ${n.uplink_port}`);
  if (n.local_port) parts.push(`(its port ${n.local_port})`);
  const s = speed(n.uplink_speed_mbps);
  if (s) parts.push(s);
  return parts.join(" ");
}

export function NetworkMap({ siteId }: { siteId: string }) {
  const [topo, setTopo] = useState<SiteTopology | null>(null);
  const [err, setErr] = useState("");
  const [mode, setMode] = useState<"tree" | "diagram">("tree");
  const [infraOnly, setInfraOnly] = useState(false);
  const [q, setQ] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<string | null>(focusFromHash());
  const focusRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.siteTopology(siteId)
        .then((t) => { if (alive) { setTopo(t); setErr(""); } })
        .catch((e) => alive && setErr(String(e instanceof Error ? e.message : e)));
    load();
    const id = setInterval(load, 30000);
    return () => { alive = false; clearInterval(id); };
  }, [siteId]);

  useEffect(() => {
    if (focus && focusRef.current) focusRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focus, topo, mode]);

  const { byId, kids, roots, unmapped } = useMemo(() => {
    const byId = new Map<string, TopoNode>();
    const kids: Kids = new Map();
    for (const n of topo?.nodes ?? []) byId.set(n.id, n);
    for (const n of topo?.nodes ?? []) {
      const p = n.parent_id && byId.has(n.parent_id) ? n.parent_id : null;
      if (!kids.has(p)) kids.set(p, []);
      kids.get(p)!.push(n);
    }
    for (const list of kids.values())
      list.sort((a, b) => (a.uplink_port ?? 999) - (b.uplink_port ?? 999) || a.name.localeCompare(b.name));
    const top = kids.get(null) ?? [];
    // Gateways are the real roots; anything else without a parent isn't mapped yet.
    const roots = top.filter((n) => n.type === "gateway");
    const unmapped = top.filter((n) => n.type !== "gateway");
    return { byId, kids, roots, unmapped };
  }, [topo]);

  // Search: keep matches plus their ancestors (so you can see where they hang).
  const visible = useMemo(() => {
    const term = q.trim().toLowerCase();
    if (!term && !infraOnly) return null;
    const keep = new Set<string>();
    for (const n of topo?.nodes ?? []) {
      const infra = n.type === "switch" || n.type === "gateway";
      if (infraOnly && !infra && !(n.is_online === false && !n.dormant)) continue;
      const hay = `${n.name} ${n.model ?? ""} ${n.ip ?? ""} ${n.mac ?? ""}`.toLowerCase();
      if (term && !hay.includes(term)) continue;
      let cur: TopoNode | undefined = n;
      const seen = new Set<string>();
      while (cur && !seen.has(cur.id)) {
        seen.add(cur.id); keep.add(cur.id);
        cur = cur.parent_id ? byId.get(cur.parent_id) : undefined;
      }
    }
    return keep;
  }, [q, infraOnly, topo, byId]);

  if (err && !topo) return <section className="panel"><div className="banner err">Network map unavailable: {err}</div></section>;
  if (!topo) return <section className="panel"><span className="hint">Building network map…</span></section>;
  if (!topo.topology_at) {
    return (
      <section className="panel">
        <div className="panel-title">Network map</div>
        <p className="hint">No uplink data for this site yet — it fills in within a minute of the console being reachable.</p>
      </section>
    );
  }

  const countBelow = (id: string): number => {
    let c = 0;
    const stack = [...(kids.get(id) ?? [])];
    const seen = new Set<string>();
    while (stack.length) {
      const n = stack.pop()!;
      if (seen.has(n.id)) continue;
      seen.add(n.id); c++;
      stack.push(...(kids.get(n.id) ?? []));
    }
    return c;
  };

  const renderRow = (n: TopoNode, depth: number, seen: Set<string>): JSX.Element | null => {
    if (seen.has(n.id)) return null;
    seen.add(n.id);
    if (visible && !visible.has(n.id)) return null;
    const children = (kids.get(n.id) ?? []).filter((c) => !visible || visible.has(c.id));
    const isCollapsed = collapsed.has(n.id) && !visible;
    const st = state(n);
    const parent = n.parent_id ? byId.get(n.parent_id) : undefined;
    const root = n.outage_root_id && n.outage_root_id !== n.id ? byId.get(n.outage_root_id) : undefined;
    const isRoot = n.outage_root_id === n.id;
    const toggle = () => setCollapsed((s) => {
      const nx = new Set(s); if (nx.has(n.id)) nx.delete(n.id); else nx.add(n.id); return nx;
    });
    return (
      <div key={n.id}>
        <div
          ref={focus === n.id ? focusRef : undefined}
          className={`nm-row st-${st} ${isRoot ? "nm-root" : ""} ${focus === n.id ? "nm-focus" : ""} ${n.type === "switch" || n.type === "gateway" ? "nm-infra" : ""}`}
          style={{ paddingLeft: 8 + depth * 22 }}
          onClick={() => setFocus(n.id)}
        >
          {children.length ? (
            <button className="nm-twisty" onClick={(e) => { e.stopPropagation(); toggle(); }} aria-label="toggle">
              {isCollapsed ? "▸" : "▾"}
            </button>
          ) : <span className="nm-twisty" />}
          <span className="nm-dot" />
          <span className="nm-name">{n.name || n.mac}</span>
          {n.type ? <span className="nm-type">{TYPE_LABEL[n.type] ?? n.type}</span> : null}
          {parent ? <span className="nm-link">{linkText(n, parent)}</span> : null}
          {isRoot ? <span className="nm-badge bad">ROOT CAUSE{countBelow(n.id) ? ` · ${countBelow(n.id)} behind` : ""}</span> : null}
          {root ? <span className="nm-badge">behind {root.name}</span> : null}
          {st === "down" && n.down_seconds != null ? <span className="nm-meta bad">down {humanizeDuration(n.down_seconds)}</span> : null}
          {st === "unreach" ? <span className="nm-meta warn">no ping from kiosks</span> : null}
          {st === "dormant" ? <span className="nm-meta">dormant</span> : null}
          <span className="nm-spacer" />
          {n.ports_total ? <span className="nm-meta">{n.ports_up}/{n.ports_total} ports up</span> : null}
          {n.ip ? <span className="nm-meta mono">{n.ip}</span> : null}
          {isCollapsed && children.length ? <span className="nm-meta">+{countBelow(n.id)}</span> : null}
        </div>
        {!isCollapsed ? children.map((c) => renderRow(c, depth + 1, seen)) : null}
      </div>
    );
  };

  const seen = new Set<string>();
  const switches = topo.nodes.filter((n) => n.type === "switch" && !n.dormant);
  return (
    <section className="panel">
      <div className="nm-toolbar">
        <div className="panel-title" style={{ margin: 0 }}>Network map</div>
        <span className="sub" style={{ fontSize: 12 }}>
          {switches.filter((s) => s.is_online).length}/{switches.length} switches up · built from UniFi uplinks
        </span>
        <div className="nm-spacer" />
        <button className={`chip ${mode === "tree" ? "active" : ""}`} onClick={() => setMode("tree")}>Tree</button>
        <button className={`chip ${mode === "diagram" ? "active" : ""}`} onClick={() => setMode("diagram")}>Diagram</button>
        {mode === "tree" ? (
          <>
            <button className={`chip ${infraOnly ? "active" : ""}`} onClick={() => setInfraOnly((v) => !v)}>Switches only</button>
            <input className="search" type="text" placeholder="Find device, IP, MAC…" value={q} onChange={(e) => setQ(e.target.value)} />
          </>
        ) : null}
      </div>

      {topo.outages.length ? (
        <div className="nm-outages">
          {topo.outages.map((o) => (
            <div key={o.root_id} className="nm-outage" onClick={() => { setMode("tree"); setFocus(o.root_id); }}>
              <strong>{o.root_name}</strong>{o.affected_count > 1 ? ` + ${o.affected_count - 1} behind it` : ""} — {" "}
              {o.parent_name
                ? <>plugs into <strong>{o.parent_name}</strong>{o.uplink_type === "wireless" ? " (wireless)" : o.uplink_port ? ` port ${o.uplink_port}` : ""}</>
                : "no uplink on record"}
              <span className="sub" style={{ marginLeft: 8 }}>down {humanizeDuration(o.down_seconds)}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="nm-allgood">● No outages on this site.</div>
      )}

      {mode === "tree" ? (
        <div className="nm-tree">
          {roots.map((r) => renderRow(r, 0, seen))}
          {unmapped.length && (!visible || unmapped.some((u) => visible.has(u.id))) ? (
            <>
              <div className="nm-group">Not mapped yet (no uplink reported)</div>
              {unmapped.map((u) => renderRow(u, 0, seen))}
            </>
          ) : null}
        </div>
      ) : (
        <Diagram roots={roots} kids={kids} onPick={(id) => { setMode("tree"); setFocus(id); }} />
      )}
    </section>
  );
}

/** Left-to-right tree of gateways + switches. APs/clients collapse into a count. */
function Diagram({ roots, kids, onPick }: { roots: TopoNode[]; kids: Kids; onPick: (id: string) => void }) {
  const W = 200, H = 34, GAPX = 44, GAPY = 8;
  const infra = (n: TopoNode) => n.type === "switch" || n.type === "gateway";
  type P = { n: TopoNode; x: number; y: number; leaves: number; parent?: P; aps: number; apsDown: number };
  const placed: P[] = [];
  let row = 0;
  const seen = new Set<string>();
  const place = (n: TopoNode, depth: number, parent?: P): P => {
    seen.add(n.id);
    const all = kids.get(n.id) ?? [];
    const others = all.filter((c) => !infra(c));
    const p: P = { n, x: depth * (W + GAPX), y: 0, leaves: 0, parent, aps: others.length,
      apsDown: others.filter((c) => c.is_online === false && !c.dormant).length };
    placed.push(p);
    const ch = all.filter((c) => infra(c) && !seen.has(c.id) && !(c.dormant && !(kids.get(c.id) ?? []).length));
    if (!ch.length) { p.y = row++ * (H + GAPY); return p; }
    const ys = ch.map((c) => place(c, depth + 1, p).y);
    p.y = Math.min(...ys);   // top-aligned with its first child: compact for daisy chains
    return p;
  };
  roots.forEach((r) => place(r, 0));
  const width = Math.max(...placed.map((p) => p.x)) + W + 20;
  const height = row * (H + GAPY) + 10;
  const color = (n: TopoNode) => {
    const s = state(n);
    return s === "down" ? "var(--critical)" : s === "dormant" ? "var(--ink-muted)" : s === "unreach" ? "var(--warning)" : "var(--good)";
  };
  return (
    <div className="nm-diagram">
      <svg width={width} height={height} role="img" aria-label="Network diagram">
        {placed.filter((p) => p.parent).map((p) => {
          const a = p.parent!, x1 = a.x + W, y1 = a.y + H / 2, x2 = p.x, y2 = p.y + H / 2, mx = x1 + GAPX / 2;
          const down = state(p.n) === "down";
          return (
            <g key={`e-${p.n.id}`}>
              <path d={`M${x1},${y1} H${mx} V${y2} H${x2}`} fill="none"
                stroke={down ? "var(--critical)" : "var(--baseline)"} strokeWidth={down ? 2 : 1.2}
                strokeDasharray={p.n.uplink_type === "wireless" ? "4 3" : undefined} />
              {p.n.uplink_port ? (
                <text x={x2 - 6} y={y2 - 4} textAnchor="end" fontSize="10" fill="var(--ink-muted)">p{p.n.uplink_port}</text>
              ) : null}
            </g>
          );
        })}
        {placed.map((p) => (
          <g key={p.n.id} transform={`translate(${p.x},${p.y})`} style={{ cursor: "pointer" }} onClick={() => onPick(p.n.id)}>
            <title>{`${p.n.name}${p.n.ip ? ` · ${p.n.ip}` : ""}${p.n.ports_total ? ` · ${p.n.ports_up}/${p.n.ports_total} ports up` : ""}`}</title>
            <rect width={W} height={H} rx={7} fill="var(--surface-1)" stroke={color(p.n)} strokeWidth={state(p.n) === "down" ? 2.5 : 1.2} />
            <rect width={5} height={H} rx={2} fill={color(p.n)} />
            <text x={12} y={14} fontSize="11.5" fontWeight={600} fill="var(--ink-primary)">
              {p.n.name.length > 27 ? p.n.name.slice(0, 26) + "…" : p.n.name}
            </text>
            <text x={12} y={27} fontSize="10" fill={p.apsDown ? "var(--critical)" : "var(--ink-muted)"}>
              {[p.n.type === "gateway" ? "gateway" : null,
                p.aps ? `${p.aps} AP/other${p.apsDown ? ` · ${p.apsDown} down` : ""}` : null,
                p.n.ports_total ? `${p.n.ports_up}/${p.n.ports_total} ports` : null].filter(Boolean).join(" · ")}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}
