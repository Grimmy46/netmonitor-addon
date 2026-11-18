import { useState } from "react";
import { api, type NetworkMode, type NetworkModeKey } from "../api/client";
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
export function ModeCard({ mode, canChange, onChange }: { mode: NetworkMode; canChange: boolean; onChange: () => void }) {
  const where = [mode.event_name, mode.venue].filter(Boolean).join(" · ");
  return (
    <div className={`mode-card mode-${mode.mode}`}>
      <div className="mode-card-top">
        <span className="mode-card-lbl">Mode</span>
        <span className="mode-card-val">{MODE_LABEL[mode.mode]}</span>
      </div>
      {where ? <div className="mode-card-sub" title={where}>{where}</div> : null}
      {mode.since ? <div className="mode-card-sub">Since {shortWhen(mode.since)}</div> : null}
      {mode.until ? <div className="mode-card-sub">Until {shortWhen(mode.until)}</div> : null}
      {canChange ? (
        <button className="btn btn-xs mode-card-btn" onClick={onChange}>Change mode</button>
      ) : null}
    </div>
  );
}

/** `<input type=datetime-local>` value from an ISO string, in local time. */
function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

const PICK: NetworkModeKey[] = ["setup", "open", "teardown", "offseason"];

/**
 * Admin mode switch with a confirm step. Shows what changes, from the API's
 * `effects` for the current mode and the expected effects for the new one.
 */
export function ModeDialog({
  current,
  initial,
  onClose,
  onSaved,
}: {
  current: NetworkMode;
  initial?: NetworkModeKey;
  onClose: () => void;
  onSaved: (m: NetworkMode) => void;
}) {
  const [target, setTarget] = useState<NetworkModeKey>(initial ?? current.mode);
  const [eventName, setEventName] = useState(current.event_name ?? "");
  const [venue, setVenue] = useState(current.venue ?? "");
  const [until, setUntil] = useState(toLocalInput(current.until));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const now = describeEffects(current.effects);
  const after = describeEffects(target === current.mode ? current.effects : EXPECTED_EFFECTS[target]);
  const same = target === current.mode;
  const detailsChanged =
    eventName.trim() !== (current.event_name ?? "") ||
    venue.trim() !== (current.venue ?? "") ||
    until !== toLocalInput(current.until);

  async function save() {
    setBusy(true);
    setErr("");
    try {
      const untilIso = until ? new Date(until).toISOString() : null;
      const m = await api.setNetworkMode({
        mode: target,
        event_name: eventName.trim() || null,
        venue: venue.trim() || null,
        until: untilIso,
      });
      onSaved(m);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
      setBusy(false);
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal mode-dialog" role="dialog" aria-label="Change mode" onClick={(e) => e.stopPropagation()}>
        <h2>Change mode</h2>
        <p>Now: <b>{MODE_LABEL[current.mode]}</b>{current.since ? ` since ${shortWhen(current.since)}` : ""}.</p>

        <div className="mode-pick" role="radiogroup" aria-label="New mode">
          {PICK.map((k) => (
            <button
              key={k}
              role="radio"
              aria-checked={target === k}
              className={`mode-opt${target === k ? " on" : ""}${current.mode === k ? " cur" : ""}`}
              onClick={() => setTarget(k)}
            >
              <span className="mode-opt-name">{MODE_LABEL[k]}{current.mode === k ? <span className="cur-tag">Now</span> : null}</span>
              <span className="mode-opt-blurb">{MODE_BLURB[k]}</span>
            </button>
          ))}
        </div>

        <div className="mode-fields">
          <label>Event<input value={eventName} onChange={(e) => setEventName(e.target.value)} placeholder="e.g. Arizona State Fair" /></label>
          <label>Venue<input value={venue} onChange={(e) => setVenue(e.target.value)} placeholder="e.g. Phoenix" /></label>
          <label>Until (optional)<input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} /></label>
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

        {err ? <div className="banner err" style={{ marginTop: 12 }}>{err}</div> : null}

        <div className="modal-actions" style={{ marginTop: 16 }}>
          <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn btn-primary" onClick={save} disabled={busy || (same && !detailsChanged)}>
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
