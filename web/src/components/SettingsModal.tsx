import { useState } from "react";
import { api, type UnifiStatus } from "../api/client";

export function SettingsModal({
  status,
  onClose,
  onChanged,
}: {
  status: UnifiStatus | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

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

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>UniFi Site Manager</h2>
        <p>
          Paste a UniFi Site Manager API key (unifi.ui.com → profile → API). One key
          covers every site. It's stored encrypted and never shown again.
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
          <label htmlFor="apikey">API key</label>
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
          <button className="btn" onClick={onClose} disabled={busy}>Close</button>
          <button className="btn btn-primary" onClick={save} disabled={busy || key.trim().length < 10}>
            {busy ? "Verifying…" : "Save & verify"}
          </button>
        </div>
      </div>
    </div>
  );
}
