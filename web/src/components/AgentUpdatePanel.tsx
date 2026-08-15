import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Agent, type AgentExeMeta } from "../api/client";

/**
 * Agent update (admin): upload the current NetMonAgent.exe, then stage a rollout
 * by opting stations into the self-update. Flagged kiosks download the exe,
 * verify its sha256, back it up, swap, and relaunch under the watchdog — no
 * station visits. Start with a couple, watch a nightly power-cycle, then all.
 */
function fmtSize(n: number): string {
  if (!n) return "—";
  return n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${(n / 1024).toFixed(0)} KB`;
}

export function AgentUpdatePanel({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const [stations, setStations] = useState<Agent[]>([]);
  const [meta, setMeta] = useState<AgentExeMeta | null>(null);
  const [version, setVersion] = useState("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function load() {
    try {
      const [ag, m] = await Promise.all([api.agents(), api.agentExeMeta()]);
      setStations(ag);
      setMeta(m);
    } catch (e) {
      setMsg(String(e instanceof Error ? e.message : e));
    }
  }
  useEffect(() => { load(); }, []);

  const claimed = useMemo(() => {
    const n = q.trim().toLowerCase();
    const list = stations.filter((s) => s.claimed && (!n || s.name.toLowerCase().includes(n)));
    return [...list].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  }, [stations, q]);

  const onRollout = claimed.filter((s) => s.exe_rollout).length;

  async function run(fn: () => Promise<void>) {
    setBusy(true); setMsg("");
    try { await fn(); await load(); onChanged(); }
    catch (e) { setMsg(String(e instanceof Error ? e.message : e)); }
    finally { setBusy(false); }
  }

  async function upload() {
    const f = fileRef.current?.files?.[0];
    if (!f) { setMsg("Pick the NetMonAgent.exe file first."); return; }
    const v = version.trim();
    if (!v) { setMsg("Enter the exe's bootstrap version (e.g. 2.5)."); return; }
    await run(async () => {
      const m = await api.uploadAgentExe(f, v);
      setMsg(`Uploaded ${m.filename} v${m.version} (${fmtSize(m.size)}). Now flag the stations to roll out to.`);
      if (fileRef.current) fileRef.current.value = "";
    });
  }

  function toggle(s: Agent) {
    return run(async () => { await api.setExeRollout({ agentIds: [s.id], enabled: !s.exe_rollout }); });
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" style={{ width: "min(720px, 94vw)" }} onClick={(e) => e.stopPropagation()}>
        <h2>Agent update</h2>
        <p style={{ marginTop: 0 }}>
          Upload the current <strong>NetMonAgent.exe</strong>, then opt stations into
          the rollout. Flagged kiosks self-update (download → verify hash → swap →
          relaunch under the watchdog) with no station visits.
        </p>

        {/* Upload */}
        <div className="field">
          <label>Agent exe</label>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <input ref={fileRef} type="file" accept=".exe,application/octet-stream" style={{ flex: 1, minWidth: 200 }} />
            <input
              type="text" placeholder="version e.g. 2.5" value={version}
              onChange={(e) => setVersion(e.target.value)} style={{ width: 130 }}
            />
            <button className="btn btn-primary" onClick={upload} disabled={busy}>
              {busy ? "Uploading…" : "Upload"}
            </button>
          </div>
        </div>

        <div className="banner" style={{ marginBottom: 14 }}>
          {meta?.present ? (
            <span>
              Stored exe: <strong>v{meta.version}</strong> · {fmtSize(meta.size)} ·
              sha256 <code style={{ fontSize: 11 }}>{meta.sha256?.slice(0, 12)}…</code> ·
              {" "}{meta.rollout_count} station{meta.rollout_count === 1 ? "" : "s"} on rollout
            </span>
          ) : (
            <span>No exe uploaded yet — upload one to enable the rollout.</span>
          )}
        </div>

        {msg ? <div className="banner" style={{ marginBottom: 12 }}>{msg}</div> : null}

        {/* Rollout list (claimed stations only) */}
        <div className="devices-toolbar" style={{ margin: "6px 0 10px" }}>
          <div className="panel-title" style={{ margin: 0 }}>
            Rollout · {onRollout}/{claimed.length} claimed stations on
          </div>
          <div className="spacer" />
          <input className="search" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>

        <div style={{ marginBottom: 10, display: "flex", gap: 8 }}>
          <button className="btn" disabled={busy || !meta?.present}
            onClick={() => run(async () => { await api.setExeRollout({ all: true, enabled: true }); })}
            title="Opt every claimed station into the rollout">
            Enable all
          </button>
          <button className="btn" disabled={busy}
            onClick={() => run(async () => { await api.setExeRollout({ all: true, enabled: false }); })}>
            Disable all
          </button>
        </div>

        <div style={{ maxHeight: 320, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
          {claimed.length === 0 ? (
            <p className="hint" style={{ padding: 14 }}>No claimed stations{q ? " match" : ""}.</p>
          ) : (
            claimed.map((s) => (
              <label key={s.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", borderBottom: "1px solid var(--border)", cursor: "pointer" }}>
                <input type="checkbox" checked={s.exe_rollout} disabled={busy || !meta?.present} onChange={() => toggle(s)} />
                <span className={`pill ${s.online ? "online" : "offline"}`}><span className="dot" /></span>
                <strong>{s.name}</strong>
                <span className="sub" style={{ fontSize: 12 }}>
                  exe {s.bootstrap_version ?? "?"}
                  {meta?.present && s.bootstrap_version === meta.version ? " · up to date" : ""}
                </span>
                <div className="spacer" style={{ flex: 1 }} />
                {s.exe_rollout ? <span className="sub" style={{ fontSize: 11, color: "var(--accent)" }}>on rollout</span> : null}
              </label>
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
