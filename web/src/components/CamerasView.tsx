import { useEffect, useRef, useState } from "react";
import { api, camWsUrl, isAdmin, type Agent, type Camera, type CamList } from "../api/client";

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

type CamEl = HTMLElement & { mode: string; src: string; background: boolean; video?: HTMLVideoElement };

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
    return () => {
      alive = false;
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
