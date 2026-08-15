import { useEffect, useRef, useState } from "react";
import { api, type AgentCommand } from "../api/client";

/**
 * Admin tool on a kiosk card: queue a read-only "printer-status" command,
 * wait for the kiosk to pick it up on its next check-in (~30s), and decode
 * what Windows reports about every installed printer. This is the KPM180H
 * reconnaissance step — DetectedErrorState is where a bidirectional driver
 * reports paper-out / jam / cover-open.
 */

const ERROR_STATE: Record<number, string> = {
  0: "Unknown", 1: "Other", 2: "No error", 3: "LOW PAPER", 4: "NO PAPER",
  5: "Low toner", 6: "No toner", 7: "DOOR OPEN", 8: "JAMMED", 9: "OFFLINE",
  10: "Service requested", 11: "Output bin full",
};
const PRINTER_STATUS: Record<number, string> = {
  1: "Other", 2: "Unknown", 3: "Idle", 4: "Printing", 5: "Warming up",
  6: "Stopped", 7: "Offline",
};

interface PrinterRow {
  Name?: string;
  DriverName?: string;
  PortName?: string;
  Default?: boolean;
  PrinterStatus?: number;
  DetectedErrorState?: number;
  WorkOffline?: boolean;
}

export function PrinterCheck({ agentId }: { agentId: string }) {
  const [state, setState] = useState<"idle" | "waiting" | "shown">("idle");
  const [msg, setMsg] = useState("");
  const [cmd, setCmd] = useState<AgentCommand | null>(null);
  const pollRef = useRef<number | null>(null);
  const deadlineRef = useRef(0);

  useEffect(() => () => { if (pollRef.current) window.clearInterval(pollRef.current); }, []);

  async function run() {
    setState("waiting");
    setCmd(null);
    setMsg("Queued — the kiosk picks it up on its next check-in (~30s)…");
    try {
      const queued = await api.queueCommand(agentId, "printer-status");
      deadlineRef.current = Date.now() + 120000;
      pollRef.current = window.setInterval(async () => {
        try {
          const rows = await api.agentCommands(agentId, 10);
          const mine = rows.find((c) => c.id === queued.id);
          if (mine && (mine.status === "done" || mine.status === "error")) {
            if (pollRef.current) window.clearInterval(pollRef.current);
            setCmd(mine);
            setState("shown");
            setMsg("");
          } else if (mine?.status === "sent") {
            setMsg("Delivered — kiosk is querying the printer…");
          }
          if (Date.now() > deadlineRef.current) {
            if (pollRef.current) window.clearInterval(pollRef.current);
            setState("shown");
            setMsg("No answer within 2 minutes — is the kiosk online?");
          }
        } catch { /* transient; keep polling */ }
      }, 5000);
    } catch (e) {
      setState("shown");
      setMsg(String(e instanceof Error ? e.message : e));
    }
  }

  const printers: PrinterRow[] =
    cmd?.status === "done" && Array.isArray((cmd.result as { printers?: PrinterRow[] })?.printers)
      ? ((cmd.result as { printers: PrinterRow[] }).printers)
      : [];

  return (
    <div style={{ marginTop: 8 }}>
      <button
        className="btn"
        style={{ fontSize: 12, padding: "4px 10px" }}
        disabled={state === "waiting"}
        onClick={(e) => { e.stopPropagation(); run(); }}
      >
        {state === "waiting" ? "🖨 Querying…" : "🖨 Query printer"}
      </button>
      {msg ? <span className="sub" style={{ fontSize: 12, marginLeft: 8 }}>{msg}</span> : null}
      {cmd?.status === "error" ? (
        <div className="sub" style={{ fontSize: 12, color: "var(--critical)", marginTop: 6 }}>
          Kiosk reported an error: {String((cmd.result as { error?: string })?.error ?? "unknown")}
        </div>
      ) : null}
      {cmd?.status === "done" && printers.length === 0 ? (
        <div className="sub" style={{ fontSize: 12, marginTop: 6 }}>
          The kiosk answered but Windows lists no printers.
        </div>
      ) : null}
      {printers.length > 0 ? (
        <div style={{ marginTop: 8 }}>
          {printers.map((p, i) => {
            const err = p.DetectedErrorState ?? 0;
            const bad = [3, 4, 7, 8, 9].includes(err) || p.WorkOffline === true;
            return (
              <div
                key={i}
                className="banner"
                style={{
                  display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap",
                  marginBottom: 6, fontSize: 13,
                  borderLeft: `3px solid ${bad ? "var(--critical)" : "var(--good)"}`,
                }}
              >
                <strong>{p.Name ?? "?"}</strong>
                <span className="sub" style={{ fontSize: 12 }}>
                  {p.DriverName ?? ""}{p.PortName ? ` · ${p.PortName}` : ""}{p.Default ? " · default" : ""}
                </span>
                <span style={{ color: bad ? "var(--critical)" : "var(--good)", fontWeight: 600 }}>
                  {ERROR_STATE[err] ?? `error ${err}`}
                  {p.PrinterStatus != null ? ` · ${PRINTER_STATUS[p.PrinterStatus] ?? p.PrinterStatus}` : ""}
                  {p.WorkOffline ? " · WorkOffline" : ""}
                </span>
              </div>
            );
          })}
          <div className="sub" style={{ fontSize: 11 }}>
            Raw Windows printer state — if the KPM180H shows real paper/jam codes here,
            continuous printer monitoring is a go.
          </div>
        </div>
      ) : null}
    </div>
  );
}
