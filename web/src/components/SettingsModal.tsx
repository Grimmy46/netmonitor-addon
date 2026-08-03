import { useEffect, useState } from "react";
import { api, type UnifiConsole, type UnifiStatus } from "../api/client";

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

  async function loadConsoles() {
    try {
      setConsoles(await api.consoles());
    } catch {
      /* ignore — section just shows empty */
    }
  }
  useEffect(() => {
    loadConsoles();
  }, []);

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
        <h2>UniFi connections</h2>

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
