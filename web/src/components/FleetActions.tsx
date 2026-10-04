import { useEffect, useRef, useState } from "react";
import { api, isAdmin, type Agent, type FleetAction, type FleetBatch, type ShutdownSchedule } from "../api/client";

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const ZONES = ["America/Los_Angeles", "America/Phoenix", "America/Denver", "America/Chicago", "America/New_York"];

function fmtTime(t: string) {
  const [h, m] = t.split(":").map(Number);
  const d = new Date(2000, 0, 1, h, m);
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** Fleet-wide buttons for the Kiosks tab: test-print every printer, shut every
 * kiosk down (now or on a weekly schedule), and the report of who didn't answer. */
export function FleetActions({ agents, onChanged }: { agents: Agent[]; onChanged: () => void }) {
  const [batch, setBatch] = useState<FleetBatch | null>(null);
  const [busy, setBusy] = useState<FleetAction | null>(null);
  const [confirmOff, setConfirmOff] = useState(false);
  const [showSched, setShowSched] = useState(false);
  const [sched, setSched] = useState<ShutdownSchedule | null>(null);
  const [err, setErr] = useState("");
  const [hideOk, setHideOk] = useState(true);
  const timer = useRef<number | null>(null);
  const online = agents.filter((a) => a.online).length;

  // Pick up the latest run (survives a page reload).
  useEffect(() => {
    Promise.all([api.fleetLatest("test-print").catch(() => null), api.fleetLatest("power-off").catch(() => null)])
      .then(([t, p]) => {
        const recent = [t, p].filter((b): b is FleetBatch => !!b && !!b.created_at &&
          Date.now() - new Date(b.created_at).getTime() < 3 * 3600_000);
        recent.sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
        if (recent[0]) setBatch(recent[0]);
      });
    api.shutdownSchedule().then((r) => setSched(r.schedule)).catch(() => {});
  }, []);

  // Poll while kiosks are still answering.
  useEffect(() => {
    if (!batch || batch.done) return;
    timer.current = window.setTimeout(() => {
      api.fleetGet(batch.id).then((b) => { setBatch(b); if (b.done) onChanged(); }).catch(() => {});
    }, 4000);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [batch, onChanged]);

  async function run(action: FleetAction, delay?: number) {
    setBusy(action); setErr("");
    try {
      const b = await api.fleetRun(action, delay != null ? { delay } : {});
      if (action !== "power-cancel") setBatch(b);
      else setBatch(null);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }

  if (!isAdmin()) return null;
  const offRecent = batch?.action === "power-off";
  const bad = batch ? batch.items.filter((i) => i.state === "failed" || i.state === "no answer") : [];
  const skipped = batch ? batch.items.filter((i) => i.state === "skipped") : [];
  const shown = batch ? (hideOk ? bad : batch.items.filter((i) => i.state !== "skipped")) : [];

  return (
    <div className="fleet-bar">
      <div className="fleet-buttons">
        <button className="btn btn-primary fleet-btn" disabled={!!busy || online === 0} onClick={() => run("test-print")}
          title="Print a small test ticket on every online kiosk and report the ones that didn't print">
          🖨 {busy === "test-print" ? "Sending…" : `Test print all (${online})`}
        </button>
        <button className="btn fleet-btn fleet-danger" disabled={!!busy || online === 0} onClick={() => setConfirmOff(true)}
          title="Shut down every online kiosk PC">
          ⏻ Shut down all
        </button>
        {offRecent ? (
          <button className="btn fleet-btn" disabled={!!busy} onClick={() => run("power-cancel")}
            title="Abort a pending shutdown on kiosks that haven't turned off yet">
            ✋ Cancel shutdown
          </button>
        ) : null}
        <button className="btn fleet-btn fleet-sched" onClick={() => setShowSched(true)}
          title="Weekly automatic shutdown">
          🕘 {sched?.enabled ? `${DAYS[sched.weekday].slice(0, 3)} ${fmtTime(sched.time)}` : "Auto-off: off"}
        </button>
      </div>
      {err ? <div className="banner err" style={{ marginTop: 8 }}>{err}</div> : null}

      {batch ? (
        <div className="fleet-result">
          <div className="fleet-result-head">
            <strong>{batch.action === "test-print" ? "Test print" : "Shutdown"}</strong>
            <span className="fr-ok">✓ {batch.ok} {batch.action === "test-print" ? "printed" : "shutting down"}</span>
            {batch.failed ? <span className="fr-bad">✗ {batch.failed} failed</span> : null}
            {batch.waiting ? <span className="fr-wait">… {batch.waiting} waiting</span> : null}
            {batch.skipped ? <span className="fr-skip">{batch.skipped} skipped</span> : null}
            <span className="spacer" />
            <span className="sub" style={{ fontSize: 12 }}>
              {batch.created_at ? new Date(batch.created_at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : ""}
              {batch.requested_by === "schedule" ? " · scheduled" : ""}
            </span>
            <button className="btn btn-xs" onClick={() => setHideOk(!hideOk)}>{hideOk ? "Show all" : "Problems only"}</button>
            <button className="btn btn-xs" onClick={() => setBatch(null)} title="Hide">✕</button>
          </div>
          {!batch.done ? <p className="sub" style={{ fontSize: 12, margin: "6px 0 0" }}>Kiosks pick this up within ~30 s; anything silent after 4 min counts as no answer.</p> : null}
          {shown.length ? (
            <ul className="fleet-items">
              {shown.map((i) => (
                <li key={i.agent_id} className={`fi fi-${i.state.replace(" ", "-")}`}>
                  <span className="fi-name">{i.name}</span>
                  <span className="fi-state">{i.state}</span>
                  <span className="fi-detail">{i.detail}</span>
                </li>
              ))}
            </ul>
          ) : batch.done ? <p className="sub" style={{ fontSize: 13, margin: "6px 0 0" }}>Everything that was online answered OK.</p> : null}
          {skipped.length ? (
            <p className="sub fleet-skipped">
              Not sent ({skipped.length}): {skipped.map((i) => `${i.name}${i.detail && i.detail !== "offline" ? ` (${i.detail})` : ""}`).join(", ")}
              {skipped.every((i) => i.detail === "offline") ? " — offline" : ""}
            </p>
          ) : null}
        </div>
      ) : null}

      {confirmOff ? <ShutdownConfirm online={online} onClose={() => setConfirmOff(false)}
        onGo={(delay) => { setConfirmOff(false); run("power-off", delay); }} /> : null}
      {showSched ? <ScheduleModal initial={sched} onClose={() => setShowSched(false)} onSaved={setSched} /> : null}
    </div>
  );
}

function ShutdownConfirm({ online, onClose, onGo }: { online: number; onClose: () => void; onGo: (delay: number) => void }) {
  const [delay, setDelay] = useState(60);
  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
        <h2 style={{ marginTop: 0 }}>Shut down {online} kiosks?</h2>
        <p className="sub">Each online kiosk PC shows a warning, then powers off. They won't come back until power is cycled (needs "power on after AC loss" in BIOS) or someone presses the power button.</p>
        <label className="field-label">Warning time</label>
        <div className="filter-chips" style={{ margin: "6px 0 16px" }}>
          {[[30, "30 s"], [60, "1 min"], [300, "5 min"], [600, "10 min"]].map(([v, l]) => (
            <button key={v} className={`chip ${delay === v ? "active" : ""}`} onClick={() => setDelay(Number(v))}>{l}</button>
          ))}
        </div>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="btn" onClick={onClose}>Keep running</button>
          <button className="btn fleet-danger-solid" onClick={() => onGo(delay)}>⏻ Shut down {online}</button>
        </div>
      </div>
    </div>
  );
}

function ScheduleModal({ initial, onClose, onSaved }: {
  initial: ShutdownSchedule | null; onClose: () => void; onSaved: (s: ShutdownSchedule) => void;
}) {
  const guessTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const [s, setS] = useState<ShutdownSchedule>(initial ?? {
    enabled: true, weekday: 6, time: "23:00", tz: ZONES.includes(guessTz) ? guessTz : "America/Phoenix", delay: 120,
  });
  const [err, setErr] = useState("");
  const zones = ZONES.includes(s.tz) ? ZONES : [s.tz, ...ZONES];
  async function save() {
    try {
      const r = await api.setShutdownSchedule(s);
      onSaved(r.schedule);
      onClose();
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    }
  }
  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 440 }} onClick={(e) => e.stopPropagation()}>
        <h2 style={{ marginTop: 0 }}>Weekly auto-off</h2>
        <p className="sub">Shuts down every online kiosk once a week, e.g. Sunday after closing, before the boxes lose power.</p>
        <label className="check-row">
          <input type="checkbox" checked={s.enabled} onChange={(e) => setS({ ...s, enabled: e.target.checked })} /> Enabled
        </label>
        <div className="sched-grid">
          <label>Day
            <select value={s.weekday} onChange={(e) => setS({ ...s, weekday: Number(e.target.value) })}>
              {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
            </select>
          </label>
          <label>Time
            <input type="time" value={s.time} onChange={(e) => setS({ ...s, time: e.target.value })} />
          </label>
          <label>Time zone
            <select value={s.tz} onChange={(e) => setS({ ...s, tz: e.target.value })}>
              {zones.map((z) => <option key={z} value={z}>{z.replace("America/", "").replace("_", " ")}</option>)}
            </select>
          </label>
          <label>Warning
            <select value={s.delay} onChange={(e) => setS({ ...s, delay: Number(e.target.value) })}>
              {[[60, "1 min"], [120, "2 min"], [300, "5 min"], [600, "10 min"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
        </div>
        {err ? <div className="banner err">{err}</div> : null}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 14 }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={save}>Save</button>
        </div>
      </div>
    </div>
  );
}
