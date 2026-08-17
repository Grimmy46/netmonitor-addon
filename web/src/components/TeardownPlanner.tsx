import { useEffect, useMemo, useState } from "react";
import { api, type Site } from "../api/client";

/**
 * Teardown planner: manage per-site teardown across the UXG sites in one place.
 * Flag the critical sites (Safety, Main office …) as "keep monitored" so they
 * keep alerting off the UniFi API through any move, and arm a one-off scheduled
 * teardown time for the sites that pack up. Admin-only.
 */
function fmt(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  }) : "—";
}

// A datetime-local value (local wall time) → ISO with offset for the API.
function toIso(local: string): string | null {
  if (!local) return null;
  const d = new Date(local);
  return isNaN(d.getTime()) ? null : d.toISOString();
}

function SiteRow({ site, onChange }: { site: Site; onChange: () => void }) {
  const [when, setWhen] = useState("");
  const [busy, setBusy] = useState(false);
  const critical = site.keep_monitored;

  const run = (p: Promise<unknown>) => { setBusy(true); p.then(onChange).finally(() => setBusy(false)); };

  const state = site.teardown_active
    ? { t: `🧰 In teardown${site.teardown_auto_off_at ? ` · auto-off ${fmt(site.teardown_auto_off_at)}` : ""}`, c: "var(--warn, #b7791f)" }
    : site.teardown_scheduled_at
      ? { t: `⏰ Scheduled ${fmt(site.teardown_scheduled_at)}`, c: "var(--accent)" }
      : critical
        ? { t: "🔒 Critical — always monitored", c: "var(--good)" }
        : { t: "Alerting normally", c: "var(--ink-muted)" };

  return (
    <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <strong style={{ fontSize: 13, flex: 1 }}>{site.name}</strong>
        <span className="sub" style={{ fontSize: 12, color: state.c }}>{state.t}</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <label style={{ fontSize: 12, display: "inline-flex", alignItems: "center", gap: 5 }}>
          <input type="checkbox" checked={critical} disabled={busy}
            onChange={(e) => run(api.setSiteKeepMonitored(site.id, e.target.checked))} />
          Keep monitored (critical)
        </label>
        <div className="spacer" style={{ flex: 1 }} />
        {!critical && !site.teardown_active ? (
          <>
            <input type="datetime-local" value={when} disabled={busy}
              onChange={(e) => setWhen(e.target.value)}
              style={{ fontSize: 12, padding: "3px 6px" }} />
            <button className="btn" style={{ fontSize: 12 }} disabled={busy || !when}
              onClick={() => run(api.scheduleSiteTeardown(site.id, toIso(when), 18))}>
              Schedule
            </button>
            <button className="btn" style={{ fontSize: 12 }} disabled={busy}
              title="Put this site into teardown right now"
              onClick={() => run(api.setSiteTeardown(site.id, true, 18))}>
              Now
            </button>
          </>
        ) : null}
        {site.teardown_scheduled_at && !site.teardown_active ? (
          <button className="btn" style={{ fontSize: 12 }} disabled={busy}
            onClick={() => run(api.scheduleSiteTeardown(site.id, null))}>Cancel schedule</button>
        ) : null}
        {site.teardown_active ? (
          <button className="btn" style={{ fontSize: 12 }} disabled={busy}
            onClick={() => run(api.setSiteTeardown(site.id, false))}>End teardown</button>
        ) : null}
      </div>
    </div>
  );
}

export function TeardownPlanner({ onClose }: { onClose: () => void }) {
  const [sites, setSites] = useState<Site[] | null>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");

  const load = () => api.sites().then(setSites).catch((e) => setErr(String(e instanceof Error ? e.message : e)));
  useEffect(() => { load(); }, []);

  const shown = useMemo(() => {
    const n = q.trim().toLowerCase();
    return (sites ?? []).filter((s) => !n || s.name.toLowerCase().includes(n));
  }, [sites, q]);

  const counts = useMemo(() => {
    const s = sites ?? [];
    return {
      critical: s.filter((x) => x.keep_monitored).length,
      active: s.filter((x) => x.teardown_active).length,
      scheduled: s.filter((x) => x.teardown_scheduled_at && !x.teardown_active).length,
    };
  }, [sites]);

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" style={{ width: "min(820px, 96vw)" }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <h2 style={{ margin: 0 }}>Teardown planner</h2>
          <div className="spacer" style={{ flex: 1 }} />
          <input className="search" placeholder="Filter site…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <p className="sub" style={{ marginTop: 6, marginBottom: 10, fontSize: 12 }}>
          Flag critical sites to keep them monitored through a move (via UniFi API), and schedule
          teardown for the sites that pack up. {counts.critical} critical · {counts.scheduled} scheduled · {counts.active} in teardown.
        </p>
        {err ? <div className="banner err" style={{ marginBottom: 10 }}>{err}</div> : null}
        <div style={{ maxHeight: 460, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
          {sites === null ? (
            <p className="hint" style={{ padding: 14 }}>Loading…</p>
          ) : shown.length === 0 ? (
            <p className="hint" style={{ padding: 14 }}>No sites.</p>
          ) : (
            shown.map((s) => <SiteRow key={s.id} site={s} onChange={load} />)
          )}
        </div>
        <div className="modal-actions" style={{ marginTop: 16 }}>
          <button className="btn" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
