import { useEffect, useMemo, useState } from "react";
import { api, type Agent, type Site } from "../api/client";

/** Parse pasted text (plain names, one per line, OR CSV) into station names. */
function parseNames(text: string): string[] {
  const out: string[] = [];
  for (const line of text.split(/\r?\n/)) {
    const first = line.split(",")[0].trim();
    if (!first) continue;
    if (first.toLowerCase() === "name") continue; // CSV header
    out.push(first);
  }
  return out;
}

/**
 * Stations admin: manage the master list of kiosks (add, bulk-import, remove,
 * release) and show the enrollment PIN. Kiosks pick from this list on first run.
 */
export function StationsPanel({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const [stations, setStations] = useState<Agent[]>([]);
  const [sites, setSites] = useState<Site[]>([]);
  const [pin, setPin] = useState<string | null>(null);
  const [pinShown, setPinShown] = useState(false);
  const [q, setQ] = useState("");
  const [name, setName] = useState("");
  const [bulk, setBulk] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  async function load() {
    try {
      setStations(await api.agents());
    } catch {
      /* ignore */
    }
  }
  useEffect(() => {
    load();
    api.enrollmentPin().then((r) => setPin(r.pin)).catch(() => {});
    api.sites().then(setSites).catch(() => {});
  }, []);

  const shown = useMemo(() => {
    const n = q.trim().toLowerCase();
    const list = n ? stations.filter((s) => s.name.toLowerCase().includes(n)) : stations;
    return [...list].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  }, [stations, q]);

  const claimedCount = stations.filter((s) => s.claimed).length;

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setMsg("");
    try {
      await fn();
      await load();
      onChanged();
    } catch (e) {
      setMsg(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  async function addOne() {
    const nm = name.trim();
    if (!nm) return;
    await run(async () => {
      await api.createAgent(nm, null);
      setName("");
    });
  }

  async function importBulk() {
    const names = parseNames(bulk);
    if (names.length === 0) {
      setMsg("Nothing to import — paste station names (or the kiosk CSV).");
      return;
    }
    await run(async () => {
      const r = await api.bulkCreateStations(names);
      setBulk("");
      setMsg(`Imported ${r.created} station${r.created === 1 ? "" : "s"}` +
        (r.skipped ? ` · skipped ${r.skipped} already present` : ""));
    });
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" style={{ width: "min(680px, 94vw)" }} onClick={(e) => e.stopPropagation()}>
        <h2>Stations</h2>
        <p style={{ marginTop: 0 }}>
          The master list of kiosks. On a kiosk's first run the agent asks for the
          <strong> enrollment PIN</strong>, then picks its station from this list.
        </p>
        <p className="sub" style={{ marginTop: -6, marginBottom: 14, fontSize: 12 }}>
          Set a station's <strong>Probe site</strong> to have that kiosk ping its
          site's UniFi devices on the LAN — that's what powers local reachability
          (the "up in UniFi but unreachable" signal) on the site page.
        </p>

        <div className="banner" style={{ marginBottom: 14, display: "flex", alignItems: "center", gap: 10 }}>
          <span>Enrollment PIN:</span>
          <strong style={{ fontVariantNumeric: "tabular-nums", letterSpacing: 2, fontSize: 16 }}>
            {pin == null ? "…" : pinShown ? pin : "••••••"}
          </strong>
          <div className="spacer" style={{ flex: 1 }} />
          <button className="btn" onClick={() => setPinShown((v) => !v)} disabled={pin == null}>
            {pinShown ? "Hide" : "Show"}
          </button>
          <button
            className="btn"
            onClick={() => run(async () => { setPin((await api.regenerateEnrollmentPin()).pin); setPinShown(true); })}
            disabled={busy}
          >
            Regenerate
          </button>
        </div>

        {/* Add + bulk import */}
        <div className="field">
          <label htmlFor="stname">Add a station</label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              id="stname"
              type="text"
              autoComplete="off"
              placeholder="e.g. K3-6024"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addOne()}
              style={{ flex: 1 }}
            />
            <button className="btn btn-primary" onClick={addOne} disabled={busy || name.trim().length < 1}>Add</button>
          </div>
        </div>

        <div className="field">
          <label htmlFor="stbulk">Bulk import (one name per line, or paste the kiosk CSV)</label>
          <textarea
            id="stbulk"
            rows={4}
            placeholder={"K1-6001\nK1-6002\nK1-6003…"}
            value={bulk}
            onChange={(e) => setBulk(e.target.value)}
            style={{ padding: "10px 12px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--page)", color: "var(--ink-primary)", fontFamily: "inherit", fontSize: 13, resize: "vertical" }}
          />
          <div style={{ marginTop: 6, textAlign: "right" }}>
            <button className="btn" onClick={importBulk} disabled={busy || bulk.trim().length === 0}>
              {busy ? "Importing…" : "Import"}
            </button>
          </div>
        </div>

        {msg ? <div className="banner" style={{ marginBottom: 12 }}>{msg}</div> : null}

        {/* List */}
        <div className="devices-toolbar" style={{ margin: "6px 0 10px" }}>
          <div className="panel-title" style={{ margin: 0 }}>
            {stations.length} stations · {claimedCount} claimed
          </div>
          <div className="spacer" />
          <input className="search" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>

        <div style={{ maxHeight: 320, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
          {shown.length === 0 ? (
            <p className="hint" style={{ padding: 14 }}>No stations{q ? " match" : " yet"}.</p>
          ) : (
            shown.map((s) => (
              <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
                <span className={`pill ${s.online ? "online" : s.claimed ? "offline" : "unknown"}`}><span className="dot" /></span>
                <strong>{s.name}</strong>
                <span className="sub" style={{ fontSize: 12 }}>
                  {s.online ? "online" : s.claimed ? `claimed${s.hostname ? " · " + s.hostname : ""}` : "unclaimed"}
                </span>
                <div className="spacer" style={{ flex: 1 }} />
                <select
                  className="search"
                  style={{ padding: "4px 6px", fontSize: 12, maxWidth: 160 }}
                  value={s.site_id ?? ""}
                  onChange={(e) => run(async () => { await api.setAgentSite(s.id, e.target.value || null); })}
                  disabled={busy || sites.length === 0}
                  title="Which UniFi site this kiosk pings on its LAN (for local device monitoring)"
                >
                  <option value="">Probe site…</option>
                  {sites.map((si) => (
                    <option key={si.id} value={si.id}>{si.name}</option>
                  ))}
                </select>
                {s.claimed ? (
                  <button className="btn" onClick={() => run(async () => { await api.releaseAgent(s.id); })} disabled={busy} title="Un-claim so another kiosk can enroll as this station">
                    Release
                  </button>
                ) : null}
                <button className="btn" onClick={() => run(async () => { await api.deleteAgent(s.id); })} disabled={busy}>
                  Remove
                </button>
              </div>
            ))
          )}
        </div>

        <div className="modal-actions" style={{ marginTop: 16 }}>
          <button className="btn" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
