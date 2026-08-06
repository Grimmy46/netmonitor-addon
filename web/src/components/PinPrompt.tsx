import { useState } from "react";
import { adminAuth, api } from "../api/client";

/**
 * Unlock prompt for admin actions. Verifies against the server, remembers the
 * PIN for this browser session, then runs the action that was gated.
 */
export function PinPrompt({ onUnlocked, onClose }: { onUnlocked: () => void; onClose: () => void }) {
  const [pin, setPin] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (pin.trim().length < 4 || busy) return;
    setBusy(true);
    setErr("");
    try {
      await api.pinVerify(pin.trim());
      adminAuth.set(pin.trim());
      onUnlocked();
    } catch {
      setErr("Wrong PIN.");
      setPin("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" style={{ width: "min(340px, 92vw)" }} onClick={(e) => e.stopPropagation()}>
        <h2 style={{ marginTop: 0 }}>Admin PIN</h2>
        <p className="sub" style={{ marginTop: -6 }}>
          Changing settings or stations needs the dashboard PIN.
        </p>
        <input
          type="password"
          inputMode="numeric"
          autoFocus
          autoComplete="off"
          placeholder="PIN"
          value={pin}
          onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 8))}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          style={{
            width: "100%", padding: "10px 12px", fontSize: 18, letterSpacing: 6,
            textAlign: "center", borderRadius: 8, border: "1px solid var(--border)",
            background: "var(--page)", color: "var(--ink-primary)",
          }}
        />
        {err ? <div className="banner err" style={{ marginTop: 10 }}>{err}</div> : null}
        <div className="modal-actions" style={{ marginTop: 14 }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={submit} disabled={busy || pin.trim().length < 4}>
            {busy ? "Checking…" : "Unlock"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Decide whether an admin action can run now or needs the PIN prompt first. */
export async function needsPinPrompt(): Promise<boolean> {
  try {
    const st = await api.pinStatus();
    if (!st.set) return false; // bootstrap: no PIN created yet
    const saved = adminAuth.pin;
    if (saved) {
      try {
        await api.pinVerify(saved);
        return false; // stored PIN still good
      } catch {
        adminAuth.set(null); // stale (PIN was changed) — re-prompt
      }
    }
    return true;
  } catch {
    return false; // status unavailable — let the server enforce on the action itself
  }
}
