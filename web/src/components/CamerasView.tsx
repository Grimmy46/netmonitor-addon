import { useEffect, useRef, useState } from "react";
import { API_BASE, api, camWsUrl, isAdmin, type Agent, type Camera, type CamList, type PaperInfo, type PaperRoi } from "../api/client";

// go2rtc's MIT VideoRTC player (public/vendor) — MSE over a WebSocket, which
// works on desktop browsers and iPhone (Safari 17+ ManagedMediaSource).
let playerReady: Promise<void> | null = null;
type VRTC = { new (): HTMLElement & { video: HTMLVideoElement; oninit(): void } };
function loadPlayer(): Promise<void> {
  if (!playerReady) {
    // Loaded as a plain module <script> (not bundled): it's a vendored file.
    playerReady = new Promise<void>((resolve, reject) => {
      const w = window as unknown as { __VideoRTC?: VRTC };
      const done = () => {
        const Base = w.__VideoRTC;
        if (!Base) return reject(new Error("video player failed to load"));
        if (!customElements.get("nm-cam")) {
          class NmCam extends Base {
            oninit() {
              super.oninit();
              this.video.muted = true; // phones only autoplay muted video
            }
          }
          customElements.define("nm-cam", NmCam as unknown as CustomElementConstructor);
        }
        resolve();
      };
      window.addEventListener("nm-videortc", done, { once: true });
      const s = document.createElement("script");
      s.type = "module";
      s.textContent = "import { VideoRTC } from '/vendor/video-rtc.js'; window.__VideoRTC = VideoRTC; window.dispatchEvent(new Event('nm-videortc'));";
      s.onerror = () => reject(new Error("video player failed to load"));
      document.head.appendChild(s);
    });
  }
  return playerReady;
}

type CamEl = HTMLElement & { mode: string; src: string; background: boolean; visibilityCheck: boolean; video?: HTMLVideoElement;
  connectedCallback(): void; disconnectedCallback(): void };
// Keep streams open while the browser tab is hidden, up to this long.
const HIDDEN_KEEP_MS = 10 * 60 * 1000;

// Last still per camera: shown the instant the tab opens, until live video
// has frames. Kept in memory (tab switches) and localStorage (reloads).
type Snap = { t: number; url: string };
const snaps = new Map<string, Snap>();
const SNAP_KEY = (id: string) => `nm-cam-snap-${id}`;
function getSnap(id: string): Snap | null {
  const m = snaps.get(id);
  if (m) return m;
  try {
    const v = JSON.parse(localStorage.getItem(SNAP_KEY(id)) || "null") as Snap | null;
    if (v?.url) { snaps.set(id, v); return v; }
  } catch { /* ignore */ }
  return null;
}
function grab(id: string, v: HTMLVideoElement | undefined): void {
  if (!v || v.readyState < 2 || !v.videoWidth) return;
  try {
    const w = Math.min(640, v.videoWidth), h = Math.round((w * v.videoHeight) / v.videoWidth);
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    c.getContext("2d")!.drawImage(v, 0, 0, w, h);
    const snap = { t: Date.now(), url: c.toDataURL("image/jpeg", 0.7) };
    snaps.set(id, snap);
    try { localStorage.setItem(SNAP_KEY(id), JSON.stringify(snap)); } catch { /* storage full */ }
  } catch { /* canvas blocked */ }
}
function ago(t: number): string {
  const m = Math.round((Date.now() - t) / 60000);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
}

