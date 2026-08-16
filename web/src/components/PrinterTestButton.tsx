import { useState } from "react";
import { api, type AgentCommand } from "../api/client";

/**
 * One-click ticket-printer test. Queues a `printer-test` command to the kiosk;
 * the agent prints a small ESC/POS test ticket, reads the printer's status right
 * after, and reports back. We show ✅/⚠️/❌ with the reason. Admin-only.
 */
export function PrinterTestButton({ agentId, label }: { agentId: string; label?: string }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "ok" | "warn" | "bad" | "info"; text: string } | null>(null);

  async function run(e: React.MouseEvent) {
    e.stopPropagation();
    if (busy) return;
    setBusy(true);
    setMsg({ tone: "info", text: "Sending test ticket… (~30s)" });
    try {
      const queued = await api.queueCommand(agentId, "printer-test", { label, cut: "full" });
      const deadline = Date.now() + 120000;
      const result = await new Promise<AgentCommand>((resolve, reject) => {
        const id = window.setInterval(async () => {
          try {
            const rows = await api.agentCommands(agentId, 15);
            const mine = rows.find((c) => c.id === queued.id);
            if (mine?.status === "sent") setMsg({ tone: "info", text: "Delivered — printing…" });
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
      const r = (result.result ?? {}) as { ok?: boolean; detail?: string; error?: string };
      if (result.status === "error" || r.error) {
        setMsg({ tone: "bad", text: `❌ ${r.error ?? "failed"}` });
      } else if (r.ok) {
        setMsg({ tone: "ok", text: `✅ ${r.detail ?? "test ticket printed"}` });
      } else {
        setMsg({ tone: "warn", text: `⚠️ ${r.detail ?? "sent, but printer isn't healthy"}` });
      }
    } catch (err) {
      setMsg({ tone: "bad", text: `❌ ${String(err instanceof Error ? err.message : err)}` });
    } finally {
      setBusy(false);
    }
  }

  const color =
    msg?.tone === "ok" ? "var(--good)" :
    msg?.tone === "bad" ? "var(--critical)" :
    msg?.tone === "warn" ? "var(--warn, #b7791f)" : "var(--ink-muted)";

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, marginLeft: 8, marginTop: 6 }}>
      <button
        className="btn"
        style={{ fontSize: 12, padding: "4px 10px" }}
        disabled={busy}
        title="Print a test ticket on this kiosk and report whether it worked"
        onClick={run}
      >
        🧾 Test print
      </button>
      {msg ? <span className="sub" style={{ fontSize: 11.5, color }}>{msg.text}</span> : null}
    </span>
  );
}
