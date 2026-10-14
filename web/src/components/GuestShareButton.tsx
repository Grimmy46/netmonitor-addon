import { useEffect, useRef, useState } from "react";
import { api, type GuestLink } from "../api/client";

/** Top-bar button (admins): copy the guest view link in one click. */
export function GuestShareButton() {
  const [open, setOpen] = useState(false);
  const [g, setG] = useState<GuestLink | null>(null);
  const [msg, setMsg] = useState("");
  const wrap = useRef<HTMLDivElement>(null);
  const url = g?.token ? `${window.location.origin}/#/guest/${g.token}` : "";

  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => { if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", off);
    return () => document.removeEventListener("mousedown", off);
  }, [open]);

  const copy = async (u = url) => {
    try { await navigator.clipboard.writeText(u); setMsg("✓ Copied — paste it anywhere."); }
    catch { setMsg("Select the link and copy it."); }
  };
  const toggle = async () => {
    const next = !open;
    setOpen(next); setMsg("");
    if (next) {
      try {
        const r = await api.guestLink();
        setG(r);
        if (r.token) await copy(`${window.location.origin}/#/guest/${r.token}`);
      } catch (e) { setMsg(String((e as Error)?.message ?? e)); }
    }
  };
  const create = async () => {
    try { const r = await api.makeGuestLink(); setG(r); if (r.token) await copy(`${window.location.origin}/#/guest/${r.token}`); }
    catch (e) { setMsg(String((e as Error)?.message ?? e)); }
  };

  return (
    <div ref={wrap} style={{ position: "relative" }}>
      <button className="btn" title="Copy the guest view link" onClick={toggle}>
        🔗<span className="hide-sm"> Guest link</span>
      </button>
      {open ? (
        <div className="panel" style={{ position: "absolute", right: 0, top: "calc(100% + 6px)", width: 320, maxWidth: "88vw",
          padding: 14, zIndex: 60, boxShadow: "var(--shadow)" }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Guest view link</div>
          <p className="sub" style={{ marginTop: 0, fontSize: 12 }}>
            View only, no sign-in. Shows station status — no addresses, cameras or settings.
          </p>
          {g === null ? <span className="sub">Loading…</span> : g.enabled ? (
            <>
              <input readOnly value={url} onFocus={(e) => e.currentTarget.select()} style={{ width: "100%", fontSize: 12, boxSizing: "border-box" }} />
              <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                <button className="btn btn-primary" onClick={() => copy()}>Copy</button>
                <a className="btn" href={url} target="_blank" rel="noreferrer">Preview</a>
              </div>
            </>
          ) : (
            <button className="btn btn-primary" onClick={create}>Create guest link</button>
          )}
          {msg ? <p className="hint" style={{ margin: "8px 0 0", fontSize: 12 }}>{msg}</p> : null}
          <p className="sub" style={{ margin: "8px 0 0", fontSize: 11 }}>New link / turn off: Settings → Accounts.</p>
        </div>
      ) : null}
    </div>
  );
}
