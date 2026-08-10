import { useEffect, useState } from "react";
import { api, type Agent, type AuthUser, type LiveTarget, type UnifiConsole, type UnifiStatus } from "../api/client";

export function SettingsModal({
  status,
  onClose,
  onChanged,
}: {
  status: UnifiStatus | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  // ── Site Manager key ──────────────────────────────────────────────────────
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // ── Consoles ──────────────────────────────────────────────────────────────
  const [consoles, setConsoles] = useState<UnifiConsole[]>([]);
  const [cUrl, setCUrl] = useState("");
  const [cKey, setCKey] = useState("");
  const [cLabel, setCLabel] = useState("");
  const [cVerify, setCVerify] = useState(false);
  const [cBusy, setCBusy] = useState(false);
  const [cError, setCError] = useState("");

  // ── Agents / enrollment PIN ─────────────────────────────────────────────--
  const [agents, setAgents] = useState<Agent[]>([]);
  const [aBusy, setABusy] = useState(false);
  const [pin, setPin] = useState<string | null>(null);
  const [pinShown, setPinShown] = useState(false);

  // ── Live page (probe kiosk + targets) ───────────────────────────────────
  const [liveTargets, setLiveTargets] = useState<LiveTarget[]>([]);
  const [probeAgentId, setProbeAgentId] = useState<string>("");
  const [ltKind, setLtKind] = useState("ping");
  const [ltLabel, setLtLabel] = useState("");
  const [ltTarget, setLtTarget] = useState("");
  const [ltBusy, setLtBusy] = useState(false);
  const [ltMsg, setLtMsg] = useState("");

  const loadLive = () =>
    Promise.all([api.liveTargets(), api.liveFeed(1)])
      .then(([ts, f]) => {
        setLiveTargets(ts);
        setProbeAgentId(f.probe_agent?.id ?? "");
      })
      .catch(() => {});

  async function pickProbeAgent(id: string) {
    setLtBusy(true);
    setLtMsg("");
    try {
      await api.setProbeAgent(id || null);
      setProbeAgentId(id);
      setLtMsg(id ? "Probe kiosk set — live data within ~1 min." : "Probe kiosk cleared (cloud vantage only).");
    } catch (e) {
      setLtMsg(String(e instanceof Error ? e.message : e));
    } finally {
      setLtBusy(false);
    }
  }

  async function addLiveTarget() {
    if (!ltTarget.trim()) return;
    setLtBusy(true);
    setLtMsg("");
    try {
      await api.addLiveTarget({ kind: ltKind, label: ltLabel.trim(), target: ltTarget.trim() });
      setLtLabel("");
      setLtTarget("");
      await loadLive();
    } catch (e) {
      setLtMsg(String(e instanceof Error ? e.message : e));
    } finally {
      setLtBusy(false);
    }
  }

  async function toggleLiveTarget(t: LiveTarget) {
    setLtBusy(true);
    try {
      await api.updateLiveTarget(t.id, { enabled: !t.enabled });
      await loadLive();
    } finally {
      setLtBusy(false);
    }
  }

  async function removeLiveTarget(id: string) {
    setLtBusy(true);
    try {
      await api.deleteLiveTarget(id);
      await loadLive();
    } finally {
      setLtBusy(false);
    }
  }

  // ── Users (accounts & roles) ────────────────────────────────────────────--
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [uEmail, setUEmail] = useState("");
  const [uPass, setUPass] = useState("");
  const [uRole, setURole] = useState("viewer");
  const [uBusy, setUBusy] = useState(false);
  const [uMsg, setUMsg] = useState("");

  const loadUsers = () => api.users().then(setUsers).catch(() => {});

  async function addUser() {
    if (!uEmail.trim() || uPass.length < 8) {
      setUMsg("Email + password (min 8 chars) required.");
      return;
    }
    setUBusy(true);
    setUMsg("");
    try {
      await api.createUser(uEmail.trim(), uPass, uRole);
      setUEmail(""); setUPass("");
      await loadUsers();
      setUMsg("Account created.");
    } catch (e) {
      setUMsg(String(e instanceof Error ? e.message : e));
    } finally {
      setUBusy(false);
    }
  }

  async function removeUser(id: string) {
    setUBusy(true);
    setUMsg("");
    try { await api.deleteUser(id); await loadUsers(); }
    catch (e) { setUMsg(String(e instanceof Error ? e.message : e)); }
    finally { setUBusy(false); }
  }

  async function changeRole(id: string, role: string) {
    setUBusy(true);
    setUMsg("");
    try { await api.setUserRole(id, role); await loadUsers(); }
    catch (e) { setUMsg(String(e instanceof Error ? e.message : e)); }
    finally { setUBusy(false); }
  }

  async function resetPassword(id: string, email: string) {
    const p = window.prompt(`New password for ${email} (min 8 chars):`);
    if (!p) return;
    setUBusy(true);
    setUMsg("");
    try { await api.setUserPassword(id, p); setUMsg(`Password updated for ${email}.`); }
    catch (e) { setUMsg(String(e instanceof Error ? e.message : e)); }
    finally { setUBusy(false); }
  }

  async function loadConsoles() {
    try {
      setConsoles(await api.consoles());
    } catch {
      /* ignore — section just shows empty */
    }
  }
  useEffect(() => {
    loadConsoles();
    api.agents().then(setAgents).catch(() => {});
    api.enrollmentPin().then((r) => setPin(r.pin)).catch(() => {});
    loadUsers();
    loadLive();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function regenPin() {
    setABusy(true);
    try {
      setPin((await api.regenerateEnrollmentPin()).pin);
      setPinShown(true);
    } finally {
      setABusy(false);
    }
  }

  async function save() {
    setError("");
    setBusy(true);
    try {
      await api.setUnifiKey(key.trim());
      setKey("");
      onChanged();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.deleteUnifiKey();
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function addConsole() {
    setCError("");
    setCBusy(true);
    try {
      await api.addConsole(cUrl.trim(), cKey.trim(), cLabel.trim() || "UniFi Console", cVerify);
      setCUrl("");
      setCKey("");
      setCLabel("");
      setCVerify(false);
      await loadConsoles();
      onChanged();
    } catch (e) {
      setCError(String(e instanceof Error ? e.message : e));
    } finally {
      setCBusy(false);
    }
  }

  async function removeConsole(id: string) {
    setCBusy(true);
    try {
      await api.deleteConsole(id);
      await loadConsoles();
      onChanged();
    } finally {
      setCBusy(false);
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Settings</h2>

        {/* ── Live page ───────────────────────────────────────────────────── */}
        <h3 style={{ margin: "4px 0 6px", fontSize: 15 }}>Live page</h3>
        <p style={{ marginTop: 0 }}>
          The Live tab probes these targets continuously. The <strong>probe kiosk</strong> is
          the on-lot vantage; while it sleeps the server's cloud vantage takes over.
        </p>
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
          <span className="sub" style={{ fontSize: 13 }}>Probe kiosk:</span>
          <select
            value={probeAgentId}
            onChange={(e) => pickProbeAgent(e.target.value)}
            disabled={ltBusy}
            style={{ padding: "6px", flex: 1 }}
          >
            <option value="">— none (cloud vantage only) —</option>
            {agents.filter((a) => a.claimed || a.last_seen_at).map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}{a.online ? "" : " (offline)"}
              </option>
            ))}
          </select>
        </div>
        <div style={{ marginBottom: 8 }}>
          {liveTargets.map((t) => (
            <div key={t.id} className="banner" style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, opacity: t.enabled ? 1 : 0.55 }}>
              <span className="sub" style={{ fontSize: 11, width: 34 }}>{t.kind}</span>
              <strong>{t.label}</strong>
              <span className="sub" style={{ fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{t.target}</span>
              <button className="btn" style={{ fontSize: 12, padding: "3px 8px" }} disabled={ltBusy} onClick={() => toggleLiveTarget(t)}>
                {t.enabled ? "Disable" : "Enable"}
              </button>
              <button className="btn" style={{ fontSize: 12, padding: "3px 8px" }} disabled={ltBusy} onClick={() => removeLiveTarget(t.id)}>
                Remove
              </button>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 6 }}>
          <select value={ltKind} onChange={(e) => setLtKind(e.target.value)} style={{ padding: "6px" }}>
            <option value="ping">ping</option>
            <option value="http">https</option>
          </select>
          <input placeholder="label" value={ltLabel} onChange={(e) => setLtLabel(e.target.value)} style={{ width: 140 }} />
          <input placeholder={ltKind === "http" ? "https://…" : "host / IP"} value={ltTarget}
            onChange={(e) => setLtTarget(e.target.value)} style={{ flex: 1, minWidth: 160 }} />
          <button className="btn" onClick={addLiveTarget} disabled={ltBusy || !ltTarget.trim()}>Add target</button>
        </div>
        {ltMsg ? <p className="sub" style={{ fontSize: 12 }}>{ltMsg}</p> : null}
        <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "14px 0" }} />

        {/* ── Users & roles ───────────────────────────────────────────────── */}
        <h3 style={{ margin: "4px 0 6px", fontSize: 15 }}>Users</h3>
        <p style={{ marginTop: 0 }}>
          <strong>Admins</strong> can change anything; <strong>viewers</strong> can watch
          everything but touch nothing. Server-enforced.
        </p>
        <div style={{ marginBottom: 10 }}>
          {users.map((u) => (
            <div key={u.id} className="banner" style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
              <strong style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{u.email}</strong>
              <div className="spacer" style={{ flex: 1 }} />
              <select
                value={u.role}
                onChange={(e) => changeRole(u.id, e.target.value)}
                disabled={uBusy}
                style={{ padding: "3px 6px" }}
              >
                <option value="admin">admin</option>
                <option value="viewer">viewer</option>
              </select>
              <button className="btn" onClick={() => resetPassword(u.id, u.email)} disabled={uBusy}>Password</button>
              <button className="btn" onClick={() => removeUser(u.id)} disabled={uBusy}>Remove</button>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 6 }}>
          <input type="email" placeholder="email" autoComplete="off" value={uEmail}
            onChange={(e) => setUEmail(e.target.value)} style={{ flex: 1, minWidth: 140 }} />
          <input type="password" placeholder="password (8+)" autoComplete="new-password" value={uPass}
            onChange={(e) => setUPass(e.target.value)} style={{ width: 140 }} />
          <select value={uRole} onChange={(e) => setURole(e.target.value)} style={{ padding: "6px" }}>
            <option value="viewer">viewer</option>
            <option value="admin">admin</option>
          </select>
          <button className="btn btn-primary" onClick={addUser} disabled={uBusy}>Add user</button>
        </div>
        {uMsg ? <div className="banner" style={{ marginBottom: 12 }}>{uMsg}</div> : null}

        <hr style={{ border: "none", borderTop: "1px solid var(--line)", margin: "8px 0 16px" }} />

        {/* ── Agents (kiosks) ─────────────────────────────────────────────── */}
        <h3 style={{ margin: "4px 0 6px", fontSize: 15 }}>Kiosks &amp; stations</h3>
        <p style={{ marginTop: 0 }}>
          Add a station for each kiosk here. On a kiosk's first run the agent asks for
          the <strong>enrollment PIN</strong> below, then you pick its station from a
          list — every kiosk runs the identical files, no per-kiosk tokens.
        </p>

        {/* Enrollment PIN */}
        <div className="banner" style={{ marginBottom: 14, display: "flex", alignItems: "center", gap: 10 }}>
          <span>Enrollment PIN:</span>
          <strong style={{ fontVariantNumeric: "tabular-nums", letterSpacing: 2, fontSize: 16 }}>
            {pin == null ? "…" : pinShown ? pin : "••••••"}
          </strong>
          <div className="spacer" style={{ flex: 1 }} />
          <button className="btn" onClick={() => setPinShown((v) => !v)} disabled={pin == null}>
            {pinShown ? "Hide" : "Show"}
          </button>
          <button className="btn" onClick={regenPin} disabled={aBusy}>Regenerate</button>
        </div>

        <p className="sub" style={{ marginBottom: 20 }}>
          {agents.length} station{agents.length === 1 ? "" : "s"} configured. Add, import,
          or remove them under the <strong>Kiosks</strong> tab → <strong>Manage stations</strong>.
        </p>

        <hr style={{ border: "none", borderTop: "1px solid var(--line)", margin: "8px 0 16px" }} />

        {/* ── Console connections ─────────────────────────────────────────── */}
        <h3 style={{ margin: "4px 0 6px", fontSize: 15 }}>Consoles</h3>
        <p style={{ marginTop: 0 }}>
          Connect a UniFi console by its hosting URL + a Network API key. One key
          reaches <strong>every site on that console</strong> — including sites your
          Site Manager account doesn't own. Keys are stored encrypted.
        </p>

        {consoles.length > 0 ? (
          <div style={{ marginBottom: 12 }}>
            {consoles.map((c) => (
              <div key={c.id} className="banner" style={{ marginBottom: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <strong>{c.label}</strong>
                  <span className="sub">· key {c.key_hint}</span>
                  <span className="sub">· {c.site_count} sites</span>
                  <div className="spacer" style={{ flex: 1 }} />
                  <button
                    className="btn"
                    onClick={() => removeConsole(c.id)}
                    disabled={cBusy}
                  >
                    Remove
                  </button>
                </div>
                <div className="sub" style={{ fontSize: 12, marginTop: 4 }}>
                  {c.last_error ? (
                    <span style={{ color: "var(--critical)" }}>⚠ {c.last_error}</span>
                  ) : c.last_synced_at ? (
                    <>last synced {new Date(c.last_synced_at).toLocaleString()}</>
                  ) : (
                    <>not synced yet</>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : null}

        <div className="field">
          <label htmlFor="curl">Console URL</label>
          <input
            id="curl"
            type="text"
            autoComplete="off"
            placeholder="https://<id>.unifi-hosting.ui.com"
            value={cUrl}
            onChange={(e) => setCUrl(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="ckey">Network API key</label>
          <input
            id="ckey"
            type="password"
            autoComplete="off"
            placeholder="Paste the console's Network API key"
            value={cKey}
            onChange={(e) => setCKey(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="clabel">Label (optional)</label>
          <input
            id="clabel"
            type="text"
            autoComplete="off"
            placeholder="e.g. RCS_Hosted"
            value={cLabel}
            onChange={(e) => setCLabel(e.target.value)}
          />
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, margin: "2px 0 10px" }}>
          <input type="checkbox" checked={cVerify} onChange={(e) => setCVerify(e.target.checked)} />
          Verify TLS certificate (leave off for UniFi hosting / UDM consoles)
        </label>

        {cError ? <div className="banner err" style={{ marginBottom: 12 }}>{cError}</div> : null}

        <div className="modal-actions" style={{ marginBottom: 20 }}>
          <button
            className="btn btn-primary"
            onClick={addConsole}
            disabled={cBusy || cUrl.trim().length < 5 || cKey.trim().length < 10}
          >
            {cBusy ? "Verifying…" : "Add console"}
          </button>
        </div>

        {/* ── Site Manager key ────────────────────────────────────────────── */}
        <hr style={{ border: "none", borderTop: "1px solid var(--line)", margin: "8px 0 16px" }} />
        <h3 style={{ margin: "4px 0 6px", fontSize: 15 }}>Site Manager (optional)</h3>
        <p style={{ marginTop: 0 }}>
          An account-wide Site Manager key (unifi.ui.com → profile → API) covers the
          consoles your account owns. Optional if you've connected consoles above.
        </p>

        {status?.configured ? (
          <div className="banner" style={{ marginBottom: 16 }}>
            Connected · key <strong>{status.key_hint}</strong>
            {status.last_synced_at ? (
              <> · last synced {new Date(status.last_synced_at).toLocaleString()}</>
            ) : (
              <> · not synced yet</>
            )}
          </div>
        ) : null}

        <div className="field">
          <label htmlFor="apikey">Site Manager API key</label>
          <input
            id="apikey"
            type="password"
            autoComplete="off"
            placeholder={status?.configured ? "Enter a new key to replace" : "Paste your key"}
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
        </div>

        {error ? <div className="banner err" style={{ marginBottom: 12 }}>{error}</div> : null}

        <div className="modal-actions">
          {status?.configured ? (
            <button className="btn" onClick={remove} disabled={busy}>Remove key</button>
          ) : null}
          <button className="btn" onClick={onClose} disabled={busy || cBusy}>Close</button>
          <button className="btn btn-primary" onClick={save} disabled={busy || key.trim().length < 10}>
            {busy ? "Verifying…" : "Save Site Manager key"}
          </button>
        </div>
      </div>
    </div>
  );
}
