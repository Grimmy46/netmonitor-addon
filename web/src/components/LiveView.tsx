import { useEffect, useMemo, useRef, useState } from "react";
import { api, isAdmin, type LiveFeed, type LiveFeedTarget, type LiveSample } from "../api/client";
import { LiveChart } from "./LiveChart";

/**
 * The Live landing page — the spiritual successor to NetMonitor v5.5's
 * dashboard: a status banner, headline tiles, and a continuously-scrolling
 * live chart per probe target. Data arrives in ~5–10s batches from the
 * designated kiosk (on-lot vantage) or the server prober (cloud vantage,
 * keeps the page alive overnight); the charts glide at constant speed
 * regardless (see LiveChart).
 */

const WINDOW_SEC = 300; // 5 minutes on screen

function mergeSamples(prev: LiveSample[], incoming: LiveSample[]): LiveSample[] {
  // Feed polls overlap on purpose; dedupe by timestamp and keep a bounded buffer.
  const seen = new Set(prev.map((s) => s.ts));
  const merged = prev.concat(incoming.filter((s) => !seen.has(s.ts)));
  merged.sort((a, b) => a.ts - b.ts);
  const cutoff = Date.now() / 1000 - WINDOW_SEC - 60;
  return merged.filter((s) => s.ts >= cutoff);
}

function TargetCard({ t, buffer }: { t: LiveFeedTarget; buffer: LiveSample[] }) {
  const state =
    t.ok === null ? { label: "NO DATA", color: "var(--ink-muted)" }
    : t.ok ? { label: "OK", color: "var(--good)" }
    : { label: "DOWN", color: "var(--critical)" };
  return (
    <div className="panel" style={{ padding: "12px 14px", borderLeft: `4px solid ${state.color}` }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 4, minWidth: 0 }}>
        <span style={{ fontWeight: 600, whiteSpace: "nowrap" }}>{t.label}</span>
        <span
          className="sub"
          style={{
            fontSize: 12, flex: 1, minWidth: 0,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}
        >
          {t.kind === "http" ? "https" : "ping"} · {t.target === "gateway" ? "site gateway" : t.target.replace(/^https?:\/\//, "")}
        </span>
        {t.vantage !== "none" ? (
          <span className="sub" style={{ fontSize: 11, letterSpacing: "0.04em" }}>
            {t.vantage === "local" ? "ON-LOT" : "CLOUD"}
          </span>
        ) : null}
        <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 600 }}>
          {t.last_ms != null ? `${t.last_ms.toFixed(t.last_ms < 10 ? 1 : 0)} ms` : "—"}
        </span>
        <span
          style={{
            fontSize: 11, fontWeight: 700, letterSpacing: "0.05em",
            color: state.color, border: `1px solid ${state.color}`,
            borderRadius: 999, padding: "2px 8px",
          }}
        >
          {state.label}
        </span>
      </div>
      <LiveChart samples={buffer} windowSec={WINDOW_SEC} />
      {t.loss_pct > 0 ? (
        <div className="sub" style={{ fontSize: 11, marginTop: 2, color: "var(--critical)" }}>
          {t.loss_pct}% loss in the last {Math.round(WINDOW_SEC / 60)} min
        </div>
      ) : null}
    </div>
  );
}

