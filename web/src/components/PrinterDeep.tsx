import { useRef, useState } from "react";
import { api, type AgentCommand } from "../api/client";

/**
 * Deep printer query (admin, test tool) — talks the KPM180H's NATIVE serial
 * protocol via the agent, no driver involved. Two steps:
 *   1) Detect → runs "printer-probe" to find how the printer is addressable
 *      (installed printers + active COM ports).
 *   2) Query → sends manual status commands (DLE EOT, GS Ex) as "printer-raw"
 *      transactions to a chosen COM port and shows the raw reply + a decode.
 *
 * Everything is queued to the kiosk and answered on its next check-in (~30s);
 * the raw hex is always shown so we can verify against the Custom manual before
 * trusting any decode.
 */

interface Preset {
  key: string;
  label: string;
  hex: string;
  decode?: (bytes: number[]) => string;
}

const bit = (b: number, n: number) => (b & (1 << n)) !== 0;

// Generic ESC/POS real-time status decode — a HINT; verify vs the KPM180H manual.
function decodeEot2(b: number[]): string {
  if (!b.length) return "no reply";
  const s = b[0];
  const f: string[] = [];
  if (bit(s, 2)) f.push("COVER OPEN");
  if (bit(s, 3)) f.push("paper fed by button");
  if (bit(s, 5)) f.push("PAPER END (stop)");
  if (bit(s, 6)) f.push("ERROR");
  return f.length ? f.join(", ") : "online, no offline cause";
}
function decodeEot3(b: number[]): string {
  if (!b.length) return "no reply";
  const s = b[0];
  const f: string[] = [];
  if (bit(s, 3)) f.push("CUTTER ERROR");
  if (bit(s, 5)) f.push("UNRECOVERABLE ERROR");
  if (bit(s, 6)) f.push("auto-recoverable error");
  return f.length ? f.join(", ") : "no error";
}
function decodeEot4(b: number[]): string {
  if (!b.length) return "no reply";
  const s = b[0];
  const f: string[] = [];
  if (bit(s, 2) && bit(s, 3)) f.push("PAPER OUT");
  if (bit(s, 5) && bit(s, 6)) f.push("PAPER NEAR-END (low)");
  return f.length ? f.join(", ") : "paper present";
}
function decodeInt(b: number[]): string {
  if (!b.length) return "no reply";
  // little-endian hint; the manual gives exact framing
  let v = 0;
  for (let i = 0; i < b.length; i++) v += b[i] << (8 * i);
  return `≈ ${v} (LE of ${b.length} byte${b.length === 1 ? "" : "s"})`;
}

const PRESETS: Preset[] = [
  { key: "eot1", label: "Printer status (DLE EOT 1)", hex: "10 04 01" },
  { key: "eot2", label: "Offline cause (DLE EOT 2)", hex: "10 04 02", decode: decodeEot2 },
  { key: "eot3", label: "Error cause (DLE EOT 3)", hex: "10 04 03", decode: decodeEot3 },
  { key: "eot4", label: "Paper sensor (DLE EOT 4)", hex: "10 04 04", decode: decodeEot4 },
  { key: "gsr", label: "Paper sensor (GS r 1)", hex: "1D 72 01" },
  { key: "gse1", label: "Paper remaining (GS E1)", hex: "1D E1", decode: decodeInt },
  { key: "gse2", label: "Lifetime cut count (GS E2)", hex: "1D E2", decode: decodeInt },
];

async function runCommand(
  agentId: string,
  kind: string,
  args: object,
  onStep?: (s: string) => void,
): Promise<AgentCommand> {
  const queued = await api.queueCommand(agentId, kind, args);
  const deadline = Date.now() + 120000;
  return new Promise((resolve, reject) => {
    const id = window.setInterval(async () => {
      try {
        const rows = await api.agentCommands(agentId, 15);
        const mine = rows.find((c) => c.id === queued.id);
        if (mine?.status === "sent") onStep?.("delivered — kiosk is talking to the printer…");
        if (mine && (mine.status === "done" || mine.status === "error")) {
          window.clearInterval(id);
          resolve(mine);
        } else if (Date.now() > deadline) {
          window.clearInterval(id);
          reject(new Error("no answer within 2 min — is the kiosk online?"));
        }
      } catch { /* transient */ }
    }, 4000);
  });
}

