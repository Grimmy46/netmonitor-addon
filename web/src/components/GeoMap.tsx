import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { api, isAdmin, type GeoState, type Site, type TopoNode } from "../api/client";
import { humanizeDuration } from "../lib/duration";
import { splitName } from "../lib/names";
import { getSkin, onSkinChange, type Skin } from "../lib/skin";

/**
 * MAP: the live network on a satellite photo of the lot.
 *  - pick the area (search the venue or pan/zoom, then "Save area")
 *  - Edit: drop every switch/AP where it physically sits (tap item → tap map,
 *    or drag it on desktop); drag pins to adjust
 *  - live: pins pulse on each UniFi heartbeat, go red when down; uplink lines
 *    connect each device to the switch feeding it; outages listed on top
 *  - Share: a view-only live link (no sign-in, no IPs/MACs)
 */

const POLL_MS = 10000;
const MAX_Z = 22;
const MISSED_AFTER_S = 120;

type Health = "up" | "missed" | "down" | "dormant" | "unreach";
function health(n: TopoNode): Health {
  if (n.dormant || n.closed_down) return "dormant";
  if (n.is_online === false) return "down";
  if (n.seen_age_s != null && n.seen_age_s > MISSED_AFTER_S) return "missed";
  if (n.local_reachable === false) return "unreach";
  return "up";
}
const isSwitch = (n: TopoNode) => n.type === "switch" || n.type === "gateway";
const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
const keyOf = (n: TopoNode) => (n.mac || n.id).toLowerCase();

const SAT = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const SAT_ATTR = "Imagery © Esri, Maxar, Earthstar Geographics";
const STREET = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const STREET_ATTR = "© OpenStreetMap contributors";

// ── Signed-in MAP tab ────────────────────────────────────────────────────────
export function MapTab() {
  const [sites, setSites] = useState<Site[]>([]);
  const [siteId, setSiteId] = useState("");
  useEffect(() => {
    api.sites().then((ss) => {
      setSites(ss);
      const main = ss.find((s) => s.name.toLowerCase() === "main");
      setSiteId((main ?? ss[0])?.id ?? "");
    }).catch(() => {});
  }, []);
  if (!siteId) return <div className="panel"><span className="hint">Loading sites…</span></div>;
  return <GeoMap key={siteId} siteId={siteId} sites={sites} onSite={setSiteId} />;
}

// ── Public view-only page (#/share/<token>) ──────────────────────────────────
export function SharedMapPage({ token }: { token: string }) {
  return (
    <div className="geo-share-page">
      <GeoMap shareToken={token} />
    </div>
  );
}

type Props = { siteId?: string; sites?: Site[]; onSite?: (id: string) => void; shareToken?: string };

