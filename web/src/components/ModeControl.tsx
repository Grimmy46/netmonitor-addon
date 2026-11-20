import { useEffect, useRef, useState } from "react";
import { ApiError, api, type NetworkMode, type NetworkModeKey, type NetworkModeUpdate } from "../api/client";
import { EXPECTED_EFFECTS, MODE_BLURB, MODE_LABEL, TABS, describeEffects, shortWhen, type Tab } from "../lib/modes";

export function LiveTag() {
  return (
    <span className="live-tag" aria-label="Live mode">
      <span className="live-dot" />LIVE
    </span>
  );
}

/** Setup / Open / Teardown. `live` is the tab for the current mode; the others stay viewable. */
export function ModeTabs({ tab, live, onPick }: { tab: Tab; live: Tab | null; onPick: (t: Tab) => void }) {
  return (
    <div className="mode-tabs" role="tablist" aria-label="Show">
      {TABS.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={tab === t.key}
          className={`mode-tab${tab === t.key ? " viewing" : ""}${live === t.key ? " is-live" : ""}`}
          onClick={() => onPick(t.key)}
          title={live === t.key ? `${t.label} (current mode)` : `View ${t.label}`}
        >
          <span className="mode-tab-label">{t.label}</span>
          {live === t.key ? <LiveTag /> : null}
        </button>
      ))}
    </div>
  );
}

