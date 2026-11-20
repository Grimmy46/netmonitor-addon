import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, isAdmin, session, type NetworkMode, type NetworkModeKey, type Site, type TeardownStatus, type UnifiStatus } from "../api/client";
import { PlannerView } from "../components/PlannerView";
import { TeardownPlanner } from "../components/TeardownPlanner";
import { PulseLogo } from "../components/PulseLogo";
import { AgentsView } from "../components/AgentsView";
import { CamerasView } from "../components/CamerasView";
import { DormantView } from "../components/DormantView";
import { ServerView } from "../components/ServerView";
import { MainHealth } from "../components/MainHealth";
import { LiveView } from "../components/LiveView";
import { NotifyBell } from "../components/NotifyBell";
import { GuestShareButton } from "../components/GuestShareButton";
import { SettingsModal } from "../components/SettingsModal";
import { SiteCard } from "../components/SiteCard";
import { MapTab } from "../components/GeoMap";
import { ThemeToggle } from "../components/ThemeToggle";
import { SitePage } from "./SitePage";
import { ComingSoon, ModeCard, ModeDialog, ModeTabs, ModeUnknownCard } from "../components/ModeControl";
import { MODE_LABEL, TABS, describeEffects, tabForMode, type Tab } from "../lib/modes";

type View =
  | "live" | "fleet" | "map" | "dormant" | "kiosks" | "cameras" | "ticketboxes" | "server"
  | "planner" | "setupchecklist" | "cabletests"
  | "teardown" | "stillout" | "photolog";
type ViewDef = { key: View; tab: Tab; label: string; short?: string; icon: string; mobile: boolean; kiosk?: boolean };
// Left menu, grouped under the three main tabs. Order = menu order.
const VIEWS: ViewDef[] = [
  { key: "live", tab: "open", label: "Live", icon: "📈", mobile: true },
  { key: "map", tab: "open", label: "Map", icon: "🗺", mobile: true },
  { key: "kiosks", tab: "open", label: "Kiosks", icon: "🖥", mobile: true, kiosk: true },
  { key: "cameras", tab: "open", label: "Cameras", icon: "📷", mobile: true },
  { key: "fleet", tab: "open", label: "Fleet", icon: "🌐", mobile: true },
  { key: "ticketboxes", tab: "open", label: "Ticket Boxes", short: "Tickets", icon: "🎟", mobile: true, kiosk: true },
  { key: "dormant", tab: "open", label: "Dormant", icon: "💤", mobile: false },
  { key: "server", tab: "open", label: "Server", icon: "🖧", mobile: false },
  { key: "planner", tab: "setup", label: "Planner", icon: "🗓", mobile: true },
  { key: "setupchecklist", tab: "setup", label: "Setup checklist", short: "Checklist", icon: "☑", mobile: true },
  { key: "cabletests", tab: "setup", label: "Cable tests", icon: "🔌", mobile: true },
  { key: "teardown", tab: "teardown", label: "Teardown", icon: "🧰", mobile: true },
  { key: "stillout", tab: "teardown", label: "Still out", icon: "📦", mobile: true },
  { key: "photolog", tab: "teardown", label: "Photo log", icon: "📸", mobile: true },
];
const viewDef = (v: View) => VIEWS.find((x) => x.key === v)!;
const isView = (v: unknown): v is View => VIEWS.some((x) => x.key === v);
const firstView = (t: Tab): View => VIEWS.find((x) => x.tab === t)!.key;

// Last view used in each tab, so switching tabs returns you where you were.
function lastViewIn(t: Tab): View {
  try {
    const v = localStorage.getItem(`nm-view-${t}`);
    if (isView(v) && viewDef(v).tab === t) return v;
  } catch { /* private mode */ }
  return firstView(t);
}

type FleetFilter = "all" | "online" | "attention" | "offline" | "dormant";

// Dormant (packed-up) venues drop out of every active filter into their own chip.
const FLEET_FILTERS: { key: FleetFilter; label: string; match: (s: Site) => boolean }[] = [
  { key: "all", label: "All", match: (s) => !s.dormant },
  { key: "online", label: "Online", match: (s) => s.status === "online" && !s.dormant },
  { key: "attention", label: "Needs attention", match: (s) => (s.status === "degraded" || s.status === "offline") && !s.dormant },
  { key: "offline", label: "Offline", match: (s) => s.status === "offline" && !s.dormant },
  { key: "dormant", label: "Dormant", match: (s) => s.dormant },
];