export function LiveView({ onOpenSettings }: { onOpenSettings?: () => void }) {
  const [feed, setFeed] = useState<LiveFeed | null>(null);
  const [error, setError] = useState("");
  const buffersRef = useRef<Record<string, LiveSample[]>>({});
  const [, bump] = useState(0); // re-render after buffer merges

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .liveFeed(Math.ceil(WINDOW_SEC / 60) + 1)
        .then((f) => {
          if (!alive) return;
          for (const t of f.targets) {
            buffersRef.current[t.id] = mergeSamples(buffersRef.current[t.id] ?? [], t.samples);
          }
          setFeed(f);
          setError("");
          bump((n) => n + 1);
        })
        .catch((e) => alive && setError(String(e instanceof Error ? e.message : e)));
    load();
    const id = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const enabled = useMemo(() => (feed?.targets ?? []).filter((t) => t.enabled), [feed]);
  const withData = enabled.filter((t) => t.ok !== null);
  const down = withData.filter((t) => t.ok === false);
  const pings = enabled.filter((t) => t.kind === "ping" && t.last_ms != null);
  const avgMs = pings.length
    ? pings.reduce((a, t) => a + (t.last_ms as number), 0) / pings.length
    : null;
  const anyLocal = enabled.some((t) => t.vantage === "local");

  const banner =
    feed === null
      ? { cls: "unknown", icon: "…", title: "Connecting…", sub: "" }
      : down.length === 0 && withData.length > 0
        ? {
            cls: "ok", icon: "✓", title: "ALL SYSTEMS OPERATIONAL",
            sub: `${withData.length}/${enabled.length} targets healthy`,
          }
        : down.length > 0
          ? {
              cls: "bad", icon: "▲",
              title: `${down.length} TARGET${down.length === 1 ? "" : "S"} DOWN`,
              sub: down.map((t) => t.label).join(", "),
            }
          : { cls: "unknown", icon: "…", title: "WAITING FOR FIRST SAMPLES", sub: "" };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {error ? <div className="banner err">{error}</div> : null}

      {/* ── Status banner ── */}
      <div
        className="panel"
        style={{
          display: "flex", alignItems: "center", flexWrap: "wrap", gap: 14, padding: "16px 18px",
          borderLeft: `5px solid ${
            banner.cls === "ok" ? "var(--good)" : banner.cls === "bad" ? "var(--critical)" : "var(--ink-muted)"
          }`,
        }}
      >
        <span
          aria-hidden
          style={{
            width: 14, height: 14, borderRadius: "50%", flexShrink: 0,
            background: banner.cls === "ok" ? "var(--good)" : banner.cls === "bad" ? "var(--critical)" : "var(--ink-muted)",
            boxShadow: banner.cls === "ok" ? "0 0 10px var(--good)" : undefined,
            animation: banner.cls === "ok" ? "nm-pulse 2.4s ease-in-out infinite" : undefined,
          }}
        />
        <div>
          <div style={{
            fontSize: 20, fontWeight: 700, letterSpacing: "0.02em",
            color: banner.cls === "ok" ? "var(--good)" : banner.cls === "bad" ? "var(--critical)" : "var(--ink-secondary)",
          }}>
            {banner.icon} {banner.title}
          </div>
          {banner.sub ? <div className="sub" style={{ fontSize: 13 }}>{banner.sub}</div> : null}
        </div>
        <div className="spacer" style={{ flex: 1 }} />
        <div className="sub" style={{ fontSize: 12, textAlign: "right" }}>
          {feed?.probe_agent
            ? anyLocal
              ? <>vantage: <strong>on the lot</strong> · {feed.probe_agent.name}</>
              : <>vantage: <strong>cloud</strong> · {feed.probe_agent.name} asleep</>
            : <>
                vantage: <strong>cloud</strong>
                {isAdmin() && onOpenSettings ? (
                  <> · <a href="#" onClick={(e) => { e.preventDefault(); onOpenSettings(); }}>
                    pick a probe kiosk
                  </a></>
                ) : null}
              </>}
        </div>
      </div>

      {/* ── Headlines ── */}
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10 }}>
        {[
          { v: `${withData.length - down.length}/${enabled.length}`, l: "targets OK", tone: down.length ? undefined : "good" },
          { v: String(down.length), l: down.length ? "down now" : "failures · all clear", tone: down.length ? "bad" : undefined },
          { v: avgMs != null ? avgMs.toFixed(1) : "—", l: "avg ping (ms)" },
          { v: anyLocal ? "LOT" : "CLOUD", l: "vantage" },
        ].map((s, i) => (
          <div key={i} className="panel" style={{ padding: "12px 14px" }}>
            <div style={{
              fontSize: 26, fontWeight: 700, fontVariantNumeric: "tabular-nums",
              color: s.tone === "good" ? "var(--good)" : s.tone === "bad" ? "var(--critical)" : undefined,
            }}>
              {s.v}
            </div>
            <div className="sub" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em" }}>
              {s.l}
            </div>
          </div>
        ))}
      </div>

      {/* ── Live charts ── */}
      {enabled.map((t) => (
        <TargetCard key={t.id} t={t} buffer={buffersRef.current[t.id] ?? []} />
      ))}
      {feed !== null && enabled.length === 0 ? (
        <div className="empty"><p>No probe targets configured. Add some in Settings.</p></div>
      ) : null}
    </div>
  );
}
