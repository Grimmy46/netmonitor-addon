import { useEffect, useState } from "react";
import { api, type LiveFeed } from "../api/client";

/**
 * Guest dashboard: plain-language status of the online services the fair
 * depends on (payments, Funcard, internet). Names and OK/trouble only — no
 * addresses, URLs, probe stations or charts. Replaces LiveView on the guest link.
 */
export function GuestServices({ closed = false, until }: { closed?: boolean; until?: string | null }) {
  const [feed, setFeed] = useState<LiveFeed | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let alive = true;
    const load = () => api.liveFeed(5)
      .then((f) => { if (alive) { setFeed(f); setErr(""); } })
      .catch((e) => alive && setErr(String(e instanceof Error ? e.message : e)));
    load();
    const id = setInterval(load, 15000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  // The site-gateway ping is internal (and has no data while closed) — not shown to guests.
  const svc = (feed?.targets ?? []).filter((t) => t.enabled && t.target !== "gateway");
  const known = svc.filter((t) => t.ok !== null);
  const down = known.filter((t) => t.ok === false);
  const untilTxt = until ? new Date(until).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" }) : "";

  const banner = feed === null
    ? { tone: "var(--ink-muted)", title: err ? "Status unavailable" : "Checking…", sub: err }
    : down.length
      ? { tone: "var(--critical)", title: `${down.length} online service${down.length === 1 ? " is" : "s are"} having trouble`, sub: down.map((t) => t.label).join(", ") }
      : closed
        ? { tone: "var(--ink-muted)", title: `Closed${untilTxt ? ` until ${untilTxt}` : ""}`, sub: "Online services are still checked around the clock — all OK." }
        : known.length
          ? { tone: "var(--good)", title: "All systems operational", sub: "Payments, Funcard and internet are all responding." }
          : { tone: "var(--ink-muted)", title: "Checking…", sub: "" };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="panel" style={{ display: "flex", alignItems: "center", gap: 14, padding: "16px 18px", borderLeft: `5px solid ${banner.tone}` }}>
        <span aria-hidden style={{ width: 14, height: 14, borderRadius: "50%", flexShrink: 0, background: banner.tone }} />
        <div>
          <div style={{ fontSize: 20, fontWeight: 700, color: banner.tone }}>{banner.title}</div>
          {banner.sub ? <div className="sub" style={{ fontSize: 13 }}>{banner.sub}</div> : null}
        </div>
      </div>

      {svc.length ? (
        <div className="panel" style={{ padding: "14px 18px" }}>
          <div className="panel-title" style={{ marginTop: 0 }}>Online services</div>
          {svc.map((t) => {
            const st = t.ok === null ? { cls: "off", label: "Checking" } : t.ok ? { cls: "ok", label: "OK" } : { cls: "bad", label: "Trouble" };
            return (
              <div key={t.id} style={{ display: "flex", alignItems: "center", padding: "8px 0", borderTop: "1px solid var(--border)" }}>
                <span style={{ fontWeight: 600 }}>{t.label}</span>
                <span style={{ flex: 1 }} />
                <span className={`rcs-badge ${st.cls}`}>{st.label}</span>
              </div>
            );
          })}
          <div className="sub" style={{ fontSize: 12, marginTop: 8 }}>Updates every 15 seconds.</div>
        </div>
      ) : null}
    </div>
  );
}