// Minimal hash router: "#/site/<id>" → that site's page; "#/<tab>/<view>"
// (e.g. #/open/map, #/setup/planner) → that view; anything else → last view.
function siteIdFromHash(): string | null {
  const m = window.location.hash.match(/^#\/site\/([^/?]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}
function viewFromHash(): View | null {
  const m = window.location.hash.match(/^#\/(setup|open|teardown)(?:\/([\w-]+))?/);
  if (!m) return null;
  const t = m[1] as Tab;
  return isView(m[2]) && viewDef(m[2]).tab === t ? m[2] : lastViewIn(t);
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
    const h = viewFromHash();
    if (h) return h;
    const v = localStorage.getItem("nm-view");
    return isView(v) ? v : "live";
  });
  const setView = (v: View) => {
    setViewRaw(v);
    setMoreOpen(false);
    try {
      localStorage.setItem("nm-view", v);
      localStorage.setItem(`nm-view-${viewDef(v).tab}`, v);
    } catch { /* private mode */ }
  };
  const tab = viewDef(view).tab;
  // Menu click: leave a site page if open, and keep a shareable #/<tab>/<view> URL.
  const go = (v: View) => {
    const h = `#/${viewDef(v).tab}/${v}`;
    if (siteIdFromHash()) window.location.hash = h;
    else window.history.replaceState(null, "", h);
    setView(v);
  };
  const [moreOpen, setMoreOpen] = useState(false);
  const [fleetFilter, setFleetFilter] = useState<FleetFilter>("all");
  const [siteRoute, setSiteRoute] = useState<string | null>(siteIdFromHash());
  const [teardown, setTeardown] = useState<TeardownStatus | null>(null);
  const td = !!teardown?.active;

  // Network mode (Setup / Open / Teardown / Off-season). If the server doesn't
  // have the endpoint yet, fall back to "open" and hide the admin control.
  const [mode, setMode] = useState<NetworkMode | null>(null);
  // ok = loaded; missing = 404 (old server, fall back to "open");
  // error = anything else (keep the last mode as stale, retry on refresh).
  const [modeApi, setModeApi] = useState<"unknown" | "ok" | "missing" | "error">("unknown");
  const modeApiRef = useRef(modeApi);
  modeApiRef.current = modeApi;
  const [modeDialog, setModeDialog] = useState<NetworkModeKey | "current" | null>(null);
  const [modeNote, setModeNote] = useState<string>("");
  const liveTab: Tab | null = modeApi === "ok" && mode ? tabForMode(mode.mode) : modeApi === "missing" ? "open" : null;
  const canSwitchMode = isAdmin() && modeApi === "ok" && !!mode;

  // Auto-jump to the new live tab when the mode changes: on first load (if it
  // changed since this browser last saw it), when another admin switches it
  // mid-session, and after our own switch. Never off a site page, and not on
  // first load when the URL named a view.
  const tabRef = useRef(tab);
  tabRef.current = tab;
  const goRef = useRef(go);
  goRef.current = go;
  const urlViewAtLoad = useRef(viewFromHash() !== null || siteIdFromHash() !== null);
  const lastModeRef = useRef<NetworkModeKey | null>(null);
  const noteModeChange = useCallback((m: NetworkMode, own: boolean) => {
    const prev = lastModeRef.current;
    lastModeRef.current = m.mode;
    let seen: string | null = null;
    try { seen = localStorage.getItem("nm-mode-seen"); localStorage.setItem("nm-mode-seen", m.mode); } catch { /* private mode */ }
    const first = prev === null && !own;
    if (first ? seen === m.mode : prev === m.mode) return;
    const t = tabForMode(m.mode);
    if (!t || siteIdFromHash() || (first && urlViewAtLoad.current) || tabRef.current === t) return;
    goRef.current(lastViewIn(t));
    if (!first && !own) setModeNote(`Mode changed to ${MODE_LABEL[m.mode]}${m.changed_by ? ` by ${m.changed_by}` : ""}. Showing the ${MODE_LABEL[m.mode]} tab.`);
  }, []);

  const loadMode = useCallback(() => {
    api.networkMode()
      .then((m) => { setMode(m); setModeApi("ok"); noteModeChange(m, false); })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) { setMode(null); setModeApi("missing"); }
        else setModeApi("error");
      });
  }, [noteModeChange]);

  const onModeSaved = (m: NetworkMode) => {
    setMode(m);
    setModeApi("ok");
    setModeDialog(null);
    const fx = describeEffects(m.effects).map((r) => `${r.label}: ${r.value.toLowerCase()}`).join(" · ");
    setModeNote(`Mode is now ${MODE_LABEL[m.mode]}.${fx ? ` ${fx}.` : ""}`);
    noteModeChange(m, true);
    refresh();
  };

  const toggleTeardown = () => {
    const next = !td;
    if (next && !window.confirm("Start teardown mode? Fault alerts pause and the dashboard focuses on what's still online.")) return;
    api.setTeardown(next).then(setTeardown).catch(() => {});
  };

  useEffect(() => {
    const onHash = () => {
      setSiteRoute(siteIdFromHash());
      const v = viewFromHash();
      if (v) setView(v);
    };
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

  useEffect(() => { loadMode(); }, [loadMode]);

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
    const id = setInterval(() => {
      refresh();
      if (modeApiRef.current !== "missing") loadMode(); // ok: keep fresh; error: retry
    }, 15000); // live-ish refresh
    return () => clearInterval(id);
  }, [refresh, loadMode]);

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

  // In legacy teardown (agents/teardown), the kiosk-level views are hidden.
  const hidden = (v: ViewDef) => td && !!v.kiosk;
  const navItems = VIEWS.filter((v) => v.tab === tab && !hidden(v));
  const viewLabel = viewDef(view).label;
  const badge = (k: View) => (k === "fleet" && issues > 0 ? <span className="nav-badge">{issues}</span> : null);
  const pickTab = (t: Tab) => go(lastViewIn(t));
  const tabLabel = TABS.find((t) => t.key === tab)!.label;
  // Pages that need the UniFi fleet; the rest (Live, Setup/Teardown placeholders) render regardless.
  // Planner and Teardown work without UniFi too (they show an empty site list).
  const needsFleet = !["live", "planner", "setupchecklist", "cabletests", "teardown", "stillout", "photolog"].includes(view);

  const modeBlock = mode ? (
    <ModeCard mode={mode} stale={modeApi === "error"} canChange={canSwitchMode} onChange={() => setModeDialog("current")} />
  ) : modeApi === "error" ? <ModeUnknownCard /> : null;

  return (
    <div className={`shell${siteRoute ? " shell-site" : ""}`}>
      {/* Desktop: left rail. Hidden on phones (bottom bar instead). */}
      <aside className="side-nav">
        <div className="side-brand">
          <PulseLogo size={24} />
          <span>NetMonitor</span>
        </div>
        <div className="side-section">
          <span>{tabLabel}</span>
          {liveTab === tab ? <span className="side-section-live">live</span> : null}
        </div>
        <nav className="side-items">
          {navItems.map((v) => (
            <button key={v.key} className={`side-item${view === v.key && !siteRoute ? " active" : ""}`}
              onClick={() => go(v.key)}>
              <span className="side-ico">{v.icon}</span>{v.label}{badge(v.key)}
            </button>
          ))}
        </nav>
        <div className="spacer" />
        <div className="side-foot">
          {modeBlock}
          {isAdmin() ? (
            <>
              {modeApi === "missing" ? (
                <button className={`side-item${td ? " warn" : ""}`} onClick={toggleTeardown}>
                  <span className="side-ico">🧰</span>{td ? "Teardown ON" : "Teardown mode"}
                </button>
              ) : null}
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
        <ModeTabs tab={tab} live={liveTab} onPick={pickTab} />
        <h1>{siteRoute ? "Site" : viewLabel}</h1>
        {sites.length > 0 && !siteRoute && view === "fleet" ? (
          <span className="sub hide-sm">{sites.length} sites · {online} online{issues > 0 ? ` · ${issues} need attention` : ""}</span>
        ) : null}
        {modeApi === "error" ? (
          <span className="offseason-tag mode-unknown-tag" title="Can't reach the server. Trying again.">Mode unknown</span>
        ) : modeApi === "ok" && mode?.mode === "offseason" ? <span className="offseason-tag hide-sm">Off-season</span> : null}
        <div className="spacer" />
        {configured && view === "fleet" && !siteRoute ? (
          <button className="btn btn-primary" onClick={sync} disabled={syncing} title="Sync now">
            {syncing ? "…" : "⟳"}<span className="hide-sm">{syncing ? " Syncing" : " Sync now"}</span>
          </button>
        ) : null}
        {isAdmin() ? <GuestShareButton /> : null}
        <NotifyBell />
        <button className="btn mobile-only" onClick={() => setMoreOpen(true)} aria-label="Menu">☰</button>
      </header>

      {siteRoute ? (
        <div className="container">
          <SitePage siteId={siteRoute} onBack={() => { window.location.hash = ""; }} />
        </div>
      ) : (
      <div className="container">
        {modeNote ? (
          <div className="banner mode-note">
            <span style={{ flex: 1 }}>{modeNote}</span>
            <button className="btn btn-xs" onClick={() => setModeNote("")} aria-label="Dismiss">✕</button>
          </div>
        ) : null}
        {modeApi === "ok" && mode && liveTab !== tab ? (
          <div className="mode-strip">
            <span>
              Viewing <b>{tabLabel}</b>. The network is in <b>{MODE_LABEL[mode.mode]}</b> mode.
            </span>
            {canSwitchMode ? (
              <button className="btn btn-xs" onClick={() => setModeDialog(tab)}>Switch to {tabLabel}…</button>
            ) : null}
          </div>
        ) : null}
        {td ? (
          <div className="banner" style={{ marginBottom: 14, display: "flex", alignItems: "center", gap: 10, borderLeft: "3px solid var(--warn, #b7791f)" }}>
            <span style={{ fontSize: 18 }}>🧰</span>
            <span style={{ flex: 1 }}>
              <strong>Teardown mode</strong> — fault alerts paused; kiosk views hidden.
              {teardown ? <span className="sub" style={{ fontSize: 12 }}> · {teardown.offline}/{teardown.total} stations offline</span> : null}
            </span>
            {isAdmin() ? (
              <button className="btn" style={{ fontSize: 12, padding: "3px 10px" }}
                onClick={canSwitchMode ? () => setModeDialog("open") : toggleTeardown}>End</button>
            ) : null}
          </div>
        ) : null}
        {error ? <div className="banner err">{error}</div> : null}

        {view === "live" ? (
          <>
            <MainHealth />
            <LiveView onOpenSettings={isAdmin() ? openSettings : undefined} />
          </>
        ) : view === "setupchecklist" ? (
          <ComingSoon title="Setup checklist" text="Every device in the site plan becomes a checklist item. The crew ticks them off as they go in, by hand or by posting a photo to the bot." />
        ) : view === "cabletests" ? (
          <ComingSoon title="Cable tests" text="Log a speed test for each cable run (for example Ticket Box 1 at 10 Mbps). Slow runs get flagged before opening." />
        ) : view === "stillout" ? (
          <ComingSoon title="Still out" text="Everything deployed at this event, ticked off as it comes back to the truck." />
        ) : view === "photolog" ? (
          <ComingSoon title="Photo log" text="Teardown photos from the crew, with the tag or label the bot read from each one." />
        ) : needsFleet && !configured ? (
          <div className="empty">
            <p style={{ fontSize: 16, color: "var(--ink-secondary)" }}>
              Connect a UniFi console (or Site Manager account) to see your fleet.
            </p>
            <button className="btn btn-primary" onClick={openSettings}>
              Connect UniFi
            </button>
          </div>
        ) : needsFleet && sites.length === 0 ? (
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
        ) : view === "server" ? (
          <ServerView />
        ) : view === "kiosks" ? (
          <AgentsView group="kiosk" />
        ) : view === "cameras" ? (
          <CamerasView />
        ) : view === "ticketboxes" ? (
          <AgentsView group="ticketbox" />
        ) : view === "planner" ? (
          <PlannerView />
        ) : view === "teardown" ? (
          <TeardownPlanner inline />
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

      {/* Phones: bottom tab bar (current tab's pages) + a "More" sheet for everything else. */}
      <nav className="bottom-nav">
        {navItems.filter((v) => v.mobile).map((v) => (
          <button key={v.key} className={view === v.key && !siteRoute ? "active" : ""}
            onClick={() => go(v.key)}>
            <span className="bn-ico">{v.icon}</span><span className="bn-lbl">{v.short ?? v.label}</span>{badge(v.key)}
          </button>
        ))}
        <button className={moreOpen ? "active" : ""} onClick={() => setMoreOpen(true)}>
          <span className="bn-ico">⋯</span><span className="bn-lbl">More</span>
        </button>
      </nav>
      {moreOpen ? (
        <div className="overlay sheet-overlay" onClick={() => setMoreOpen(false)}>
          <div className="sheet more-sheet" onClick={(e) => e.stopPropagation()}>
            <div className="sheet-head"><h2>More</h2><span className="spacer" /><button className="btn" onClick={() => setMoreOpen(false)}>✕</button></div>
            {modeBlock ? <div style={{ margin: "4px 0 8px" }}>{modeBlock}</div> : null}
            {navItems.filter((v) => !v.mobile).map((v) => (
              <button key={v.key} className="sheet-row" onClick={() => go(v.key)}>{v.icon} {v.label}</button>
            ))}
            {isAdmin() ? (
              <>
                {modeApi === "missing" ? (
                  <button className="sheet-row" onClick={() => { setMoreOpen(false); toggleTeardown(); }}>🧰 {td ? "End teardown" : "Teardown mode"}</button>
                ) : null}
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
      {modeDialog && mode ? (
        <ModeDialog
          current={mode}
          initial={modeDialog === "current" ? undefined : modeDialog}
          onClose={() => setModeDialog(null)}
          onSaved={onModeSaved}
          onError={loadMode}
        />
      ) : null}
    </div>
  );
}