function CamPlayer({ cam, share, playing }: { cam: Camera; share?: string; playing: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState("");
  const [live, setLive] = useState(false);
  const [snap, setSnap] = useState<Snap | null>(() => getSnap(cam.id));
  useEffect(() => {
    setLive(false);
    setSnap(getSnap(cam.id));
    if (!playing || !box.current) return;
    let el: CamEl | null = null;
    let alive = true;
    let tick: number | undefined;
    let lastGrab = 0;
    loadPlayer().then(() => {
      if (!alive || !box.current) return;
      el = document.createElement("nm-cam") as CamEl;
      el.mode = "mse";
      el.className = "cam-video";
      el.visibilityCheck = false; // don't drop the stream on a browser-tab switch…
      box.current.appendChild(el);
      el.src = camWsUrl(cam.id, share);
      tick = window.setInterval(() => {
        const v = el?.video;
        const ok = !!v && v.readyState >= 2 && v.videoWidth > 0 && v.currentTime > 0;
        if (ok) {
          setLive(true);
          if (Date.now() - lastGrab > 15000) { lastGrab = Date.now(); grab(cam.id, v); }
        }
      }, 500);
    }).catch((e) => setErr(String(e)));
    // …but if the tab stays hidden for 10 min, let it go; it reconnects on return.
    let hideTid: number | undefined;
    let slept = false;
    const onVis = () => {
      if (document.hidden) {
        grab(cam.id, el?.video);
        hideTid = window.setTimeout(() => { slept = true; el?.disconnectedCallback(); }, HIDDEN_KEEP_MS);
      } else {
        window.clearTimeout(hideTid);
        if (slept && el?.isConnected) { slept = false; setLive(false); setSnap(getSnap(cam.id)); el.connectedCallback(); }
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", onVis);
      window.clearTimeout(hideTid);
      window.clearInterval(tick);
      grab(cam.id, el?.video); // freshest still for next time
      el?.remove();
    };
  }, [cam.id, share, playing]);
  const showSnap = !!snap && (!playing || !live);
  return (
    <div className="cam-frame" ref={box}>
      {showSnap ? <img className="cam-snap" src={snap!.url} alt="" /> : null}
      {showSnap ? <span className="cam-snap-tag">{playing ? "Connecting… · " : "Paused · "}still from {ago(snap!.t)}</span> : null}
      {!playing && !snap ? <div className="cam-idle">Paused</div> : null}
      {err ? <div className="cam-idle">{err}</div> : null}
    </div>
  );
}

export function CamerasView({ share }: { share?: string }) {
  const [data, setData] = useState<CamList | null>(null);
  const [err, setErr] = useState("");
  const [playing, setPlaying] = useState(true);
  const [big, setBig] = useState<string | null>(null);
  const [setup, setSetup] = useState(false);
  const [paperCam, setPaperCam] = useState<Camera | null>(null);
  const admin = !share && isAdmin();

  const load = () => api.cams(share).then(setData).catch((e) => setErr(String(e)));
  useEffect(() => {
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [share]);

  const cams = (data?.cameras ?? []).filter((c) => c.enabled);
  const relay = data?.relay;
  const shown = big ? cams.filter((c) => c.id === big) : cams;

  return (
    <div className="cams">
      <div className="cams-bar">
        <strong>Cameras</strong>
        <span className="muted">{cams.length} camera{cams.length === 1 ? "" : "s"}</span>
        {relay ? (
          <span className={`cams-relay ${relay.polling ? "ok" : "bad"}`}>
            ● Relay {relay.name ?? "not set"}{relay.polling ? "" : " · not connected"}
          </span>
        ) : null}
        <span style={{ flex: 1 }} />
        {big ? <button className="btn" onClick={() => setBig(null)}>◀ All cameras</button> : null}
        <button className="btn" onClick={() => setPlaying((p) => !p)}>{playing ? "⏸ Pause all" : "▶ Play all"}</button>
        {admin ? <button className="btn" onClick={() => setSetup((s) => !s)}>⚙ Setup</button> : null}
      </div>
      {err ? <div className="error-box">{err}</div> : null}
      {admin && setup && data ? <CamSetup data={data} onSaved={load} /> : null}
      {admin && paperCam ? <PaperEditor cam={paperCam} onClose={() => { setPaperCam(null); load(); }} /> : null}
      {admin && relay && !relay.login_set ? (
        <div className="cams-note">Add the camera login under ⚙ Setup to start video.</div>
      ) : null}
      {data && !cams.length ? <div className="cams-note">No cameras found yet. They show up here a few minutes after they join the show Wi-Fi.</div> : null}
      <div className={`cams-grid ${big ? "cams-one" : ""}`}>
        {shown.map((c) => (
          <div key={c.id} className="cam-tile">
            <div className="cam-head" onClick={() => setBig(big ? null : c.id)} title="Tap to enlarge">
              <span className={`cam-dot ${c.live ? "on" : ""}`} />
              <b>{c.name}</b>
              {c.ap_name && !share ? <span className="muted cam-ap">{c.ap_name}</span> : null}
              {!share && c.paper?.length ? (
                <span className="cam-paper">
                  {c.paper.map((p) => (
                    <span key={p.agent_id} className={`cam-paper-b ${p.level == null ? "" : p.level < 15 ? "bad" : p.level < 35 ? "warn" : "ok"}`}
                      title={`${p.name}: camera sees ${p.level == null ? "—" : `~${p.level}%`} paper`}>
                      🧻 {p.name?.replace(/^K\d+-/, "")} {p.level == null ? "—" : `${p.level}%`}
                    </span>
                  ))}
                </span>
              ) : null}
              {admin ? (
                <button className="btn cam-paper-btn" onClick={(e) => { e.stopPropagation(); setPaperCam(c); }} title="Mark the paper rolls this camera can see">🧻 Paper</button>
              ) : null}
            </div>
            <CamPlayer cam={c} share={share} playing={playing} />
          </div>
        ))}
      </div>
    </div>
  );
}

function CamSetup({ data, onSaved }: { data: CamList; onSaved: () => void }) {
  const relay = data.relay!;
  const [user, setUser] = useState(relay.username ?? "");
  const [pw, setPw] = useState("");
  const [kiosks, setKiosks] = useState<Agent[]>([]);
  const [relayId, setRelayId] = useState(relay.preferred_id ?? "");
  const [msg, setMsg] = useState("");
  useEffect(() => {
    api.agents().then((a) => setKiosks(a.filter((x) => x.station_group === "kiosk").sort(
      (p, q) => p.name.localeCompare(q.name, undefined, { numeric: true })))).catch(() => {});
  }, []);
  const save = async () => {
    setMsg("Saving…");
    try {
      await api.camConfig({ username: user, ...(pw ? { password: pw } : {}), ...(relayId ? { relay_agent_id: relayId } : {}) });
      setPw("");
      setMsg("Saved");
      onSaved();
    } catch (e) { setMsg(String(e)); }
  };
  return (
    <div className="cams-setup">
      <div className="cams-setup-row">
        <label>Camera username<input className="search" value={user} onChange={(e) => setUser(e.target.value)} autoComplete="off" /></label>
        <label>Camera password<input className="search" type="password" value={pw} placeholder={relay.login_set ? "saved — type to change" : ""} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" /></label>
        <label>Relay kiosk
          <select className="search" value={relayId} onChange={(e) => setRelayId(e.target.value)}>
            <option value="">— choose —</option>
            {kiosks.map((k) => <option key={k.id} value={k.id}>{k.name}{k.online ? "" : " (offline)"}</option>)}
          </select>
        </label>
        <button className="btn btn-primary" onClick={save}>Save</button>
        <span className="muted">{msg}</span>
      </div>
      <p className="muted" style={{ margin: "6px 0" }}>
        The relay kiosk pulls the cameras' low-res stream and sends it here only while someone is watching.
        If it goes offline another kiosk at the same show takes over.
      </p>
      <table className="cams-table">
        <thead><tr><th>Show</th><th>Name</th><th>Near AP</th><th>IP</th></tr></thead>
        <tbody>
          {data.cameras.map((c) => <CamRow key={c.id} cam={c} onSaved={onSaved} />)}
        </tbody>
      </table>
    </div>
  );
}

function CamRow({ cam, onSaved }: { cam: Camera; onSaved: () => void }) {
  const [name, setName] = useState(cam.name);
  return (
    <tr>
      <td><input type="checkbox" checked={cam.enabled} onChange={(e) => api.camUpdate(cam.id, { enabled: e.target.checked }).then(onSaved)} /></td>
      <td><input className="search" value={name} onChange={(e) => setName(e.target.value)}
        onBlur={() => name !== cam.name && api.camUpdate(cam.id, { name }).then(onSaved)} /></td>
      <td className="muted">{cam.ap_name ?? "—"}</td>
      <td className="muted">{cam.ip ?? "—"}</td>
    </tr>
  );
}

// ── Paper vision: draw one box per ticket printer around its roll ─────────
function PaperEditor({ cam, onClose }: { cam: Camera; onClose: () => void }) {
  const [info, setInfo] = useState<PaperInfo | null>(null);
  const [rois, setRois] = useState<PaperRoi[]>([]);
  const [station, setStation] = useState("");
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const [msg, setMsg] = useState("");
  const [t, setT] = useState(Date.now());
  const imgBox = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.camPaper(cam.id).then((i) => {
      setInfo(i); setRois(i.rois);
      const free = i.stations.find((s) => !i.rois.some((r) => r.agent_id === s.id));
      setStation(free?.id ?? i.stations[0]?.id ?? "");
    }).catch((e) => setMsg(String(e)));
  }, [cam.id]);

  const pos = (e: React.PointerEvent) => {
    const r = imgBox.current!.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  };
  const down = (e: React.PointerEvent) => {
    if (!station) { setMsg("Pick a station first."); return; }
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    const p = pos(e); setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
  };
  const move = (e: React.PointerEvent) => { if (drag) { const p = pos(e); setDrag({ ...drag, x1: p.x, y1: p.y }); } };
  const up = () => {
    if (!drag) return;
    const x = Math.min(drag.x0, drag.x1), y = Math.min(drag.y0, drag.y1);
    const w = Math.abs(drag.x1 - drag.x0), h = Math.abs(drag.y1 - drag.y0);
    setDrag(null);
    if (w < 0.01 || h < 0.01) return;
    const name = info?.stations.find((s) => s.id === station)?.name;
    const next = [...rois.filter((r) => r.agent_id !== station), { agent_id: station, name, x, y, w, h }];
    setRois(next);
    const free = info?.stations.find((s) => !next.some((r) => r.agent_id === s.id));
    if (free) setStation(free.id);
    setMsg("");
  };
  const save = async () => {
    try { await api.camPaperSave(cam.id, rois); setMsg("Saved. Readings start within a few minutes."); }
    catch (e) { setMsg(String(e)); }
  };
  const box = (r: { x: number; y: number; w: number; h: number }) => ({
    left: `${r.x * 100}%`, top: `${r.y * 100}%`, width: `${r.w * 100}%`, height: `${r.h * 100}%`,
  });

  return (
    <div className="paper-ed">
      <div className="paper-ed-bar">
        <strong>🧻 Paper rolls · {cam.name}</strong>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={() => setT(Date.now())}>↻ New picture</button>
        <button className="btn" onClick={onClose}>Close</button>
      </div>
      <div className="muted paper-ed-help">
        Pick a station, then drag a box tightly around its paper roll (the white roll on the holder, not the spares). One box per printer. Then Save.
      </div>
      <div className="paper-ed-row">
        <select value={station} onChange={(e) => setStation(e.target.value)}>
          {(info?.stations ?? []).map((s) => (
            <option key={s.id} value={s.id}>{s.name}{rois.some((r) => r.agent_id === s.id) ? " ✓" : ""}</option>
          ))}
        </select>
        <button className="btn btn-primary" onClick={save}>Save</button>
        {msg ? <span className="muted">{msg}</span> : null}
      </div>
      <div className="paper-ed-img" ref={imgBox} onPointerDown={down} onPointerMove={move} onPointerUp={up}>
        <img src={`${API_BASE}/cams/${cam.id}/snapshot.jpg?t=${t}`} alt="" draggable={false} />
        {rois.map((r) => (
          <div key={r.agent_id} className="paper-ed-box" style={box(r)}>
            <span>{r.name?.replace(/^K\d+-/, "")}{r.level != null ? ` ${r.level}%` : ""}</span>
          </div>
        ))}
        {drag ? <div className="paper-ed-box drawing" style={box({ x: Math.min(drag.x0, drag.x1), y: Math.min(drag.y0, drag.y1), w: Math.abs(drag.x1 - drag.x0), h: Math.abs(drag.y1 - drag.y0) })} /> : null}
      </div>
      {rois.length ? (
        <div className="paper-ed-list">
          {rois.map((r) => (
            <span key={r.agent_id} className="paper-ed-chip">
              {r.name} {r.level != null ? `· ~${r.level}%` : ""}
              <button className="btn" onClick={() => setRois(rois.filter((x) => x.agent_id !== r.agent_id))} title="Remove">✕</button>
            </span>
          ))}
        </div>
      ) : null}
      {info?.events.length ? (
        <div className="paper-ed-events">
          <strong>Recent camera events</strong>
          {info.events.map((e) => (
            <div key={e.id} className="paper-ed-ev">
              {e.has_image ? <img src={`${API_BASE}/cams/paper/events/${e.id}.jpg`} alt="" /> : null}
              <div>
                <b>{e.name}</b> · {e.kind === "changed" ? "Roll changed" : e.kind === "jam_suspect" ? "Likely jam" : e.kind}
                <div className="muted">{new Date(e.at).toLocaleString()} · {e.detail}</div>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