function hexToBytes(hex: string): number[] {
  const clean = hex.replace(/[^0-9a-fA-F]/g, "");
  const out: number[] = [];
  for (let i = 0; i + 1 < clean.length; i += 2) out.push(parseInt(clean.substr(i, 2), 16));
  return out;
}

export function PrinterDeep({ agentId }: { agentId: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [ports, setPorts] = useState<{ device: string; port: string }[]>([]);
  const [printers, setPrinters] = useState<{ Name?: string; PortName?: string; DriverName?: string }[]>([]);
  const [usbPaths, setUsbPaths] = useState<string[]>([]);
  const [target, setTarget] = useState("");
  const [mode, setMode] = useState("");
  const [freeHex, setFreeHex] = useState("");
  const [rows, setRows] = useState<{ label: string; hex: string; reply: string; decode: string; ok: boolean }[]>([]);
  const cancel = useRef(false);

  async function detect() {
    setBusy(true); setMsg("Detecting… (~30s)"); setRows([]);
    try {
      const res = await runCommand(agentId, "printer-probe", {}, setMsg);
      if (res.status !== "done") { setMsg("Probe failed: " + JSON.stringify(res.result)); return; }
      const r = res.result as {
        printers?: { Name?: string; PortName?: string; DriverName?: string }[];
        serial_ports?: { device: string; port: string }[];
        usb_paths?: string[];
      };
      const sp = Array.isArray(r.serial_ports) ? r.serial_ports : [];
      const pr = Array.isArray(r.printers) ? r.printers : [];
      const usb = Array.isArray(r.usb_paths) ? r.usb_paths : [];
      setPorts(sp); setPrinters(pr); setUsbPaths(usb);
      // Auto-pick, most-reliable first: COM if the printer is on one, else the
      // KPM180H via the spooler, else its USB interface path.
      const printerCom = pr.map((p) => p.PortName || "").find((pn) => /^com\d+$/i.test(pn));
      const kpm = pr.find((p) => /kpm|custom|receipt|ticket|pos/i.test(`${p.Name} ${p.DriverName}`));
      setTarget(
        printerCom ||
        (kpm?.Name ? `SPOOL:${kpm.Name}` : "") ||
        (usb[0] ? usb[0] : "") ||
        (pr[0]?.Name ? `SPOOL:${pr[0].Name}` : ""),
      );
      const bits: string[] = [];
      if (sp.length) bits.push(`${sp.length} COM`);
      if (usb.length) bits.push(`${usb.length} USB printer interface(s)`);
      bits.push(`${pr.length} spooler printer(s)`);
      setMsg(`Found: ${bits.join(" · ")}. Try the auto-picked target, or switch channel below.`);
    } catch (e) {
      setMsg(String(e instanceof Error ? e.message : e));
    } finally { setBusy(false); }
  }

  async function query(label: string, hex: string, decode?: (b: number[]) => string) {
    if (!target) { setMsg("Pick a COM target first (run Detect)."); return null; }
    const args: Record<string, unknown> = { target, write_hex: hex, read_timeout_ms: 600, read_max: 64 };
    if (mode.trim()) args.mode = mode.trim();
    const res = await runCommand(agentId, "printer-raw", args, setMsg);
    const rr = res.result as { read_hex?: string; error?: string; read_ok?: boolean };
    const replyHex = rr?.read_hex ?? "";
    const bytes = replyHex ? hexToBytes(replyHex) : [];
    const row = {
      label, hex,
      reply: res.status === "error" || rr?.error ? `error: ${rr?.error ?? "see audit"}` : (replyHex || "(empty)"),
      decode: decode && bytes.length ? decode(bytes) : "",
      ok: res.status === "done" && !rr?.error,
    };
    setRows((prev) => [...prev, row]);
    return row;
  }

  async function runBattery() {
    setBusy(true); setRows([]); cancel.current = false;
    try {
      for (const p of PRESETS) {
        if (cancel.current) break;
        setMsg(`Querying: ${p.label}…`);
        await query(p.label, p.hex, p.decode);
      }
      setMsg("Battery complete — raw replies above. Trigger a real fault (open the paper door) and re-run to confirm the bits move.");
    } catch (e) {
      setMsg(String(e instanceof Error ? e.message : e));
    } finally { setBusy(false); }
  }

  if (!open) {
    return (
      <button
        className="btn"
        style={{ fontSize: 12, padding: "4px 10px", marginTop: 6, marginLeft: 8 }}
        onClick={(e) => { e.stopPropagation(); setOpen(true); }}
      >
        🔧 Deep printer query
      </button>
    );
  }

  return (
    <div className="panel" style={{ marginTop: 8, padding: 12 }} onClick={(e) => e.stopPropagation()}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <strong style={{ fontSize: 13 }}>Deep printer query (native protocol)</strong>
        <div className="spacer" style={{ flex: 1 }} />
        <button className="btn" style={{ fontSize: 12, padding: "3px 8px" }} onClick={() => setOpen(false)}>Close</button>
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>
        <button className="btn" style={{ fontSize: 12 }} disabled={busy} onClick={detect}>1 · Detect ports</button>
        <label className="sub" style={{ fontSize: 12 }}>Channel:</label>
        <select value={target} onChange={(e) => setTarget(e.target.value)} style={{ padding: "4px 6px", maxWidth: 260 }}>
          <option value="">— pick target —</option>
          {ports.length ? <optgroup label="Serial (COM)">
            {ports.map((p) => <option key={p.port} value={p.port}>{p.port} ({p.device})</option>)}
          </optgroup> : null}
          {printers.length ? <optgroup label="Spooler (RAW)">
            {printers.map((p) => <option key={p.Name} value={`SPOOL:${p.Name}`}>Spooler: {p.Name}</option>)}
          </optgroup> : null}
          {usbPaths.length ? <optgroup label="USB printer interface">
            {usbPaths.map((u, i) => <option key={u} value={u}>USB #{i + 1}: …{u.slice(-28)}</option>)}
          </optgroup> : null}
          {target && !ports.some((p) => p.port === target) && !printers.some((p) => `SPOOL:${p.Name}` === target) && !usbPaths.includes(target)
            ? <option value={target}>{target}</option> : null}
        </select>
        <input
          placeholder="mode (optional) e.g. baud=115200 parity=N data=8 stop=1"
          value={mode} onChange={(e) => setMode(e.target.value)}
          style={{ flex: 1, minWidth: 180, fontSize: 12 }}
        />
      </div>

      {printers.length ? (
        <div className="sub" style={{ fontSize: 11, marginBottom: 8 }}>
          Printers seen: {printers.map((p) => `${p.Name ?? "?"}${p.PortName ? ` (${p.PortName})` : ""}`).join(" · ")}
        </div>
      ) : null}

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
        <button className="btn btn-primary" style={{ fontSize: 12 }} disabled={busy || !target} onClick={runBattery}>
          2 · Run status battery
        </button>
        {PRESETS.map((p) => (
          <button
            key={p.key} className="btn" style={{ fontSize: 11, padding: "3px 8px" }}
            disabled={busy || !target}
            title={p.hex}
            onClick={() => { setBusy(true); query(p.label, p.hex, p.decode).finally(() => setBusy(false)); }}
          >
            {p.label.replace(/ \(.*\)/, "")}
          </button>
        ))}
      </div>

      <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 8 }}>
        <input
          placeholder="free-form hex, e.g. 10 04 01" value={freeHex}
          onChange={(e) => setFreeHex(e.target.value)}
          style={{ flex: 1, fontSize: 12, fontFamily: "monospace" }}
        />
        <button
          className="btn" style={{ fontSize: 12 }} disabled={busy || !target || !freeHex.trim()}
          onClick={() => { setBusy(true); query("custom: " + freeHex, freeHex).finally(() => setBusy(false)); }}
        >Send</button>
      </div>

      {msg ? <p className="sub" style={{ fontSize: 12 }}>{msg}</p> : null}

      {rows.length ? (
        <div className="table-scroll" style={{ marginTop: 6 }}>
          <table className="devices" style={{ fontSize: 12 }}>
            <thead><tr><th>Query</th><th>Sent</th><th>Reply (hex)</th><th>Decode (generic ESC/POS)</th></tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td>{r.label}</td>
                  <td className="mono">{r.hex}</td>
                  <td className="mono" style={{ color: r.ok ? undefined : "var(--critical)" }}>{r.reply}</td>
                  <td style={{ color: /OUT|OPEN|ERROR|END/.test(r.decode) ? "var(--critical)" : undefined }}>{r.decode || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
