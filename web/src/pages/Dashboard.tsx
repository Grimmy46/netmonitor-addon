import { useCallback, useEffect, useMemo, useState } from "react";
import { api, isAdmin, session, type Site, type TeardownStatus, type UnifiStatus } from "../api/client";
import { PlannerView } from "../components/PlannerView";
import { TeardownPlanner } from "../components/TeardownPlanner";
import { PulseLogo } from "../components/PulseLogo";
import { AgentsView } from "../components/AgentsView";
import { DormantView } from "../components/DormantView";
import { MainHealth } from "../components/MainHealth";
import { LiveView } from "../components/LiveView";
import { NotifyBell } from "../components/NotifyBell";
import { SettingsModal } from "../components/SettingsModal";
import { SiteCard } from "../components/SiteCard";
import { MapTab } from "../components/GeoMap";
import { ThemeToggle } from "../components/ThemeToggle";
import { SitePage } from "./SitePage";

type View = "live" | "fleet" | "map" | "dormant" | "kiosks" | "ticketboxes" | "planner";
const VIEWS: { key: View; label: string; icon: string; mobile: boolean; kiosk?: boolean }[] = [
  { key: "live", label: "Live", icon: "📈", mobile: true },
  { key: "map", label: "MAP", icon: "🗺", mobile: true },
  { key: "kiosks", label: "Kiosks", icon: "🖥", mobile: true, kiosk: true },
  { key: "fleet", label: "Fleet", icon: "🌐", mobile: true },
  { key: "ticketboxes", label: "Ticket Boxes", icon: "🎟", mobile: false, kiosk: true },
  { key: "dormant", label: "Dormant", icon: "💤", mobile: false },
];

type FleetFilter = "all" | "online" | "attention" | "offline" | "dormant";

// Dormant (packed-up) venues drop out of every active filter into their own chip.
const FLEET_FILTERS: { key: FleetFilter; label: string; match: (s: Site) => boolean }[] = [
  { key: "all", label: "All", match: (s) => !s.dormant },
  { key: "online", label: "Online", match: (s) => s.status === "online" && !s.dormant },
  { key: "attention", label: "Needs attention", match: (s) => (s.status === "degraded" || s.status === "offline") && !s.dormant },
  { key: "offline", label: "Offline", match: (s) => s.status === "offline" && !s.dormant },
  { key: "dormant", label: "Dormant", match: (s) => s.dormant },
];

