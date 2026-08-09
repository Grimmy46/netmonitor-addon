import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { disablePush, enablePush, pushSupport } from "../lib/push";

/**
 * Header bell: per-device alert toggle for ANY signed-in user (viewers too).
 * The ON state means the SERVER has this browser's subscription — not merely
 * that the browser holds one locally (those can drift; the server is truth).
 * The popover reports each enable step live so a stall points at its cause.
 */
export function NotifyBell() {
  const support = pushSupport();
  const [on, setOn] = useState(false);
  const [mine, setMine] = useState<number | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [popStyle, setPopStyle] = useState<React.CSSProperties>({});
  const wrapRef = useRef<HTMLDivElement | null>(null);

  async function refresh() {
    try {
      let endpoint: string | undefined;
      if (support === "ok" && "serviceWorker" in navigator && Notification.permission === "granted") {
        const reg = await navigator.serviceWorker.getRegistration();
        endpoint = (await reg?.pushManager.getSubscription())?.endpoint;
      }
      const st = await api.pushStatus(endpoint);
      setOn(!!endpoint && st.this_device);
      setMine(st.mine);
    } catch {
      /* signed-out or offline — leave defaults */
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  function togglePop() {
    // On phones the bell can sit anywhere after the header wraps, so an
    // anchored popover can hang off-screen — pin it to the viewport instead.
    const r = wrapRef.current?.getBoundingClientRect();
    if (window.innerWidth <= 760 && r) {
      setPopStyle({ position: "fixed", left: 12, right: 12, top: r.bottom + 10, width: "auto" });
    } else {
      setPopStyle({ position: "absolute", right: 0, top: "calc(100% + 8px)", width: 280 });
    }
    setOpen((v) => !v);
    if (!open) refresh();
  }

  async function toggle() {
    setBusy(true);
    setMsg("");
    try {
      if (on) {
        await disablePush();
        setMsg("Alerts off on this device.");
      } else {
        await enablePush(setMsg);
        setMsg("✅ This device is registered — send a test.");
      }
    } catch (e) {
      setMsg(`⚠️ ${String(e instanceof Error ? e.message : e)}`);
    } finally {
      setBusy(false);
      refresh();
    }
  }

  async function test() {
    setBusy(true);
    setMsg("Sending test…");
    try {
      const r = await api.pushTest();
      setMsg(
        r.sent > 0
          ? `Test sent to ${r.sent} device${r.sent === 1 ? "" : "s"} — check your notifications.`
          : "The server has no registered devices for your account yet — hit “Turn on” first (on the device that should get the push).",
      );
    } catch (e) {
      setMsg(`⚠️ ${String(e instanceof Error ? e.message : e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div ref={wrapRef} style={{ position: "relative" }}>
      <button
        className="btn"
        title={on ? "Offline alerts: ON for this device" : "Enable offline alerts on this device"}
        onClick={togglePop}
      >
        {on ? "🔔" : "🔕"}
      </button>
      {open ? (
        <div
          className="panel"
          style={{ padding: 14, zIndex: 60, boxShadow: "var(--shadow)", ...popStyle }}
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
              <p className="sub" style={{ fontSize: 13, marginBottom: 6 }}>
                Get a push on this device when a kiosk stops reporting or a Main-site
                device goes down or unreachable — and when it comes back. Mass
                power-downs arrive as one summary, not a storm.
              </p>
              <p className="sub" style={{ fontSize: 12, marginBottom: 10 }}>
                This device: <strong>{on ? "registered ✓" : "not registered"}</strong>
                {mine !== null ? <> · your account: {mine} device{mine === 1 ? "" : "s"}</> : null}
              </p>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button className="btn" disabled={busy} onClick={toggle}>
                  {busy ? "…" : on ? "Turn off" : "Turn on"}
                </button>
                <button className="btn" disabled={busy} onClick={test}>
                  🔔 Send test
                </button>
              </div>
            </>
          )}
          {msg ? (
            <p className="sub" style={{ fontSize: 12, marginTop: 8, wordBreak: "break-word" }}>{msg}</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