function GeoMap({ siteId, sites, onSite, shareToken }: Props) {
  const shared = !!shareToken;
  const admin = !shared && isAdmin();
  const [skin, setSkin] = useState<Skin>(getSkin());
  useEffect(() => onSkinChange(setSkin), []);

  const [geo, setGeo] = useState<GeoState | null>(null);
  const [nodes, setNodes] = useState<TopoNode[]>([]);
  const [siteName, setSiteName] = useState("");
  const [err, setErr] = useState("");
  const [edit, setEdit] = useState(false);
  const [placing, setPlacing] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [base, setBase] = useState<"sat" | "street">("sat");
  const [shareOpen, setShareOpen] = useState(false);
  const [toast, setToast] = useState("");
  const [find, setFind] = useState("");
  const [drawer, setDrawer] = useState(false);
  const [zoom, setZoom] = useState(4);

  const boxRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const baseRef = useRef<L.TileLayer | null>(null);
  const markers = useRef(new Map<string, L.Marker>());
  const lines = useRef<L.LayerGroup | null>(null);
  const lastSeen = useRef(new Map<string, string | null>());
  const placingRef = useRef<string | null>(null);
  placingRef.current = placing;
  const fitted = useRef(false);

  const flash = (m: string) => { setToast(m); window.setTimeout(() => setToast(""), 2500); };

  // ── data ──
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        if (shared) {
          const s = await api.sharedMap(shareToken!);
          if (!alive) return;
          setSiteName(s.site_name);
          setGeo((g) => g ?? { center: s.center, zoom: s.zoom, placements: s.placements, share_token: null });
          setGeo((g) => (g ? { ...g, placements: s.placements } : g));
          setNodes(s.nodes);
        } else {
          const t = await api.siteTopology(siteId!);
          if (!alive) return;
          setSiteName(t.site_name);
          setNodes(t.nodes);
        }
        setErr("");
      } catch (e) {
        if (alive) setErr(e instanceof Error ? e.message : String(e));
      }
    };
    if (!shared) api.geo(siteId!).then((g) => alive && setGeo(g)).catch((e) => setErr(String(e)));
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, [siteId, shareToken, shared]);

  // ── map init ──
  useEffect(() => {
    if (!boxRef.current || mapRef.current) return;
    // Fine zoom: 0.1 snap, quarter steps on wheel/buttons, slider in the UI.
    const m = L.map(boxRef.current, {
      zoomControl: false, maxZoom: MAX_Z, minZoom: 3, attributionControl: true,
      zoomSnap: 0.1, zoomDelta: 0.25, wheelPxPerZoomLevel: 140, wheelDebounceTime: 20,
    });
    m.attributionControl.setPrefix(false);
    m.setView([39.5, -98.35], 4);
    baseRef.current = L.tileLayer(SAT, { maxNativeZoom: 19, maxZoom: MAX_Z, attribution: SAT_ATTR }).addTo(m);
    lines.current = L.layerGroup().addTo(m);
    const zoomCls = () => { boxRef.current?.classList.toggle("z-near", m.getZoom() >= 19); setZoom(m.getZoom()); };
    m.on("zoom", () => setZoom(m.getZoom()));
    m.on("zoomend", zoomCls);
    zoomCls();
    m.on("click", (e: L.LeafletMouseEvent) => {
      const mac = placingRef.current;
      if (!mac) return;
      pin(mac, e.latlng.lat, e.latlng.lng);
      setPlacing(null);
    });
    mapRef.current = m;
    return () => { m.remove(); mapRef.current = null; markers.current.clear(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Base layer switch.
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !baseRef.current) return;
    m.removeLayer(baseRef.current);
    baseRef.current = base === "sat"
      ? L.tileLayer(SAT, { maxNativeZoom: 19, maxZoom: MAX_Z, attribution: SAT_ATTR })
      : L.tileLayer(STREET, { maxNativeZoom: 19, maxZoom: MAX_Z, attribution: STREET_ATTR });
    baseRef.current.addTo(m);
    baseRef.current.bringToBack();
  }, [base]);

  // First view: saved area → else fit the pins.
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !geo || fitted.current) return;
    if (geo.center && geo.zoom) {
      m.setView(geo.center as [number, number], geo.zoom);
      fitted.current = true;
    } else {
      const pts = Object.values(geo.placements).map((p) => [p.lat, p.lng] as [number, number]);
      if (pts.length) { m.fitBounds(L.latLngBounds(pts).pad(0.2)); fitted.current = true; }
    }
  }, [geo]);

  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const pins = geo?.placements ?? {};

  async function pin(mac: string, lat: number, lng: number) {
    setGeo((g) => (g ? { ...g, placements: { ...g.placements, [mac]: { lat, lng } } } : g));
    try { await api.geoPin(siteId!, mac, lat, lng); } catch (e) { flash(`Couldn't save: ${e instanceof Error ? e.message : e}`); }
  }
  async function unpin(mac: string) {
    setGeo((g) => {
      if (!g) return g;
      const p = { ...g.placements };
      delete p[mac];
      return { ...g, placements: p };
    });
    mapRef.current?.closePopup();
    try { await api.geoUnpin(siteId!, mac); } catch { /* next load fixes it */ }
  }

  // Popup buttons (Leaflet HTML) → React actions.
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const h = (e: Event) => {
      const t = (e.target as HTMLElement).closest("[data-unpin]") as HTMLElement | null;
      if (t) unpin(t.dataset.unpin!);
    };
    el.addEventListener("click", h);
    return () => el.removeEventListener("click", h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [siteId]);

  // ── draw pins + uplink lines ──
  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;
    const seen = new Set<string>();
    for (const n of nodes) {
      const k = keyOf(n);
      const p = pins[k];
      if (!p) continue;
      seen.add(k);
      const h = health(n);
      const nm = splitName(n.name);
      const root = n.outage_root_id === n.id;
      const cls = `gp ${isSwitch(n) ? "gp-sw" : "gp-ap"} gp-${h}${root ? " gp-root" : ""}`;
      const html = `<div class="${cls}"><i class="gp-ring"></i><i class="gp-dot"></i><span class="gp-label">${esc(nm.label)}</span></div>`;
      const icon = L.divIcon({ className: "gp-wrap", html, iconSize: [26, 26], iconAnchor: [13, 13] });
      let mk = markers.current.get(k);
      if (!mk) {
        mk = L.marker([p.lat, p.lng], { icon, draggable: edit, riseOnHover: true, keyboard: false });
        mk.on("dragend", () => { const ll = mk!.getLatLng(); pin(k, ll.lat, ll.lng); });
        mk.addTo(m);
        markers.current.set(k, mk);
      } else {
        const cur = mk.getLatLng();
        if (Math.abs(cur.lat - p.lat) > 1e-9 || Math.abs(cur.lng - p.lng) > 1e-9) mk.setLatLng([p.lat, p.lng]);
        mk.setIcon(icon);
        if (edit) mk.dragging?.enable(); else mk.dragging?.disable();
      }
      mk.setZIndexOffset(h === "down" ? 1000 : isSwitch(n) ? 500 : 0);
      // Popup with details.
      const parent = n.parent_id ? byId.get(n.parent_id) : undefined;
      const rows: string[] = [];
      rows.push(`<b>${esc(nm.label)}</b>${nm.tag ? ` <span class="gpp-tag">#${nm.tag}</span>` : ""}`);
      rows.push(`<div class="gpp-sub">${esc([isSwitch(n) ? (n.type === "gateway" ? "Gateway" : "Switch") : "Access point", n.model].filter(Boolean).join(" · "))}</div>`);
      const st = n.closed_down ? "Switched off (closure)" : h === "down" ? `DOWN ${humanizeDuration(n.down_seconds) ?? ""}` : h === "missed" ? `Missed heartbeat (${humanizeDuration(n.seen_age_s)})` : h === "unreach" ? "Up, not answering pings" : h === "dormant" ? "Dormant" : `Up · beat ${Math.round(n.seen_age_s ?? 0)}s ago`;
      rows.push(`<div class="gpp-st gpp-${h}">${esc(st)}</div>`);
      if (parent) rows.push(`<div>Fed from <b>${esc(splitName(parent.name).label)}</b>${n.uplink_type === "wireless" ? " (mesh)" : n.uplink_port ? `, port ${n.uplink_port}` : ""}</div>`);
      if (root) {
        const behind = nodes.filter((x) => x.outage_root_id === n.id).length - 1;
        if (behind > 0) rows.push(`<div class="gpp-root">Root cause · ${behind} device${behind > 1 ? "s" : ""} behind it down</div>`);
      }
      if (n.ports_total) rows.push(`<div>Ports ${n.ports_up}/${n.ports_total} up</div>`);
      if (n.ip && !shared) rows.push(`<div class="gpp-sub">${esc(n.ip)}</div>`);
      if (edit) rows.push(`<button class="btn gpp-btn" data-unpin="${esc(k)}">Remove from map</button>`);
      mk.bindPopup(`<div class="gpp">${rows.join("")}</div>`, { closeButton: false, offset: [0, -8] });
      // Heartbeat pulse.
      const prev = lastSeen.current.get(k);
      if (prev !== undefined && prev !== n.last_seen && h === "up") {
        const el = mk.getElement()?.querySelector(".gp");
        if (el) { el.classList.remove("beat"); void (el as HTMLElement).offsetWidth; el.classList.add("beat"); }
      }
      lastSeen.current.set(k, n.last_seen);
    }
    for (const [k, mk] of markers.current) {
      if (!seen.has(k)) { mk.remove(); markers.current.delete(k); }
    }
    // Uplink lines between pinned devices.
    const lg = lines.current!;
    lg.clearLayers();
    for (const n of nodes) {
      const p = pins[keyOf(n)];
      const par = n.parent_id ? byId.get(n.parent_id) : undefined;
      const pp = par ? pins[keyOf(par)] : undefined;
      if (!p || !pp) continue;
      const down = health(n) === "down";
      L.polyline([[pp.lat, pp.lng], [p.lat, p.lng]], {
        className: `gl ${down ? "gl-down" : ""} ${n.uplink_type === "wireless" ? "gl-mesh" : ""} ${isSwitch(n) ? "gl-sw" : "gl-ap"}`,
        interactive: false,
      }).addTo(lg);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, pins, edit, byId, shared]);

  // ── derived ──
  const live = nodes.filter((n) => !n.dormant);
  const sw = live.filter(isSwitch);
  const aps = live.filter((n) => n.type === "ap");
  const roots = live.filter((n) => n.outage_root_id === n.id && !n.closed_down)
    .map((r) => ({ r, behind: live.filter((x) => x.outage_root_id === r.id).length - 1 }))
    .sort((a, b) => Number(isSwitch(b.r)) - Number(isSwitch(a.r)) || b.behind - a.behind);
  const unplaced = live.filter((n) => !pins[keyOf(n)] && (n.mac || n.id))
    .filter((n) => !q || `${n.name} ${n.ip ?? ""}`.toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => Number(isSwitch(b)) - Number(isSwitch(a)) || splitName(a.name).label.localeCompare(splitName(b.name).label));
  const placedCount = live.filter((n) => pins[keyOf(n)]).length;

  const goTo = (n: TopoNode) => {
    const p = pins[keyOf(n)];
    const mk = markers.current.get(keyOf(n));
    if (p && mapRef.current) {
      mapRef.current.flyTo([p.lat, p.lng], Math.max(mapRef.current.getZoom(), 19), { duration: 0.8 });
      window.setTimeout(() => mk?.openPopup(), 850);
    } else flash(`${splitName(n.name).label} isn't on the map yet${admin ? " — place it in Edit" : ""}.`);
  };

  async function searchPlace(e: React.FormEvent) {
    e.preventDefault();
    if (!find.trim()) return;
    try {
      const r = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(find)}`);
      const j = (await r.json()) as { lat: string; lon: string }[];
      if (!j.length) return flash("No match — try the fairground name + city.");
      mapRef.current?.flyTo([+j[0].lat, +j[0].lon], 17, { duration: 1 });
    } catch { flash("Search unavailable — pan/zoom by hand."); }
  }
  async function saveArea() {
    const m = mapRef.current!;
    const c = m.getCenter();
    try {
      const g = await api.geoView(siteId!, c.lat, c.lng, m.getZoom());
      setGeo((old) => ({ ...(old ?? g), center: g.center, zoom: g.zoom }));
      flash(`Area saved at zoom ${m.getZoom().toFixed(1)} — the map opens exactly here.`);
    } catch (e) { flash(`Couldn't save: ${e instanceof Error ? e.message : e}`); }
  }
  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    const mac = e.dataTransfer.getData("text/nm-mac");
    const m = mapRef.current;
    if (!mac || !m || !boxRef.current) return;
    const r = boxRef.current.getBoundingClientRect();
    const ll = m.containerPointToLatLng([e.clientX - r.left, e.clientY - r.top]);
    pin(mac, ll.lat, ll.lng);
  }

  const shareUrl = geo?.share_token ? `${window.location.origin}/#/share/${geo.share_token}` : "";

  return (
    <div className={`geo ${skin === "holo" ? "geo-holo" : "geo-pro"} ${edit ? "geo-edit" : ""} ${placing ? "geo-placing" : ""}`}>
      <div className="geo-bar">
        <strong className="geo-title">{siteName || "Map"}</strong>
        {shared ? <span className="geo-live-tag">● LIVE · view only</span> : null}
        {sites && sites.length > 1 && onSite ? (
          <select className="search geo-site" value={siteId} onChange={(e) => onSite(e.target.value)}>
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        ) : null}
        <span className="geo-stat">
          <b className={sw.some((n) => health(n) === "down") ? "bad" : "ok"}>{sw.filter((n) => health(n) !== "down").length}/{sw.length}</b> switches
          {" · "}<b className={aps.some((n) => health(n) === "down") ? "warn" : "ok"}>{aps.filter((n) => health(n) !== "down").length}/{aps.length}</b> APs
          {!shared ? <> · {placedCount}/{live.length} on map</> : null}
        </span>
        <div className="spacer" />
        {!shared ? (
          <form onSubmit={searchPlace} className="geo-find">
            <input className="search" placeholder="Find venue / address…" value={find} onChange={(e) => setFind(e.target.value)} />
          </form>
        ) : null}
        <div className="seg">
          <button className={base === "sat" ? "on" : ""} onClick={() => setBase("sat")}>Satellite</button>
          <button className={base === "street" ? "on" : ""} onClick={() => setBase("street")}>Street</button>
        </div>
        {admin ? (
          <>
            <button className={`btn ${edit ? "btn-primary" : ""}`} onClick={() => { setEdit((v) => !v); setPlacing(null); setDrawer(true); }}>
              {edit ? "Done" : "✎ Edit"}
            </button>
            <button className="btn" onClick={() => setShareOpen(true)}>Share</button>
            <a className="btn geo-classic" href={`/planner/index.html?embedded=1&site=${siteId}`} target="_blank" rel="noreferrer" title="The old SitePlanner (cable runs, measurements)">Classic planner ↗</a>
          </>
        ) : null}
      </div>

      {roots.length ? (
        <div className="geo-alerts">
          {roots.slice(0, 4).map(({ r, behind }) => (
            <button key={r.id} className="geo-alert" onClick={() => goTo(r)}>
              <span className="hb-static down" />
              <b>{isSwitch(r) ? "Switch down" : "AP down"}:</b> {splitName(r.name).label}
              {behind > 0 ? <span className="nm-count bad">+{behind} behind it</span> : null}
              <span className="sub"> · {humanizeDuration(r.down_seconds)}</span>
            </button>
          ))}
          {roots.length > 4 ? <span className="sub">+{roots.length - 4} more</span> : null}
        </div>
      ) : null}

      <div className="geo-body">
        {edit ? (
          <aside className={`geo-side ${drawer ? "open" : ""}`}>
            <div className="geo-side-head">
              <b>Place devices</b>
              <span className="sub">{unplaced.length} not on the map</span>
              <button className="btn geo-side-toggle" onClick={() => setDrawer((v) => !v)}>{drawer ? "▾" : "▴"}</button>
            </div>
            <button className="btn geo-save-area" onClick={saveArea} title="The map will open at exactly this view">📍 Save this view as the area</button>
            <input className="search" placeholder="Filter…" value={q} onChange={(e) => setQ(e.target.value)} />
            <p className="sub geo-help">{placing ? "Now tap the spot on the map." : "Tap a device, then tap where it sits. Drag pins to fine-tune."}</p>
            <div className="geo-list">
              {unplaced.map((n) => {
                const nm = splitName(n.name);
                const k = keyOf(n);
                return (
                  <button
                    key={n.id}
                    className={`geo-item ${placing === k ? "on" : ""} gi-${health(n)}`}
                    draggable
                    onDragStart={(e) => e.dataTransfer.setData("text/nm-mac", k)}
                    onClick={() => { setPlacing(placing === k ? null : k); if (window.innerWidth < 760) setDrawer(false); }}
                  >
                    <span className={`gi-dot ${isSwitch(n) ? "sw" : "ap"}`} />
                    <span className="gi-name">{nm.label}</span>
                    {nm.tag ? <span className="nm-tag">#{nm.tag}</span> : null}
                  </button>
                );
              })}
              {!unplaced.length ? <p className="sub" style={{ padding: 8 }}>Everything is on the map.</p> : null}
            </div>
          </aside>
        ) : null}
        <div className="geo-mapwrap">
          <div ref={boxRef} className="geo-map" onDragOver={(e) => edit && e.preventDefault()} onDrop={onDrop} />
          {placing ? (
            <div className="geo-placing-hint">
              Tap the map to place <b>{splitName(nodes.find((n) => keyOf(n) === placing)?.name).label}</b>
              <button className="btn" onClick={() => setPlacing(null)}>Cancel</button>
            </div>
          ) : null}
          {!geo?.center && !Object.keys(pins).length && !shared ? (
            <div className="geo-empty">
              <b>Set up this map</b>
              <span>1. Search the venue above (or pan/zoom to it).<br />2. Tap <b>✎ Edit</b> → <b>Save this view as the area</b>.<br />3. Tap each switch/AP, then tap where it sits.</span>
            </div>
          ) : null}
          <div className="geo-zoom" onDoubleClick={(e) => e.stopPropagation()}>
            <button className="btn" title="Zoom in" onClick={() => mapRef.current?.setZoom(Math.min(MAX_Z, +(zoom + 0.25).toFixed(2)))}>+</button>
            <input
              type="range" min={12} max={MAX_Z} step={0.05} value={zoom}
              onChange={(e) => mapRef.current?.setZoom(+e.target.value, { animate: false })}
              aria-label="Zoom"
            />
            <button className="btn" title="Zoom out" onClick={() => mapRef.current?.setZoom(Math.max(3, +(zoom - 0.25).toFixed(2)))}>−</button>
            <span className="geo-zoom-val">{zoom.toFixed(1)}×</span>
            {geo?.center && geo.zoom ? (
              <button className="btn" title="Back to the saved area" onClick={() => mapRef.current?.flyTo(geo.center as [number, number], geo.zoom!, { duration: 0.6 })}>⟲</button>
            ) : null}
          </div>
          <div className="geo-legend">
            <span><i className="lg up" />up</span><span><i className="lg missed" />missed beat</span>
            <span><i className="lg down" />down</span><span><i className="lg sq" />switch</span><span><i className="lg ci" />AP</span>
          </div>
          {toast ? <div className="geo-toast">{toast}</div> : null}
          {err ? <div className="geo-toast bad">{err}</div> : null}
        </div>
      </div>

      {shareOpen && admin ? (
        <div className="overlay" onClick={() => setShareOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginTop: 0 }}>View-only link</h3>
            <p className="sub" style={{ fontSize: 13 }}>
              Anyone with this link sees this live map — device names and status only (no IPs, no settings, no sign-in).
            </p>
            {shareUrl ? (
              <>
                <input className="search" style={{ width: "100%" }} readOnly value={shareUrl} onFocus={(e) => e.target.select()} />
                <div className="modal-actions" style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
                  <button className="btn btn-primary" onClick={() => { navigator.clipboard?.writeText(shareUrl); flash("Link copied."); }}>Copy link</button>
                  <button className="btn" onClick={async () => { const r = await api.geoShare(siteId!); setGeo((g) => g && { ...g, share_token: r.share_token }); }}>New link (old one stops)</button>
                  <button className="btn" onClick={async () => { await api.geoUnshare(siteId!); setGeo((g) => g && { ...g, share_token: null }); }}>Turn off link</button>
                </div>
              </>
            ) : (
              <button className="btn btn-primary" onClick={async () => { const r = await api.geoShare(siteId!); setGeo((g) => g && { ...g, share_token: r.share_token }); }}>
                Create link
              </button>
            )}
            <div style={{ textAlign: "right", marginTop: 12 }}>
              <button className="btn" onClick={() => setShareOpen(false)}>Close</button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
