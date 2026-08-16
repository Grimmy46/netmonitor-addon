import { useEffect, useMemo, useState } from "react";
import { api, type PrinterEvent } from "../api/client";

/**
 * Printer log (readable): every ticket-printer status change across the fleet,
 * with a summary, a time-range switch, a station filter, and a CSV export for
 * records. Opened from the Kiosks toolbar.
 */
const LABEL: Record<string, { t: string; c: string; icon: string }> = {
  ok: { t: "Paper OK", c: "var(--good)", icon: "🖨️" },
  paper_out: { t: "Paper OUT", c: "var(--critical)", icon: "🧻" },
  cover_open: { t: "Cover open", c: "var(--critical)", icon: "🔧" },
  error: { t: "Printer error", c: "var(--critical)", icon: "⚠️" },
  unknown: { t: "No reply", c: "var(--ink-muted)", icon: "🖨️" },
  removed: { t: "Disconnected", c: "var(--ink-muted)", icon: "🔌" },
};

function fmt(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

const RANGES: { h: number; t: string }[] = [
  { h: 24, t: "24h" }, { h: 168, t: "7d" }, { h: 720, t: "30d" },
];

export function PrinterLogPanel({ onClose }: { onClose: () => void }) {
  const [hours, setHours] = useState(168);
  const [rows, setRows] = useState<PrinterEvent[] | null>(null);
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    let alive = true;
    setRows(null); setErr("");
    api.fleetPrinterLog(hours, 10000)
      .then((r) => alive && setRows(r))
      .catch((e) => alive && setErr(String(e instanceof Error ? e.message : e)));
    return () => { alive = false; };
  }, [hours]);

  const shown = useMemo(() => {
    const n = q.trim().toLowerCase();
    return (rows ?? []).filter((e) => !n || (e.agent_name ?? "").toLowerCase().includes(n));
  }, [rows, q]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const e of rows ?? []) c[e.state] = (c[e.state] ?? 0) + 1;
    return c;
  }, [rows]);

  function csv() {
    const qq = (v: string | null) => `"${(v ?? "").replace(/"/g, '""')}"`;
    const text = "time,station,state,previous,detail,raw\n" +
      (rows ?? []).map((e) => [qq(new Date(e.at).toISOString()), qq(e.agent_name),
        qq(e.state), qq(e.prev_state), qq(e.detail), qq(e.raw)].join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `printer-log-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const summary: [string, string][] = [
    ["paper_out", "🧻 paper-outs"], ["cover_open", "🔧 cover-opens"],
    ["error", "⚠️ errors"], ["removed", "🔌 disconnects"],
  ];

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" style={{ width: "min(780px, 95vw)" }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <h2 style={{ margin: 0 }}>Printer log</h2>
          <div className="spacer" style={{ flex: 1 }} />
          {RANGES.map((r) => (
            <button key={r.h} className={`btn${hours === r.h ? " btn-primary" : ""}`}
              style={{ fontSize: 12 }} onClick={() => setHours(r.h)}>{r.t}</button>
          ))}
        </div>
        <p className="sub" style={{ marginTop: 6, marginBottom: 10, fontSize: 12 }}>
          Every ticket-printer status change across the fleet — newest first.
        </p>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
          {summary.map(([k, lbl]) => (
            <span key={k} style={{ fontSize: 12, padding: "4px 10px", borderRadius: 999, background: "rgba(127,127,127,0.1)" }}>
              <strong>{counts[k] ?? 0}</strong> {lbl}
            </span>
          ))}
        </div>

        <div className="devices-toolbar" style={{ margin: "0 0 10px" }}>
          <div className="panel-title" style={{ margin: 0 }}>
            {rows === null ? "…" : `${shown.length} change${shown.length === 1 ? "" : "s"}`}
          </div>
          <div className="spacer" />
          <input className="search" placeholder="Filter station…" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn" style={{ marginLeft: 8, fontSize: 12 }} onClick={csv} disabled={!rows?.length}>⤓ CSV</button>
        </div>

        {err ? <div className="banner err" style={{ marginBottom: 10 }}>{err}</div> : null}

        <div style={{ maxHeight: 440, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
          {rows === null ? (
            <p className="hint" style={{ padding: 14 }}>Loading…</p>
          ) : shown.length === 0 ? (
            <p className="hint" style={{ padding: 14 }}>No printer status changes in this window.</p>
          ) : (
            shown.map((e) => {
              const l = LABEL[e.state] ?? LABEL.unknown;
              return (
                <div key={e.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "7px 12px", borderBottom: "1px solid var(--border)" }}>
                  <span style={{ width: 130, color: l.c, fontSize: 13, flexShrink: 0 }}>{l.icon} {l.t}</span>
                  <strong style={{ fontSize: 13, width: 110, flexShrink: 0 }}>{e.agent_name ?? "?"}</strong>
                  <span className="sub" style={{ fontSize: 12, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.detail ?? ""}</span>
                  <span className="sub" style={{ fontSize: 11, whiteSpace: "nowrap", flexShrink: 0 }}>{fmt(e.at)}</span>
                </div>
              );
            })
          )}
        </div>

        <div className="modal-actions" style={{ marginTop: 16 }}>
          <button className="btn" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