/** Small status block: current mode, event, since/until, and the admin change button. */
export function ModeCard({ mode, canChange, onChange, stale = false }: { mode: NetworkMode; canChange: boolean; onChange: () => void; stale?: boolean }) {
  const where = [mode.event_name, mode.venue].filter(Boolean).join(" · ");
  return (
    <div className={`mode-card mode-${mode.mode}${stale ? " stale" : ""}`}>
      <div className="mode-card-top">
        <span className="mode-card-lbl">Mode</span>
        <span className="mode-card-val">{MODE_LABEL[mode.mode]}</span>
      </div>
      {stale ? <div className="mode-card-warn" role="status">Can't reach the server. This may be out of date.</div> : null}
      {where ? <div className="mode-card-sub" title={where}>{where}</div> : null}
      {mode.since ? <div className="mode-card-sub">Since {shortWhen(mode.since)}</div> : null}
      {mode.until ? <div className="mode-card-sub">Until {shortWhen(mode.until)}</div> : null}
      {mode.reopen_grace_until ? <div className="mode-card-sub">Reopen grace until {shortWhen(mode.reopen_grace_until)}</div> : null}
      {canChange && !stale ? (
        <button className="btn btn-xs mode-card-btn" onClick={onChange}>Change mode</button>
      ) : null}
    </div>
  );
}

/** Shown in place of the mode card when the mode couldn't be loaded at all. */
export function ModeUnknownCard() {
  return (
    <div className="mode-card stale">
      <div className="mode-card-top">
        <span className="mode-card-lbl">Mode</span>
        <span className="mode-card-val">Unknown</span>
      </div>
      <div className="mode-card-warn" role="status">Can't reach the server. Trying again.</div>
    </div>
  );
}

/** `<input type=datetime-local>` value from a Date or ISO string, in local time. */
function toLocalInput(v: string | Date | null | undefined): string {
  if (!v) return "";
  const d = v instanceof Date ? v : new Date(v);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

const PICK: NetworkModeKey[] = ["setup", "open", "teardown", "offseason"];

/** Default end time for a new mode, prefilled in the dialog (editable, can be cleared). */
export const DEFAULT_HOURS: Partial<Record<NetworkModeKey, number>> = { teardown: 18, setup: 72 };
function defaultUntil(m: NetworkModeKey): string {
  const h = DEFAULT_HOURS[m];
  return h ? toLocalInput(new Date(Date.now() + h * 3600_000)) : "";
}

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';

/**
 * Admin mode switch with a confirm step. Shows what changes, from the API's
 * `effects` for the current mode and the expected effects for the new one.
 */
export function ModeDialog({
  current,
  initial,
  onClose,
  onSaved,
  onError,
}: {
  current: NetworkMode;
  initial?: NetworkModeKey;
  onClose: () => void;
  onSaved: (m: NetworkMode) => void;
  /** Called after a failed save, so the caller can reload the real mode. */
  onError?: () => void;
}) {
  const start = initial ?? current.mode;
  const [target, setTargetRaw] = useState<NetworkModeKey>(start);
  const [eventName, setEventName] = useState(start === "offseason" && current.mode !== "offseason" ? "" : current.event_name ?? "");
  const [venue, setVenue] = useState(start === "offseason" && current.mode !== "offseason" ? "" : current.venue ?? "");
  const [until, setUntil] = useState(start === current.mode ? toLocalInput(current.until) : defaultUntil(start));
  const [untilTouched, setUntilTouched] = useState(false);
  const [ack, setAck] = useState(false);
  const [needConfirm, setNeedConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);

  const setTarget = (k: NetworkModeKey) => {
    setTargetRaw(k);
    setNeedConfirm(false);
    setErr("");
    // Off-season is between events: don't carry the event or venue over.
    if (k === "offseason") { setEventName(""); setVenue(""); }
    else if (target === "offseason") { setEventName(current.event_name ?? ""); setVenue(current.venue ?? ""); }
    if (!untilTouched) setUntil(k === current.mode ? toLocalInput(current.until) : defaultUntil(k));
  };

  const same = target === current.mode;
  const leavingOpen = current.mode === "open" && target !== "open";
  const showAck = leavingOpen || needConfirm;
  const now = describeEffects(current.effects);
  const after = describeEffects(same ? current.effects : EXPECTED_EFFECTS[target]);
  const detailsChanged =
    eventName.trim() !== (current.event_name ?? "") ||
    venue.trim() !== (current.venue ?? "") ||
    until !== toLocalInput(current.until);
  const untilDate = until ? new Date(until) : null;
  const untilBad = !!untilDate && (Number.isNaN(untilDate.getTime()) || untilDate.getTime() <= Date.now());

  const close = () => { if (!busy) onClose(); };
  const busyRef = useRef(busy);
  busyRef.current = busy;
  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape" && !busyRef.current) onClose(); };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [onClose]);

  // Escape closes (when idle); Tab stays inside the dialog; focus returns on close.
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const box = boxRef.current;
    box?.querySelector<HTMLElement>(".mode-opt.on")?.focus();
    return () => { prev?.focus?.(); };
  }, []);
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== "Tab" || !boxRef.current) return;
    const els = Array.from(boxRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (!els.length) return;
    const first = els[0], last = els[els.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  async function save() {
    if (untilBad) { setErr("The end time has to be in the future."); return; }
    if (showAck && !ack) { setErr("Tick the box to confirm."); return; }
    setBusy(true);
    setErr("");
    const body: NetworkModeUpdate = {
      mode: target,
      event_name: eventName.trim() || null,
      venue: venue.trim() || null,
      until: untilDate ? untilDate.toISOString() : null,
    };
    try {
      let m: NetworkMode;
      try {
        m = await api.setNetworkMode(needConfirm ? { ...body, confirm: true } : body);
      } catch (e) {
        // 409 = the server wants an explicit confirm. Resend with it if the box is ticked.
        if (e instanceof ApiError && e.status === 409 && !needConfirm) {
          setNeedConfirm(true);
          if (!ack) {
            setErr(e.message && !/^409\b/.test(e.message) ? e.message : "Please confirm this change.");
            setBusy(false);
            return;
          }
          m = await api.setNetworkMode({ ...body, confirm: true });
        } else throw e;
      }
      onSaved(m);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
      setBusy(false);
      onError?.();
    }
  }

  return (
    <div className="overlay" onClick={close}>
      <div ref={boxRef} className="modal mode-dialog" role="dialog" aria-modal="true" aria-labelledby="mode-dlg-title"
        onClick={(e) => e.stopPropagation()} onKeyDown={onKeyDown}>
        <h2 id="mode-dlg-title">Change mode</h2>
        <p>
          Now: <b>{MODE_LABEL[current.mode]}</b>{current.since ? ` since ${shortWhen(current.since)}` : ""}.
          {current.reopen_grace_until ? <> Reopen grace until {shortWhen(current.reopen_grace_until)}.</> : null}
        </p>

        <div className="mode-pick" role="radiogroup" aria-label="New mode">
          {PICK.map((k) => (
            <button
              key={k}
              role="radio"
              aria-checked={target === k}
              className={`mode-opt${target === k ? " on" : ""}${current.mode === k ? " cur" : ""}`}
              onClick={() => setTarget(k)}
              disabled={busy}
            >
              <span className="mode-opt-name">{MODE_LABEL[k]}{current.mode === k ? <span className="cur-tag">Now</span> : null}</span>
              <span className="mode-opt-blurb">{MODE_BLURB[k]}</span>
            </button>
          ))}
        </div>

        <div className="mode-fields">
          {target === "offseason" ? (
            <p className="hint mode-fields-note">Off-season is between events, so the event and venue are cleared.</p>
          ) : (
            <>
              <label>Event<input value={eventName} disabled={busy} onChange={(e) => setEventName(e.target.value)} placeholder="e.g. Arizona State Fair" /></label>
              <label>Venue<input value={venue} disabled={busy} onChange={(e) => setVenue(e.target.value)} placeholder="e.g. Phoenix" /></label>
            </>
          )}
          <label>
            {DEFAULT_HOURS[target] && !same ? `Ends (default ${DEFAULT_HOURS[target]} h from now)` : "Ends (optional)"}
            <input type="datetime-local" value={until} disabled={busy} aria-invalid={untilBad}
              min={toLocalInput(new Date())}
              onChange={(e) => { setUntil(e.target.value); setUntilTouched(true); }} />
            {untilBad ? <span className="field-err">Pick a time in the future.</span> : null}
          </label>
        </div>

        <div className="panel-title" style={{ margin: "14px 0 6px" }}>{same ? "What this mode does" : "What changes"}</div>
        <table className="fx-table">
          <thead>
            <tr><th /><th>Now</th>{same ? null : <th>After</th>}</tr>
          </thead>
          <tbody>
            {now.map((r, i) => {
              const a = after[i];
              const changed = !same && a && a.value !== r.value;
              return (
                <tr key={r.key} className={changed ? "changed" : ""}>
                  <td>{r.label}</td>
                  <td>{r.value}</td>
                  {same ? null : <td>{a?.value ?? ""}{changed ? <span className="fx-arrow"> ●</span> : null}</td>}
                </tr>
              );
            })}
          </tbody>
        </table>
        {!same ? <p className="hint" style={{ margin: "6px 0 0", fontSize: 12 }}>"After" is what {MODE_LABEL[target]} normally does. The server confirms it when you switch.</p> : null}

        {showAck ? (
          <label className="mode-ack">
            <input type="checkbox" checked={ack} disabled={busy} onChange={(e) => { setAck(e.target.checked); setErr(""); }} />
            <span>I understand alerts will pause</span>
          </label>
        ) : null}

        {err ? <div className="banner err" role="alert" style={{ marginTop: 12 }}>{err}</div> : null}

        <div className="modal-actions" style={{ marginTop: 16 }}>
          <button className="btn" onClick={close} disabled={busy}>Cancel</button>
          <button className="btn btn-primary" onClick={save}
            disabled={busy || (same && !detailsChanged) || untilBad || (showAck && !ack)}>
            {busy ? "Saving…" : same ? "Save details" : `Switch to ${MODE_LABEL[target]}`}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Placeholder for tabs that are planned but not built yet. */
export function ComingSoon({ title, text }: { title: string; text: string }) {
  return (
    <div className="panel soon">
      <div className="panel-title">{title}</div>
      <p>{text}</p>
      <span className="soon-tag">Coming soon</span>
    </div>
  );
}
