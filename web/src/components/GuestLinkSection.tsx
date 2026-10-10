import { useEffect, useState } from "react";
import { api, type GuestLink } from "../api/client";

/** Settings → Accounts: the guest view link (view-only, reduced detail, no sign-in). */
export function GuestLinkSection() {
  const [g, setG] = useState<GuestLink | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => { api.guestLink().then(setG).catch((e) => setMsg(String(e?.message ?? e))); }, []);
  const url = g?.token ? `${window.location.origin}/#/guest/${g.token}` : "";
  const run = async (fn: () => Promise<GuestLink>, note: string) => {
    setBusy(true); setMsg("");
    try { setG(await fn()); setMsg(note); } catch (e) { setMsg(String((e as Error)?.message ?? e)); }
    setBusy(false);
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(url); setMsg("Link copied."); } catch { setMsg("Select the link and copy it."); }
  };
  return (
    <>
      <h3 style={{ margin: "18px 0 6px", fontSize: 15 }}>Guest view link</h3>
      <p style={{ marginTop: 0 }}>
        Anyone with this link sees the landing dashboard plus kiosk &amp; ticket box status — view only,
        no sign-in, no IPs, ports, cameras or settings. Making a new link or turning it off signs every guest out.
      </p>
      {g?.enabled ? (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input readOnly value={url} onFocus={(e) => e.currentTarget.select()} style={{ flex: 1, minWidth: 220, fontSize: 12 }} />
          <button className="btn btn-primary" onClick={copy} disabled={busy}>Copy</button>
          <button className="btn" disabled={busy}
            onClick={() => window.confirm("Make a new guest link? The old link stops working and current guests are signed out.")
              && run(api.makeGuestLink, "New link made — the old one no longer works.")}>New link</button>
          <button className="btn" disabled={busy}
            onClick={() => window.confirm("Turn off the guest link? Current guests are signed out.")
              && run(api.disableGuestLink, "Guest link turned off.")}>Turn off</button>
        </div>
      ) : (
        <button className="btn btn-primary" disabled={busy || g === null} onClick={() => run(api.makeGuestLink, "Guest link created.")}>
          Create guest link
        </button>
      )}
      {msg ? <p className="hint" style={{ marginTop: 6 }}>{msg}</p> : null}
    </>
  );
}