// Minimal hash router: "#/site/<id>" → that site's page; anything else → fleet.
function siteIdFromHash(): string | null {
  const m = window.location.hash.match(/^#\/site\/([^/?]+)/);
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
  const [view, setViewRaw] = useState<View>(() => {
    const v = localStorage.getItem("nm-view") as View | null;
    return v && VIEWS.some((x) => x.key === v) ? v : "live";
  });
  const setView = (v: View) => { setViewRaw(v); setMoreOpen(false); localStorage.setItem("nm-view", v); };
  const [moreOpen, setMoreOpen] = useState(false);
  const [fleetFilter, setFleetFilter] = useState<FleetFilter>("all");
  const [siteRoute, setSiteRoute] = useState<string | null>(siteIdFromHash());
  const [teardown, setTeardown] = useState<TeardownStatus | null>(null);
  const [showPlanner, setShowPlanner] = useState(false);
  const td = !!teardown?.active;

  const toggleTeardown = () => {
    const next = !td;
    if (next && !window.confirm("Start teardown mode? Fault alerts pause and the dashboard focuses on what's still online.")) return;
    api.setTeardown(next).then(setTeardown).catch(() => {});
  };

  useEffect(() => {
    const onHash = () => setSiteRoute(siteIdFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // In teardown, the kiosk-level tabs are hidden — bounce off them to the
  // "what's still online" fleet view.
  useEffect(() => {
    if (td && (view === "kiosks" || view === "ticketboxes")) {
      setView("fleet");
      setFleetFilter("online");
    }
  }, [td, view]);

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
      api.getTeardown().then(setTeardown).catch(() => {});
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

  const counts = useMemo(
    () => Object.fromEntries(FLEET_FILTERS.map((f) => [f.key, sites.filter(f.match).length])) as Record<FleetFilter, number>,
    [sites],
  );
  const online = counts.online ?? 0;
  const issues = counts.attention ?? 0;
  const shownSites = sites.filter(FLEET_FILTERS.find((f) => f.key === fleetFilter)!.match);

  const navItems = VIEWS.filter((v) => !(td && v.kiosk));
  const viewLabel = VIEWS.find((v) => v.key === view)?.label ?? "";
  const badge = (k: View) => (k === "fleet" && issues > 0 ? <span className="nav-badge">{issues}</span> : null);

  return (
    <div className={`shell${siteRoute ? " shell-site" : ""}`}>
      {/* Desktop: left rail. Hidden on phones (bottom bar instead). */}
      <aside className="side-nav">
        <div className="side-brand">
          <PulseLogo size={24} />
          <span>NetMonitor</span>
        </div>
        {configured ? (
          <nav className="side-items">
            {navItems.map((v) => (
              <button key={v.key} className={`side-item${view === v.key && !siteRoute ? " active" : ""}`}
                onClick={() => { window.location.hash = ""; setView(v.key); }}>
                <span className="side-ico">{v.icon}</span>{v.label}{badge(v.key)}
              </button>
            ))}
          </nav>
        ) : null}
        <div className="spacer" />
        <div className="side-foot">
          {isAdmin() ? (
            <>
              <button className={`side-item${td ? " warn" : ""}`} onClick={toggleTeardown}>
                <span className="side-ico">🧰</span>{td ? "Teardown ON" : "Teardown"}
              </button>
              <button className="side-item" onClick={() => setShowPlanner(true)}><span className="side-ico">🗓</span>Planner</button>
              <button className="side-item" onClick={openSettings}><span className="side-ico">⚙</span>Settings</button>
            </>
          ) : null}
          <div className="side-user">
            <span className="sub" title={session.user?.email}>{session.user?.email}</span>
            <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 6 }}>
              <ThemeToggle />
              <button className="btn btn-xs" onClick={signOut} title="Sign out">⎋ Sign out</button>
            </div>
            <span className="sub" style={{ fontSize: 11 }}>2.0{version && ` · v${version}`}</span>
          </div>
        </div>
      </aside>

      <div className="main-col">
      <header className="app-header">
        <span className="mobile-only"><PulseLogo size={22} /></span>
        <h1>{siteRoute ? "Site" : viewLabel}</h1>
        {sites.length > 0 && !siteRoute && view === "fleet" ? (
          <span className="sub hide-sm">{sites.length} sites · {online} online{issues > 0 ? ` · ${issues} need attention` : ""}</span>
        ) : null}
        <div className="spacer" />
        {configured && view === "fleet" && !siteRoute ? (
          <button className="btn btn-primary" onClick={sync} disabled={syncing}>{syncing ? "Syncing…" : "Sync now"}</button>
        ) : null}
        <NotifyBell />
        <button className="btn mobile-only" onClick={() => setMoreOpen(true)} aria-label="Menu">☰</button>
      </header>

      {siteRoute ? (
        <div className="container">
          <SitePage siteId={siteRoute} onBack={() => { window.location.hash = ""; }} />
        </div>
      ) : (
      <div className="container">
        {td ? (
          <div className="banner" style={{ marginBottom: 14, display: "flex", alignItems: "center", gap: 10, borderLeft: "3px solid var(--warn, #b7791f)" }}>
            <span style={{ fontSize: 18 }}>🧰</span>
            <span style={{ flex: 1 }}>
              <strong>Teardown mode</strong> — fault alerts paused; kiosk views hidden.
              {teardown ? <span className="sub" style={{ fontSize: 12 }}> · {teardown.offline}/{teardown.total} stations offline</span> : null}
            </span>
            <button className="btn" style={{ fontSize: 12, padding: "3px 10px" }} onClick={toggleTeardown}>End</button>
          </div>
        ) : null}
        {error ? <div className="banner err">{error}</div> : null}

        {view === "live" ? (
          <>
            <MainHealth />
            <LiveView onOpenSettings={isAdmin() ? openSettings : undefined} />
          </>
        ) : !configured ? (
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
          <MapTab />
        ) : view === "dormant" ? (
          <DormantView />
        ) : view === "kiosks" ? (
          <AgentsView group="kiosk" />
        ) : view === "ticketboxes" ? (
          <AgentsView group="ticketbox" />
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

      </div>

      {/* Phones: bottom tab bar + a "More" sheet for everything else. */}
      {configured ? (
        <nav className="bottom-nav">
          {navItems.filter((v) => v.mobile).map((v) => (
            <button key={v.key} className={view === v.key && !siteRoute ? "active" : ""}
              onClick={() => { window.location.hash = ""; setView(v.key); }}>
              <span className="bn-ico">{v.icon}</span><span>{v.label}</span>{badge(v.key)}
            </button>
          ))}
          <button className={moreOpen ? "active" : ""} onClick={() => setMoreOpen(true)}>
            <span className="bn-ico">⋯</span><span>More</span>
          </button>
        </nav>
      ) : null}
      {moreOpen ? (
        <div className="overlay sheet-overlay" onClick={() => setMoreOpen(false)}>
          <div className="sheet more-sheet" onClick={(e) => e.stopPropagation()}>
            <div className="sheet-head"><h2>More</h2><span className="spacer" /><button className="btn" onClick={() => setMoreOpen(false)}>✕</button></div>
            {navItems.filter((v) => !v.mobile).map((v) => (
              <button key={v.key} className="sheet-row" onClick={() => { window.location.hash = ""; setView(v.key); }}>{v.icon} {v.label}</button>
            ))}
            {isAdmin() ? (
              <>
                <button className="sheet-row" onClick={() => { setMoreOpen(false); toggleTeardown(); }}>🧰 {td ? "End teardown" : "Teardown mode"}</button>
                <button className="sheet-row" onClick={() => { setMoreOpen(false); setShowPlanner(true); }}>🗓 Teardown planner</button>
                <button className="sheet-row" onClick={() => { setMoreOpen(false); openSettings(); }}>⚙ Settings</button>
              </>
            ) : null}
            <div className="sheet-row" style={{ display: "flex", alignItems: "center", gap: 10 }}>Theme <ThemeToggle /></div>
            <button className="sheet-row" onClick={signOut}>⎋ Sign out <span className="sub">{session.user?.email}</span></button>
          </div>
        </div>
      ) : null}

      {settingsOpen ? (
        <SettingsModal
          status={status}
          onClose={() => setSettingsOpen(false)}
          onChanged={refresh}
        />
      ) : null}
      {showPlanner ? (
        <TeardownPlanner onClose={() => { setShowPlanner(false); refresh(); }} />
      ) : null}
    </div>
  );
}
