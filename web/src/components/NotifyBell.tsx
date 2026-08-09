import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { disablePush, enablePush, pushEnabled, pushSupport } from "../lib/push";

/**
 * Header bell: per-device alert toggle for ANY signed-in user (viewers too).
 * 🔔 = this device gets offline alerts; 🔕 = it doesn't. The popover carries
 * the enable/disable action, a test button, and the iPhone install hint.
 */
export function NotifyBell() {
  const support = pushSupport();
  const [on, setOn] = useState(false);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    pushEnabled().then(setOn);
  }, []);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  if (support === "unsupported") return null;

  async function toggle() {
    setBusy(true);
    setMsg("");
    try {
      if (on) {
        await disablePush();
        setOn(false);
        setMsg("Alerts off on this device.");
      } else {
        await enablePush();
        setOn(true);
        setMsg("Alerts on — try a test push.");
      }
    } catch (e) {
      setMsg(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    setBusy(true);
    setMsg("");
    try {
      const r = await api.pushTest();
      setMsg(r.sent > 0 ? "Test sent — check your notifications." : "No subscribed devices for your account yet.");
    } catch (e) {
      setMsg(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div ref={wrapRef} style={{ position: "relative" }}>
      <button
        className="btn"
        title={on ? "Offline alerts: ON for this device" : "Enable offline alerts on this device"}
        onClick={() => setOpen((v) => !v)}
      >
        {on ? "🔔" : "🔕"}
      </button>
      {open ? (
        <div
          className="panel"
          style={{
            position: "absolute",
            right: 0,
            top: "calc(100% + 8px)",
            width: 280,
            padding: 14,
            zIndex: 60,
            boxShadow: "var(--shadow)",
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Offline alerts</div>
          {support === "needs-install" ? (
            <p className="sub" style={{ fontSize: 13 }}>
              On iPhone/iPad, first add NetMonitor to your Home Screen
              (Share&nbsp;→&nbsp;<strong>Add to Home Screen</strong>), then open it from
              there and enable alerts.
            </p>
          ) : (
            <>
              <p className="sub" style={{ fontSize: 13, marginBottom: 10 }}>
                Get a push on this device when a kiosk stops reporting or a Main-site
                device goes down or unreachable — and when it comes back. Mass
                power-downs arrive as one summary, not a storm.
              </p>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn" disabled={busy} onClick={toggle}>
                  {busy ? "…" : on ? "Turn off" : "Turn on"}
                </button>
                {on ? (
                  <button className="btn" disabled={busy} onClick={test}>
                    Send test
                  </button>
                ) : null}
              </div>
            </>
          )}
          {msg ? (
            <p className="sub" style={{ fontSize: 12, marginTop: 8 }}>{msg}</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
