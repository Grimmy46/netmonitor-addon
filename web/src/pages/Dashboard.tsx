import { useCallback, useEffect, useMemo, useState } from "react";
import { api, isAdmin, session, type Site, type UnifiStatus } from "../api/client";
import { PlannerView } from "../components/PlannerView";
import { PulseLogo } from "../components/PulseLogo";
import { AgentsView } from "../components/AgentsView";
import { DormantView } from "../components/DormantView";
import { SettingsModal } from "../components/SettingsModal";
import { SiteCard } from "../components/SiteCard";
import { SiteMap } from "../components/SiteMap";
import { ThemeToggle } from "../components/ThemeToggle";
import { SitePage } from "./SitePage";

type FleetFilter = "all" | "online" | "attention" | "offline";

const FLEET_FILTERS: { key: FleetFilter; label: string; match: (s: Site) => boolean }[] = [
  { key: "all", label: "All", match: () => true },
  { key: "online", label: "Online", match: (s) => s.status === "online" },
  { key: "attention", label: "Needs attention", match: (s) => s.status === "degraded" || s.status === "offline" },
  { key: "offline", label: "Offline", match: (s) => s.status === "offline" },
];

// Minimal hash router: "#/site/<id>" → that site's page; anything else → fleet.
function siteIdFromHash(): string | null {
  const m = window.location.hash.match(/^#\/site\/([^/]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

export function Dashboard() {
  const [sites, setSites] = useState<Site[]>([]);
  const [status, setStatus] = useState<UnifiStatus | null>(null);
  const [consoleCount, setConsoleCount] = useState(0);
  const [version, setVersion] = useState("");
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);

  function openSettings() {
    setSettingsOpen(true);
  }

  async function signOut() {
    try { await api.logout(); } catch { /* ignore */ }
    window.dispatchEvent(new Event("nm-unauthorized"));
  }
  const [syncing, setSyncing] = useState(false);
  const [view, setView] = useState<"fleet" | "map" | "dormant" | "kiosks" | "planner">("fleet");
  const [fleetFilter, setFleetFilter] = useState<FleetFilter>("all");
  const [siteRoute, setSiteRoute] = useState<string | null>(siteIdFromHash());

  useEffect(() => {
    const onHash = () => setSiteRoute(siteIdFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [s, st, cs] = await Promise.all([
        api.sites(),
        api.unifiStatus(),
        api.consoles().catch(() => []),
      ]);
      setSites(s);
      setStatus(st);
      setConsoleCount(cs.length);
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

  // Fleet is available once EITHER integration is connected.
  const configured = Boolean(status?.configured) || consoleCount > 0;

  async function sync() {
    setSyncing(true);
    setError("");
    try {
      // Sync whichever integrations are connected; surface any console errors.
      const jobs: Promise<unknown>[] = [];
      if (status?.configured) jobs.push(api.syncUnifi());
      if (consoleCount > 0) jobs.push(api.syncConsoles());
      const results = await Promise.allSettled(jobs);
      const failed = results.find((r) => r.status === "rejected") as
        | PromiseRejectedResult
        | undefined;
      if (failed) setError(String(failed.reason?.message ?? failed.reason));
      await refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setSyncing(false);
    }
  }

  const online = sites.filter((s) => s.status === "online").length;
  const issues = sites.filter((s) => s.status === "degraded" || s.status === "offline").length;
  const counts = useMemo(
    () => ({
      all: sites.length,
      online,
      attention: issues,
      offline: sites.filter((s) => s.status === "offline").length,
    }),
    [sites, online, issues],
  );
  const shownSites = sites.filter(FLEET_FILTERS.find((f) => f.key === fleetFilter)!.match);

  return (
    <>
      <header className="app-header">
        <PulseLogo size={26} />
        <h1>NetMonitor</h1>
        <span className="sub">2.0{version && ` · cloud v${version}`}</span>
        {configured && !siteRoute ? (
          <nav className="map-tabs" style={{ marginLeft: 12 }}>
            <button className={`tab ${view === "fleet" ? "active" : ""}`} onClick={() => setView("fleet")}>Fleet</button>
            <button className={`tab ${view === "map" ? "active" : ""}`} onClick={() => setView("map")}>Map</button>
            <button className={`tab ${view === "dormant" ? "active" : ""}`} onClick={() => setView("dormant")}>Dormant</button>
            <button className={`tab ${view === "kiosks" ? "active" : ""}`} onClick={() => setView("kiosks")}>Kiosks</button>
            <button className={`tab ${view === "planner" ? "active" : ""}`} onClick={() => setView("planner")}>Planner</button>
          </nav>
        ) : null}
        <div className="spacer" />
        <ThemeToggle />
        <span className="sub" style={{ margin: "0 4px" }}>{session.user?.email}</span>
        {isAdmin() ? <button className="btn" onClick={openSettings}>⚙ Settings</button> : null}
        <button className="btn" onClick={signOut} title="Sign out">⎋</button>
      </header>

      {siteRoute ? (
        <div className="container">
          <SitePage siteId={siteRoute} onBack={() => { window.location.hash = ""; }} />
        </div>
      ) : (
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
          {configured ? (
            <button className="btn btn-primary" onClick={sync} disabled={syncing}>
              {syncing ? "Syncing…" : "Sync now"}
            </button>
          ) : null}
        </div>

        {error ? <div className="banner err">{error}</div> : null}

        {!configured ? (
          <div className="empty">
            <p style={{ fontSize: 16, color: "var(--ink-secondary)" }}>
              Connect a UniFi console (or Site Manager account) to see your fleet.
            </p>
            <button className="btn btn-primary" onClick={openSettings}>
              Connect UniFi
            </button>
          </div>
        ) : sites.length === 0 ? (
          <div className="empty">
            <p>No sites yet. Run a sync to pull your fleet from UniFi.</p>
            <button className="btn btn-primary" onClick={sync} disabled={syncing}>
              {syncing ? "Syncing…" : "Sync now"}
            </button>
          </div>
        ) : view === "map" ? (
          <SiteMap sites={sites} />
        ) : view === "dormant" ? (
          <DormantView />
        ) : view === "kiosks" ? (
          <AgentsView />
        ) : view === "planner" ? (
          <PlannerView />
        ) : (
          <>
            <div className="filter-chips" style={{ marginBottom: 16 }}>
              {FLEET_FILTERS.map((f) => (
                <button
                  key={f.key}
                  className={`chip ${fleetFilter === f.key ? "active" : ""}`}
                  onClick={() => setFleetFilter(f.key)}
                >
                  {f.label} ({counts[f.key]})
                </button>
              ))}
            </div>
            {shownSites.length === 0 ? (
              <p className="hint">No sites match this filter.</p>
            ) : (
              <div className="grid">
                {shownSites.map((s) => (
                  <SiteCard key={s.id} site={s} />
                ))}
              </div>
            )}
          </>
        )}
      </div>
      )}

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
