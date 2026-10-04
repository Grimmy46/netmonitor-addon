import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { api, isAdmin, type GeoBg, type GeoState, type Site, type TopoNode } from "../api/client";
import { AffineImage, shrinkImage } from "../lib/affineImage";
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
const BG_Z = 20; // fixed zoom for image-geometry maths (pixels ≈ conformal locally)
const LABEL_SIZES = [12, 13.5, 15.5] as const;

type Pt = { x: number; y: number };
type BgGeom = { c: Pt; u: Pt; v: Pt }; // centre + half-width / half-height vectors (z20 px)

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
  const [base, setBase] = useState<"sat" | "street" | "none">("sat");
  const [shareOpen, setShareOpen] = useState(false);
  const [toast, setToast] = useState("");
  const [find, setFind] = useState("");
  const [drawer, setDrawer] = useState(false);
  const [zoom, setZoom] = useState(4);
  const [labelSize, setLabelSize] = useState<number>(() => Math.min(2, Math.max(0, Number(localStorage.getItem("nm-geo-lbl") ?? 1))));
  const [bgAdjust, setBgAdjust] = useState(false);
  const [bgBusy, setBgBusy] = useState(false);

  const boxRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const baseRef = useRef<L.TileLayer | null>(null);
  const markers = useRef(new Map<string, L.Marker>());
  const lines = useRef<L.LayerGroup | null>(null);
  const lastSeen = useRef(new Map<string, string | null>());
  const placingRef = useRef<string | null>(null);
  placingRef.current = placing;
  const fitted = useRef(false);
  const bgLayer = useRef<{ layer: AffineImage; version: number } | null>(null);
  const bgHandles = useRef<L.LayerGroup | null>(null);
  const bgGeom = useRef<BgGeom | null>(null);
  const layoutRef = useRef<() => void>(() => {});

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
          setGeo((g) => (g ? { ...g, placements: s.placements, bg: s.bg ?? null }
            : { center: s.center, zoom: s.zoom, placements: s.placements, share_token: null, bg: s.bg ?? null }));
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
    m.createPane("geoBg").style.zIndex = "250"; // above tiles, under lines/pins
    lines.current = L.layerGroup().addTo(m);
    bgHandles.current = L.layerGroup().addTo(m);
    m.on("moveend zoomend resize", () => layoutRef.current());
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
    // Toolbar wrapping / drawer opening resizes the box — keep Leaflet in sync.
    const ro = new ResizeObserver(() => m.invalidateSize({ pan: false }));
    ro.observe(boxRef.current);
    return () => { ro.disconnect(); m.remove(); mapRef.current = null; markers.current.clear(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Base layer switch.
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !baseRef.current) return;
    m.removeLayer(baseRef.current);
    boxRef.current?.classList.toggle("geo-nobase", base === "none");
    if (base === "none") { baseRef.current = L.tileLayer(""); return; }
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

  // ── AP auto-attach: put an AP right next to the switch feeding it ──
  /** Nearest pinned ancestor (the switch it hangs off, or the mesh parent). */
  function pinnedParent(n: TopoNode, pp: Record<string, { lat: number; lng: number }>): TopoNode | undefined {
    let p = n.parent_id ? byId.get(n.parent_id) : undefined;
    for (let i = 0; p && !pp[keyOf(p)] && i < 12; i++) p = p.parent_id ? byId.get(p.parent_id) : undefined;
    return p && pp[keyOf(p)] ? p : undefined;
  }
  /** Free spot on a ring around the parent (14 m, then 22 m, 30 m…). */
  function spotNear(par: TopoNode, pp: Record<string, { lat: number; lng: number }>): [number, number] {
    const c = pp[keyOf(par)];
    const mLat = 1 / 111320, mLng = 1 / (111320 * Math.cos((c.lat * Math.PI) / 180));
    const all = Object.values(pp);
    const dist = (a: number, b: number, p: { lat: number; lng: number }) => Math.hypot((a - p.lat) / mLat, (b - p.lng) / mLng);
    for (const r of [14, 22, 30, 40, 52]) {
      const steps = Math.round((2 * Math.PI * r) / 9);
      for (let i = 0; i < steps; i++) {
        const a = -Math.PI / 2 + (i * 2 * Math.PI) / steps; // start straight up, go clockwise
        const lat = c.lat - r * Math.sin(a) * mLat, lng = c.lng + r * Math.cos(a) * mLng;
        if (all.every((p) => dist(lat, lng, p) >= 8)) return [lat, lng];
      }
    }
    return [c.lat + 10 * mLat, c.lng + 10 * mLng];
  }
  function attachAp(n: TopoNode): boolean {
    const par = pinnedParent(n, pins);
    if (!par) return false;
    const [lat, lng] = spotNear(par, pins);
    pin(keyOf(n), lat, lng);
    flash(`${splitName(n.name).label} → next to ${splitName(par.name).label}. Drag to fine-tune.`);
    return true;
  }
  async function autoPlaceAps() {
    const pp = { ...pins };
    const todo: [string, number, number][] = [];
    // Shallow first so mesh children can follow their (just placed) parents.
    const cand = nodes.filter((n) => n.type === "ap" && !n.dormant && !pp[keyOf(n)])
      .sort((a, b) => (a.depth ?? 0) - (b.depth ?? 0));
    for (const n of cand) {
      const par = pinnedParent(n, pp);
      if (!par) continue;
      const [lat, lng] = spotNear(par, pp);
      pp[keyOf(n)] = { lat, lng };
      todo.push([keyOf(n), lat, lng]);
    }
    if (!todo.length) return flash("No APs to attach — place their switches first.");
    setGeo((g) => (g ? { ...g, placements: pp } : g));
    let bad = 0;
    for (const [k, lat, lng] of todo) { try { await api.geoPin(siteId!, k, lat, lng); } catch { bad++; } }
    flash(bad ? `Placed ${todo.length - bad}, ${bad} failed — try again.` : `Placed ${todo.length} APs next to their switches. Drag any to fine-tune.`);
  }

  // ── label layout: no overlaps, readable; hidden ones show on hover ──
  layoutRef.current = () => {
    const m = mapRef.current;
    if (!m) return;
    const z = m.getZoom();
    const showAps = z >= 18;
    const boxes: { x1: number; y1: number; x2: number; y2: number }[] = [];
    const items: { el: HTMLElement; lbl: HTMLElement; pt: L.Point; pri: number; sw: boolean }[] = [];
    for (const mk of markers.current.values()) {
      const el = mk.getElement()?.querySelector(".gp") as HTMLElement | null;
      const lbl = el?.querySelector(".gp-label") as HTMLElement | null;
      if (!el || !lbl) continue;
      const pt = m.latLngToContainerPoint(mk.getLatLng());
      const sw = el.classList.contains("gp-sw");
      const r = sw ? 10 : 7;
      boxes.push({ x1: pt.x - r, y1: pt.y - r, x2: pt.x + r, y2: pt.y + r }); // dots are obstacles
      const pri = (el.classList.contains("gp-down") ? 0 : el.classList.contains("gp-root") ? 0 : sw ? 1 : 2);
      lbl.classList.remove("lbl-hide");
      items.push({ el, lbl, pt, pri, sw });
    }
    items.sort((a, b) => a.pri - b.pri);
    const sz = m.getSize();
    const dims = items.map((it) => [it.lbl.offsetWidth, it.lbl.offsetHeight]); // one layout pass
    items.forEach((it, i) => {
      const [w, h] = dims[i];
      if (!it.sw && !showAps && it.pri > 0) { it.lbl.classList.add("lbl-hide"); return; }
      const g = it.sw ? 13 : 10;
      const cands: [number, number][] = [
        [-w / 2, g], [-w / 2, -g - h], [g, -h / 2], [-g - w, -h / 2],
        [g - 4, g - 2], [-w - g + 4, g - 2], [g - 4, -g - h + 2], [-w - g + 4, -g - h + 2],
      ];
      for (const [dx, dy] of cands) {
        const b = { x1: it.pt.x + dx - 2, y1: it.pt.y + dy - 1, x2: it.pt.x + dx + w + 2, y2: it.pt.y + dy + h + 1 };
        if (b.x2 < 0 || b.y2 < 0 || b.x1 > sz.x || b.y1 > sz.y) continue;
        if (boxes.some((o) => b.x1 < o.x2 && b.x2 > o.x1 && b.y1 < o.y2 && b.y2 > o.y1)) continue;
        boxes.push(b);
        it.lbl.style.left = `${13 + dx}px`;
        it.lbl.style.top = `${13 + dy}px`;
        return;
      }
      it.lbl.classList.add("lbl-hide");
      it.lbl.style.left = `${13 - w / 2}px`;
      it.lbl.style.top = `${13 + g}px`;
    });
  };
  useEffect(() => {
    localStorage.setItem("nm-geo-lbl", String(labelSize));
    boxRef.current?.style.setProperty("--gp-fs", `${LABEL_SIZES[labelSize]}px`);
    requestAnimationFrame(() => layoutRef.current());
  }, [labelSize]);

  // ── background image (plan / drone photo instead of the satellite) ──
  const bgUrl = geo?.bg
    ? shared ? `/map/shared/${encodeURIComponent(shareToken!)}/bg?v=${geo.bg.version}` : `/map/geo/${siteId}/bg?v=${geo.bg.version}`
    : "";
  const toPx = (ll: [number, number]) => mapRef.current!.project(L.latLng(ll[0], ll[1]), BG_Z);
  const toLL = (p: Pt) => mapRef.current!.unproject(L.point(p.x, p.y), BG_Z);
  const geomFromCorners = (c: GeoBg["corners"]): BgGeom => {
    const tl = toPx(c.tl), tr = toPx(c.tr), bl = toPx(c.bl);
    return { c: { x: (tr.x + bl.x) / 2, y: (tr.y + bl.y) / 2 }, u: { x: (tr.x - tl.x) / 2, y: (tr.y - tl.y) / 2 }, v: { x: (bl.x - tl.x) / 2, y: (bl.y - tl.y) / 2 } };
  };
  const cornersFromGeom = (g: BgGeom) => {
    const P = (sx: number, sy: number) => toLL({ x: g.c.x + sx * g.u.x + sy * g.v.x, y: g.c.y + sx * g.u.y + sy * g.v.y });
    return { tl: P(-1, -1), tr: P(1, -1), bl: P(-1, 1) };
  };
  const llPair = (l: L.LatLng): [number, number] => [l.lat, l.lng];

  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;
    if (!geo?.bg || !bgUrl) {
      if (bgLayer.current) { bgLayer.current.layer.remove(); bgLayer.current = null; }
      return;
    }
    const c = geo.bg.corners;
    const corners = { tl: L.latLng(c.tl[0], c.tl[1]), tr: L.latLng(c.tr[0], c.tr[1]), bl: L.latLng(c.bl[0], c.bl[1]) };
    if (bgLayer.current && bgLayer.current.version === geo.bg.version) {
      bgLayer.current.layer.setCorners(corners);
      bgLayer.current.layer.setOpacity(geo.bg.opacity);
    } else {
      bgLayer.current?.layer.remove();
      const layer = new AffineImage(bgUrl, corners, geo.bg.opacity, "geoBg");
      layer.addTo(m);
      bgLayer.current = { layer, version: geo.bg.version };
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bgUrl, JSON.stringify(geo?.bg ?? null)]);

  const saveBgTimer = useRef<number | undefined>(undefined);
  function commitBg(g: BgGeom, opacity?: number, save = true) {
    bgGeom.current = g;
    const cs = cornersFromGeom(g);
    bgLayer.current?.layer.setCorners(cs);
    const corners = { tl: llPair(cs.tl), tr: llPair(cs.tr), bl: llPair(cs.bl) } as GeoBg["corners"];
    let op = opacity;
    setGeo((old) => {
      if (!old?.bg) return old;
      op = op ?? old.bg.opacity;
      return { ...old, bg: { ...old.bg, corners, opacity: op } };
    });
    if (!save) return;
    window.clearTimeout(saveBgTimer.current);
    saveBgTimer.current = window.setTimeout(() => {
      api.geoBgMeta(siteId!, corners, op ?? geo?.bg?.opacity ?? 0.85).catch((e) => flash(`Couldn't save image position: ${e instanceof Error ? e.message : e}`));
    }, 400);
  }
  function setBgOpacity(o: number) {
    if (!geo?.bg) return;
    bgLayer.current?.layer.setOpacity(o);
    const g = bgGeom.current ?? geomFromCorners(geo.bg.corners);
    commitBg(g, o);
  }
  function rotateBg(deg: number) {
    if (!geo?.bg) return;
    const g = bgGeom.current ?? geomFromCorners(geo.bg.corners);
    const a = (deg * Math.PI) / 180, co = Math.cos(a), si = Math.sin(a);
    const rot = (p: Pt) => ({ x: p.x * co - p.y * si, y: p.x * si + p.y * co });
    commitBg({ c: g.c, u: rot(g.u), v: rot(g.v) });
    drawBgHandles();
  }
  function scaleBg(f: number) {
    if (!geo?.bg) return;
    const g = bgGeom.current ?? geomFromCorners(geo.bg.corners);
    commitBg({ c: g.c, u: { x: g.u.x * f, y: g.u.y * f }, v: { x: g.v.x * f, y: g.v.y * f } });
    drawBgHandles();
  }

  /** Handles: ✥ centre = move · ⤡ corner = rotate + resize · ↔ / ↕ = stretch. */
  function drawBgHandles() {
    const m = mapRef.current, hg = bgHandles.current;
    if (!m || !hg) return;
    hg.clearLayers();
    if (!bgAdjust || !geo?.bg) return;
    const g0 = bgGeom.current ?? geomFromCorners(geo.bg.corners);
    bgGeom.current = g0;
    const at = (g: BgGeom, sx: number, sy: number) => toLL({ x: g.c.x + sx * g.u.x + sy * g.v.x, y: g.c.y + sx * g.u.y + sy * g.v.y });
    const outline = L.polygon([at(g0, -1, -1), at(g0, 1, -1), at(g0, 1, 1), at(g0, -1, 1)], { className: "geo-bg-outline", interactive: false }).addTo(hg);
    const mk = (sx: number, sy: number, glyph: string, cls: string, title: string) =>
      L.marker(at(g0, sx, sy), { draggable: true, title, icon: L.divIcon({ className: `geo-h ${cls}`, html: glyph, iconSize: [30, 30], iconAnchor: [15, 15] }), zIndexOffset: 3000 }).addTo(hg);
    const hc = mk(0, 0, "✥", "geo-h-move", "Drag to move the image");
    const hr = mk(1, 1, "⤡", "geo-h-rot", "Drag to rotate + resize");
    const hw = mk(1, 0, "↔", "geo-h-str", "Drag to stretch width");
    const hh = mk(0, 1, "↕", "geo-h-str", "Drag to stretch height");
    let start: BgGeom = g0;
    const begin = () => { start = bgGeom.current!; };
    const place = (g: BgGeom) => {
      outline.setLatLngs([at(g, -1, -1), at(g, 1, -1), at(g, 1, 1), at(g, -1, 1)]);
      hc.setLatLng(at(g, 0, 0)); hr.setLatLng(at(g, 1, 1)); hw.setLatLng(at(g, 1, 0)); hh.setLatLng(at(g, 0, 1));
    };
    const live = (g: BgGeom, done: boolean) => { place(g); commitBg(g, undefined, done); };
    const P = (h: L.Marker) => { const p = m.project(h.getLatLng(), BG_Z); return { x: p.x, y: p.y }; };
    for (const h of [hc, hr, hw, hh]) h.on("dragstart", begin);
    const onMove = (g: () => BgGeom) => (e: L.LeafletEvent) => live(g(), e.type === "dragend");
    const moveG = () => ({ ...start, c: P(hc) });
    const rotG = () => {
      const p = P(hr), d0 = { x: start.u.x + start.v.x, y: start.u.y + start.v.y }, d = { x: p.x - start.c.x, y: p.y - start.c.y };
      const s = Math.max(0.05, Math.hypot(d.x, d.y) / Math.max(1, Math.hypot(d0.x, d0.y)));
      const a = Math.atan2(d.y, d.x) - Math.atan2(d0.y, d0.x), co = Math.cos(a) * s, si = Math.sin(a) * s;
      const rot = (q: Pt) => ({ x: q.x * co - q.y * si, y: q.x * si + q.y * co });
      return { c: start.c, u: rot(start.u), v: rot(start.v) };
    };
    const strG = (axis: "u" | "v", h: L.Marker) => () => {
      const p = P(h), vec = start[axis], len = Math.max(1, Math.hypot(vec.x, vec.y));
      const proj = Math.max(4, ((p.x - start.c.x) * vec.x + (p.y - start.c.y) * vec.y) / len);
      return { ...start, [axis]: { x: (vec.x / len) * proj, y: (vec.y / len) * proj } } as BgGeom;
    };
    hc.on("drag dragend", onMove(moveG));
    hr.on("drag dragend", onMove(rotG));
    hw.on("drag dragend", onMove(strG("u", hw)));
    hh.on("drag dragend", onMove(strG("v", hh)));
  }
  useEffect(() => {
    if (!bgAdjust) bgGeom.current = null;
    drawBgHandles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bgAdjust, geo?.bg?.version]);
  useEffect(() => { if (!edit) setBgAdjust(false); }, [edit]);

  async function uploadBg(file: File | undefined) {
    const m = mapRef.current;
    if (!file || !m) return;
    setBgBusy(true);
    try {
      const blob = await shrinkImage(file);
      // Natural size → initial fit: ~70% of the current view, aspect kept.
      const dim = await new Promise<[number, number]>((res) => {
        const u = URL.createObjectURL(blob), i = new Image();
        i.onload = () => { res([i.naturalWidth || 1, i.naturalHeight || 1]); URL.revokeObjectURL(u); };
        i.onerror = () => res([4, 3]);
        i.src = u;
      });
      const g = await api.geoBgUpload(siteId!, blob);
      const b = m.getBounds(), nw = m.project(b.getNorthWest(), BG_Z), se = m.project(b.getSouthEast(), BG_Z);
      const vw = se.x - nw.x, vh = se.y - nw.y, asp = dim[0] / dim[1];
      let hw = 0.35 * vw, hh = hw / asp;
      if (hh > 0.35 * vh) { hh = 0.35 * vh; hw = hh * asp; }
      const cpx = m.project(m.getCenter(), BG_Z);
      const geom: BgGeom = { c: { x: cpx.x, y: cpx.y }, u: { x: hw, y: 0 }, v: { x: 0, y: hh } };
      const cs = cornersFromGeom(geom);
      const corners = { tl: llPair(cs.tl), tr: llPair(cs.tr), bl: llPair(cs.bl) } as GeoBg["corners"];
      const g2 = await api.geoBgMeta(siteId!, corners, 0.85);
      setGeo((old) => ({ ...(old ?? g), bg: g2.bg ?? null }));
      bgGeom.current = geom;
      setBgAdjust(true);
      flash("Image added. Drag ✥ to move, ⤡ to rotate/resize, ↔ ↕ to stretch.");
    } catch (e) {
      flash(`Upload failed: ${e instanceof Error ? e.message : e}`);
    } finally { setBgBusy(false); }
  }
  async function removeBg() {
    if (!window.confirm("Remove the background image?")) return;
    try {
      await api.geoBgDelete(siteId!);
      setBgAdjust(false);
      setGeo((old) => (old ? { ...old, bg: null } : old));
      if (base === "none") setBase("sat");
    } catch (e) { flash(`Couldn't remove: ${e instanceof Error ? e.message : e}`); }
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
    requestAnimationFrame(() => layoutRef.current());
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
  const apsAttachable = live.filter((n) => n.type === "ap" && !pins[keyOf(n)] && pinnedParent(n, pins)).length;

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
          {geo?.bg ? <button className={base === "none" ? "on" : ""} onClick={() => setBase("none")} title="Only your image, no satellite">Image</button> : null}
        </div>
        <button className="btn geo-lbl-btn" title="Label size" onClick={() => setLabelSize((v) => (v + 1) % 3)}>
          Aa <span className="sub">{["S", "M", "L"][labelSize]}</span>
        </button>
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
            <p className="sub geo-help">{placing ? "Now tap the spot on the map." : "Tap a switch, then tap where it sits. Tap an AP and it snaps next to its switch. Drag pins to fine-tune."}</p>
            {apsAttachable > 0 ? (
              <button className="btn btn-primary geo-auto" onClick={autoPlaceAps}>⚡ Auto-place {apsAttachable} AP{apsAttachable > 1 ? "s" : ""} next to their switches</button>
            ) : null}
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
                    onClick={() => {
                      // APs snap next to the switch feeding them (if it's on the map).
                      if (n.type === "ap" && placing !== k && attachAp(n)) { setPlacing(null); return; }
                      setPlacing(placing === k ? null : k);
                      if (window.innerWidth < 760) setDrawer(false);
                    }}
                  >
                    <span className={`gi-dot ${isSwitch(n) ? "sw" : "ap"}`} />
                    <span className="gi-name">{nm.label}</span>
                    {nm.tag ? <span className="nm-tag">#{nm.tag}</span> : null}
                  </button>
                );
              })}
              {!unplaced.length ? <p className="sub" style={{ padding: 8 }}>Everything is on the map.</p> : null}
            </div>
            <div className="geo-bgbox">
              <b>Background image</b>
              <span className="sub">Site plan or drone photo instead of the satellite.</span>
              <label className={`btn ${bgBusy ? "disabled" : ""}`}>
                {bgBusy ? "Uploading…" : geo?.bg ? "Replace image…" : "＋ Add image…"}
                <input type="file" accept="image/*" hidden disabled={bgBusy} onChange={(e) => { uploadBg(e.target.files?.[0]); e.target.value = ""; }} />
              </label>
              {geo?.bg ? (
                <>
                  <button className={`btn ${bgAdjust ? "btn-primary" : ""}`} onClick={() => setBgAdjust((v) => !v)}>{bgAdjust ? "✓ Done adjusting" : "✥ Adjust / fit image"}</button>
                  {bgAdjust ? (
                    <div className="geo-bg-fine">
                      <button className="btn" title="Rotate left 1°" onClick={() => rotateBg(-1)}>⟲ 1°</button>
                      <button className="btn" title="Rotate right 1°" onClick={() => rotateBg(1)}>⟳ 1°</button>
                      <button className="btn" title="Smaller" onClick={() => scaleBg(0.98)}>−</button>
                      <button className="btn" title="Bigger" onClick={() => scaleBg(1.02)}>＋</button>
                    </div>
                  ) : null}
                  <label className="geo-bg-op">Opacity
                    <input type="range" min={0.1} max={1} step={0.05} value={geo.bg.opacity} onChange={(e) => setBgOpacity(+e.target.value)} />
                  </label>
                  <button className="btn" onClick={removeBg}>Remove image</button>
                </>
              ) : null}
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
