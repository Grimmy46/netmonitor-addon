import { useEffect, useMemo, useState } from "react";
import { api, type PaperUsage } from "../api/client";

/**
 * Paper usage: tickets printed (fleet per day + per kiosk), paper used, and
 * every real roll change. Opened from the Kiosks toolbar.
 */
const HOW: Record<string, string> = {
  ran_out: "Ran out → new roll",
  swapped: "Swapped before empty",
  manual: "Marked new roll",
  inferred: "Change missed (inferred)",
};
const RANGES = [{ d: 1, t: "Today" }, { d: 7, t: "7d" }, { d: 30, t: "30d" }];

const m = (cm: number | null | undefined) => (cm == null ? "—" : `${(cm / 100).toFixed(cm >= 10000 ? 0 : 1)} m`);
const n = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString());
function fmt(iso: string) {
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
function dayLbl(d: string) {
  return new Date(d + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

export function PaperPanel({ onClose }: { onClose: () => void }) {
  const [days, setDays] = useState(7);
  const [data, setData] = useState<PaperUsage | null>(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<"kiosks" | "rolls" | "days">("kiosks");
  const [q, setQ] = useState("");

  useEffect(() => {
    let alive = true;
    setData(null); setErr("");
    api.paperUsage(days).then((r) => alive && setData(r))
      .catch((e) => alive && setErr(String(e instanceof Error ? e.message : e)));
    return () => { alive = false; };
  }, [days]);

  const tot = useMemo(() => {
    const t = { tickets: 0, cm: 0 };
    for (const d of data?.days ?? []) { t.tickets += d.tickets; t.cm += d.cm; }
    return t;
  }, [data]);
  const counting = (data?.stations ?? []).filter((s) => s.counting).length;
  const filt = (name: string | null) => !q.trim() || (name ?? "").toLowerCase().includes(q.trim().toLowerCase());

  function csv() {
    if (!data) return;
    const qq = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const lines = tab === "rolls"
      ? ["time,kiosk,what,tickets on roll,paper on roll (m),estimate"].concat(data.rolls.map((r) =>
          [qq(new Date(r.at).toISOString()), qq(r.name), qq(HOW[r.how] ?? r.how), qq(r.tickets),
           qq(r.cm != null ? (r.cm / 100).toFixed(1) : ""), qq(r.partial ? "yes" : "")].join(",")))
      : ["kiosk,tickets,paper (m),today,roll changes,roll used %,tickets left,lifetime tickets"].concat(data.stations.map((s) =>
          [qq(s.name), qq(s.tickets), qq((s.cm / 100).toFixed(1)), qq(s.today_tickets), qq(s.roll_changes),
           qq(s.roll_percent), qq(s.tickets_left), qq(s.lifetime_tickets)].join(",")));
    const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url; a.download = `paper-${tab}-${new Date().toISOString().slice(0, 10)}.csv`; a.click();
    URL.revokeObjectURL(url);
  }

  const cell: React.CSSProperties = { padding: "6px 10px", fontSize: 13, whiteSpace: "nowrap" };
  const num: React.CSSProperties = { ...cell, textAlign: "right", fontVariantNumeric: "tabular-nums" };

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" style={{ width: "min(860px, 96vw)" }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <h2 style={{ margin: 0 }}>Paper usage</h2>
          <div className="spacer" style={{ flex: 1 }} />
          {RANGES.map((r) => (
            <button key={r.d} className={`btn${days === r.d ? " btn-primary" : ""}`} style={{ fontSize: 12 }}
              onClick={() => setDays(r.d)}>{r.t}</button>
          ))}
        </div>
        <p className="sub" style={{ marginTop: 6, marginBottom: 10, fontSize: 12 }}>
          Read from each printer's own counters (tickets cut and paper printed). A roll change is logged only when
          the printer sat empty for at least a minute, or its low-paper sensor cleared, or someone marked a new roll.
        </p>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
          {[
            [n(tot.tickets), "tickets"], [m(tot.cm), "paper"],
            [n(data?.rolls.length), "roll changes"], [`${counting}/${data?.stations.length ?? "—"}`, "kiosks counting"],
          ].map(([v, l]) => (
            <span key={l} style={{ fontSize: 12, padding: "4px 10px", borderRadius: 999, background: "rgba(127,127,127,0.1)" }}>
              <strong>{v}</strong> {l}
            </span>
          ))}
        </div>

        <div className="devices-toolbar" style={{ margin: "0 0 10px" }}>
          {(["kiosks", "rolls", "days"] as const).map((t) => (
            <button key={t} className={`btn${tab === t ? " btn-primary" : ""}`} style={{ fontSize: 12, marginRight: 6 }}
              onClick={() => setTab(t)}>{t === "kiosks" ? "By kiosk" : t === "rolls" ? "Roll changes" : "By day"}</button>
          ))}
          <div className="spacer" />
          {tab !== "days" ? <input className="search" placeholder="Filter kiosk…" value={q} onChange={(e) => setQ(e.target.value)} /> : null}
          <button className="btn" style={{ marginLeft: 8, fontSize: 12 }} onClick={csv} disabled={!data || tab === "days"}>⤓ CSV</button>
        </div>

        {err ? <div className="banner err" style={{ marginBottom: 10 }}>{err}</div> : null}

        <div style={{ maxHeight: 440, overflow: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
          {data === null ? <p className="hint" style={{ padding: 14 }}>Loading…</p> : tab === "kiosks" ? (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
                <th style={cell}>Kiosk</th><th style={num}>Tickets</th><th style={num}>Paper</th>
                <th style={num}>Today</th><th style={num}>Roll changes</th><th style={num}>Current roll</th>
              </tr></thead>
              <tbody>
                {data.stations.filter((s) => filt(s.name)).map((s) => (
                  <tr key={s.agent_id} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={cell}><strong>{s.name}</strong>{s.near_end ? <span title="Low-paper sensor is on"> 🧻</span> : null}</td>
                    {s.counting ? (<>
                      <td style={num}>{n(s.tickets)}</td><td style={num}>{m(s.cm)}</td>
                      <td style={num}>{n(s.today_tickets)}</td><td style={num}>{s.roll_changes}</td>
                      <td style={num} title={s.roll_estimate ? "Estimate until the next roll change" : ""}>
                        {s.roll_percent != null ? `${Math.round(s.roll_percent)}% used${s.roll_estimate ? " ~" : ""}` : "—"}
                        {s.tickets_left != null ? <span className="sub"> · ~{n(s.tickets_left)} left</span> : null}
                      </td>
                    </>) : <td style={{ ...cell, color: "var(--ink-muted)" }} colSpan={5}>Not counting yet</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : tab === "rolls" ? (
            data.rolls.filter((r) => filt(r.name)).length === 0 ? (
              <p className="hint" style={{ padding: 14 }}>No roll changes in this window.</p>
            ) : data.rolls.filter((r) => filt(r.name)).map((r, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "7px 12px", borderBottom: "1px solid var(--border)" }}>
                <strong style={{ fontSize: 13, width: 90, flexShrink: 0 }}>{r.name ?? "?"}</strong>
                <span style={{ fontSize: 13, width: 190, flexShrink: 0 }}>🧻 {HOW[r.how] ?? r.how}</span>
                <span className="sub" style={{ fontSize: 12, flex: 1 }}>
                  {r.tickets != null ? `${n(r.tickets)} tickets` : ""}{r.cm != null ? ` · ${m(r.cm)}` : ""}
                  {r.partial ? " (roll partly counted)" : ""}
                  {r.out_seconds != null ? ` · empty ${Math.max(1, Math.round(r.out_seconds / 60))} min` : ""}
                </span>
                <span className="sub" style={{ fontSize: 11, whiteSpace: "nowrap" }}>{fmt(r.at)}</span>
              </div>
            ))
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
                <th style={cell}>Day</th><th style={num}>Tickets</th><th style={num}>Paper</th>
              </tr></thead>
              <tbody>{data.days.slice().reverse().map((d) => (
                <tr key={d.day} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={cell}>{dayLbl(d.day)}</td><td style={num}>{n(d.tickets)}</td><td style={num}>{m(d.cm)}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </div>

        <div className="modal-actions" style={{ marginTop: 16 }}>
          <button className="btn" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
