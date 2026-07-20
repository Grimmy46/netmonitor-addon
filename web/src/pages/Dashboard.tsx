import { useCallback, useEffect, useState } from "react";
import { api, type Site, type UnifiStatus } from "../api/client";
import { SettingsModal } from "../components/SettingsModal";
import { SiteCard } from "../components/SiteCard";
import { ThemeToggle } from "../components/ThemeToggle";

export function Dashboard() {
  const [sites, setSites] = useState<Site[]>([]);
  const [status, setStatus] = useState<UnifiStatus | null>(null);
  const [version, setVersion] = useState("");
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [s, st] = await Promise.all([api.sites(), api.unifiStatus()]);
      setSites(s);
      setStatus(st);
      setError("");
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }, []);

  useEffect(() => {
    api.health().then((h) => setVersion(h.version)).catch(() => {});
    refresh();
    const id = setInterval(refresh, 15000); // live-ish refresh
    return () => clearInterval(id);
  }, [refresh]);

  async function sync() {
    setSyncing(true);
    setError("");
    try {
      await api.syncUnifi();
      await refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setSyncing(false);
    }
  }

  const online = sites.filter((s) => s.status === "online").length;
  const issues = sites.filter((s) => s.status === "degraded" || s.status === "offline").length;

  return (
    <>
      <header className="app-header">
        <h1>NetMonitor</h1>
        <span className="sub">2.0{version && ` · cloud v${version}`}</span>
        <div className="spacer" />
        <ThemeToggle />
        <button className="btn" onClick={() => setSettingsOpen(true)}>⚙ Settings</button>
      </header>

      <div className="container">
        <div className="toolbar">
          <div>
            {sites.length > 0 ? (
              <span className="sub" style={{ fontSize: 14 }}>
                {sites.length} sites · {online} online{issues > 0 ? ` · ${issues} need attention` : ""}
              </span>
            ) : null}
          </div>
          <div className="spacer" />
          {status?.configured ? (
            <button className="btn btn-primary" onClick={sync} disabled={syncing}>
              {syncing ? "Syncing…" : "Sync now"}
            </button>
          ) : null}
        </div>

        {error ? <div className="banner err">{error}</div> : null}

        {!status?.configured ? (
          <div className="empty">
            <p style={{ fontSize: 16, color: "var(--ink-secondary)" }}>
              Connect your UniFi Site Manager account to see your fleet.
            </p>
            <button className="btn btn-primary" onClick={() => setSettingsOpen(true)}>
              Add UniFi API key
            </button>
          </div>
        ) : sites.length === 0 ? (
          <div className="empty">
            <p>No sites yet. Run a sync to pull your fleet from UniFi.</p>
            <button className="btn btn-primary" onClick={sync} disabled={syncing}>
              {syncing ? "Syncing…" : "Sync now"}
            </button>
          </div>
        ) : (
          <div className="grid">
            {sites.map((s) => (
              <SiteCard key={s.id} site={s} />
            ))}
          </div>
        )}
      </div>

      {settingsOpen ? (
        <SettingsModal
          status={status}
          onClose={() => setSettingsOpen(false)}
          onChanged={refresh}
        />
      ) : null}
    </>
  );
}
