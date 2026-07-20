import { useEffect, useState } from "react";
import { api, type Site } from "./api/client";

export function App() {
  const [sites, setSites] = useState<Site[]>([]);
  const [version, setVersion] = useState<string>("");
  const [error, setError] = useState<string>("");

  useEffect(() => {
    api.health().then((h) => setVersion(h.version)).catch(() => {});
    api.sites().then(setSites).catch((e) => setError(String(e)));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 900, margin: "40px auto", padding: 16 }}>
      <header style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
        <h1 style={{ margin: 0 }}>NetMonitor</h1>
        <span style={{ color: "#888", fontSize: 14 }}>
          2.0 {version && `· cloud v${version}`}
        </span>
      </header>
      <p style={{ color: "#666" }}>
        Multi-site dashboard. Phase 0 scaffold — connect a UniFi Site Manager API
        key to populate sites (Phase 1).
      </p>

      {error && (
        <p style={{ color: "#b00" }}>
          Couldn’t reach the API ({error}). Is the cloud service running?
        </p>
      )}

      <h2>Sites {sites.length > 0 && `(${sites.length})`}</h2>
      {sites.length === 0 ? (
        <p style={{ color: "#888" }}>No sites yet.</p>
      ) : (
        <ul>
          {sites.map((s) => (
            <li key={s.id}>
              <strong>{s.name}</strong> — {s.status} · {s.device_count} devices
              {s.isp_name ? ` · ${s.isp_name}` : ""}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
