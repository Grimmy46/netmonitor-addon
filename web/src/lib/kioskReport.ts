import { jsPDF } from "jspdf";
import { api, type Agent, type PingSummary } from "../api/client";

// ── palette (PDF is on white paper) ──────────────────────────────────────────
const INK = "#111827";
const MUTED = "#6b7280";
const LINE = "#e5e7eb";
const AVG = "#2563eb"; // avg latency line
const AVG_FILL = "rgba(37,99,235,0.12)";
const MAXC = "#93c5fd"; // peak latency line
const LOSS = "#dc2626";

function fmtMs(v: number | null): string {
  if (v == null) return "—";
  return v >= 100 ? `${Math.round(v)}` : `${v.toFixed(1)}`;
}

function fmtClock(d: Date): string {
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.toLocaleDateString()} ${fmtClock(d)}`;
}

/**
 * Draw a 24h latency chart onto a high-DPI canvas and return it as a PNG data
 * URL. Avg latency as a filled line, peak latency as a faint line above it, and
 * red ticks along the bottom wherever a minute bucket saw packet loss.
 */
function renderChart(sum: PingSummary): string {
  const scale = 2;
  const W = 1040;
  const H = 380;
  const cvs = document.createElement("canvas");
  cvs.width = W * scale;
  cvs.height = H * scale;
  const ctx = cvs.getContext("2d")!;
  ctx.scale(scale, scale);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, W, H);

  const padL = 54;
  const padR = 16;
  const padT = 16;
  const padB = 46;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const end = Date.parse(sum.generated_at);
  const start = end - sum.hours * 3600_000;

  const pts = sum.buckets
    .map((b) => ({ t: Date.parse(b.ts), avg: b.avg_rtt_ms, max: b.max_rtt_ms, loss: b.loss_pct }))
    .filter((p) => Number.isFinite(p.t));

  // Adaptive y-max: standard 100ms baseline, grow to fit peaks.
  let peak = 0;
  for (const p of pts) peak = Math.max(peak, p.max ?? 0, p.avg ?? 0);
  const yMax = peak <= 100 ? 100 : Math.ceil(peak / 50) * 50;

  const xOf = (t: number) => padL + ((t - start) / (end - start)) * plotW;
  const yOf = (v: number) => padT + plotH - (Math.min(v, yMax) / yMax) * plotH;

  // ── grid + y labels ──
  ctx.font = "12px Helvetica, Arial, sans-serif";
  ctx.textBaseline = "middle";
  const ySteps = 4;
  for (let i = 0; i <= ySteps; i++) {
    const v = (yMax / ySteps) * i;
    const y = yOf(v);
    ctx.strokeStyle = LINE;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(W - padR, y);
    ctx.stroke();
    ctx.fillStyle = MUTED;
    ctx.textAlign = "right";
    ctx.fillText(`${Math.round(v)}`, padL - 8, y);
  }
  // y axis unit
  ctx.save();
  ctx.translate(14, padT + plotH / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = "center";
  ctx.fillStyle = MUTED;
  ctx.fillText("latency (ms)", 0, 0);
  ctx.restore();

  // ── x ticks (every ~hours/8) ──
  ctx.textBaseline = "top";
  ctx.textAlign = "center";
  const ticks = 8;
  for (let i = 0; i <= ticks; i++) {
    const t = start + ((end - start) / ticks) * i;
    const x = padL + (plotW / ticks) * i;
    ctx.strokeStyle = LINE;
    ctx.beginPath();
    ctx.moveTo(x, padT);
    ctx.lineTo(x, padT + plotH);
    ctx.stroke();
    ctx.fillStyle = MUTED;
    ctx.fillText(fmtClock(new Date(t)), x, padT + plotH + 8);
  }

  if (pts.length === 0) {
    ctx.fillStyle = MUTED;
    ctx.font = "15px Helvetica, Arial, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("No ping data in this window yet", padL + plotW / 2, padT + plotH / 2);
    return cvs.toDataURL("image/png");
  }

  // ── peak line (faint) ──
  ctx.strokeStyle = MAXC;
  ctx.lineWidth = 1;
  ctx.beginPath();
  let started = false;
  for (const p of pts) {
    if (p.max == null) { started = false; continue; }
    const x = xOf(p.t);
    const y = yOf(p.max);
    if (!started) { ctx.moveTo(x, y); started = true; } else { ctx.lineTo(x, y); }
  }
  ctx.stroke();

  // ── avg area + line ──
  const segs: { x: number; y: number }[][] = [];
  let cur: { x: number; y: number }[] = [];
  for (const p of pts) {
    if (p.avg == null) { if (cur.length) { segs.push(cur); cur = []; } continue; }
    cur.push({ x: xOf(p.t), y: yOf(p.avg) });
  }
  if (cur.length) segs.push(cur);

  const baseY = padT + plotH;
  for (const seg of segs) {
    if (seg.length < 1) continue;
    ctx.fillStyle = AVG_FILL;
    ctx.beginPath();
    ctx.moveTo(seg[0].x, baseY);
    for (const s of seg) ctx.lineTo(s.x, s.y);
    ctx.lineTo(seg[seg.length - 1].x, baseY);
    ctx.closePath();
    ctx.fill();

    ctx.strokeStyle = AVG;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(seg[0].x, seg[0].y);
    for (const s of seg) ctx.lineTo(s.x, s.y);
    ctx.stroke();
  }

  // ── loss ticks along the bottom ──
  for (const p of pts) {
    if (!p.loss) continue;
    const x = xOf(p.t);
    ctx.strokeStyle = LOSS;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x, baseY);
    ctx.lineTo(x, baseY - Math.min(14, 4 + (p.loss / 100) * 14));
    ctx.stroke();
  }

  return cvs.toDataURL("image/png");
}

function statTiles(sum: PingSummary): { label: string; value: string }[] {
  const s = sum.stats;
  return [
    { label: "Avg latency", value: `${fmtMs(s.avg_rtt_ms)} ms` },
    { label: "Min", value: `${fmtMs(s.min_rtt_ms)} ms` },
    { label: "Peak", value: `${fmtMs(s.max_rtt_ms)} ms` },
    { label: "95th pct", value: `${fmtMs(s.p95_rtt_ms)} ms` },
    { label: "Packet loss", value: `${s.loss_pct.toFixed(2)}%` },
    { label: "Uptime", value: `${s.uptime_pct.toFixed(2)}%` },
    { label: "Gateway avg", value: `${fmtMs(s.avg_gateway_rtt_ms)} ms` },
    { label: "Samples", value: s.samples.toLocaleString() },
  ];
}

/**
 * Build a per-kiosk PDF: one page each, kiosk header + 24h stats + 24h latency
 * graph. Returns the jsPDF so callers can save or inspect it.
 */
export async function buildKioskReport(agents: Agent[], hours = 24): Promise<jsPDF> {
  const doc = new jsPDF({ unit: "pt", format: "letter" });
  const pageW = doc.internal.pageSize.getWidth();
  const margin = 40;
  const contentW = pageW - margin * 2;
  const nowStr = fmtDateTime(new Date().toISOString());

  let first = true;
  for (const agent of agents) {
    let sum: PingSummary | null = null;
    try {
      sum = await api.agentPingSummary(agent.id, hours);
    } catch {
      sum = null;
    }

    if (!first) doc.addPage();
    first = false;

    let y = margin;

    // ── header ──
    doc.setTextColor(INK);
    doc.setFont("helvetica", "bold");
    doc.setFontSize(18);
    doc.text(agent.name, margin, y + 4);
    y += 22;

    doc.setFont("helvetica", "normal");
    doc.setFontSize(10);
    doc.setTextColor(MUTED);
    const sub = [
      agent.site_name || null,
      agent.hostname || null,
      `target ${sum?.target || agent.last_target || "—"}`,
      agent.online ? "online" : agent.last_seen_at ? "offline" : "no data",
    ]
      .filter(Boolean)
      .join("   ·   ");
    doc.text(sub, margin, y);
    y += 12;
    doc.setTextColor("#9ca3af");
    doc.setFontSize(9);
    doc.text(`Last ${hours}h  ·  generated ${nowStr}`, margin, y);
    y += 16;

    // ── stat tiles (4 columns × 2 rows) ──
    if (sum) {
      const tiles = statTiles(sum);
      const cols = 4;
      const gap = 10;
      const tileW = (contentW - gap * (cols - 1)) / cols;
      const tileH = 42;
      tiles.forEach((t, i) => {
        const col = i % cols;
        const row = Math.floor(i / cols);
        const tx = margin + col * (tileW + gap);
        const ty = y + row * (tileH + gap);
        doc.setDrawColor(LINE);
        doc.setFillColor("#f9fafb");
        doc.roundedRect(tx, ty, tileW, tileH, 4, 4, "FD");
        doc.setTextColor(INK);
        doc.setFont("helvetica", "bold");
        doc.setFontSize(14);
        doc.text(t.value, tx + 10, ty + 20);
        doc.setTextColor(MUTED);
        doc.setFont("helvetica", "normal");
        doc.setFontSize(8);
        doc.text(t.label.toUpperCase(), tx + 10, ty + 33);
      });
      y += 2 * 42 + 10 + 18;
    } else {
      doc.setTextColor(MUTED);
      doc.setFontSize(11);
      doc.text("Could not load ping data for this kiosk.", margin, y + 10);
      y += 30;
    }

    // ── chart ──
    if (sum) {
      const img = renderChart(sum);
      const imgW = contentW;
      const imgH = (380 / 1040) * imgW;
      doc.addImage(img, "PNG", margin, y, imgW, imgH);
      y += imgH + 10;

      // legend
      doc.setFontSize(8);
      doc.setFont("helvetica", "normal");
      doc.setTextColor(AVG);
      doc.text("— avg", margin, y);
      doc.setTextColor(MAXC);
      doc.text("— peak", margin + 42, y);
      doc.setTextColor(LOSS);
      doc.text("| packet loss", margin + 90, y);
      doc.setTextColor(MUTED);
      const span =
        sum.first_ts && sum.last_ts
          ? `data ${fmtDateTime(sum.first_ts)} – ${fmtDateTime(sum.last_ts)}`
          : "no samples in window";
      doc.text(span, margin + 190, y);
    }

    // ── footer ──
    const ph = doc.internal.pageSize.getHeight();
    doc.setFontSize(8);
    doc.setTextColor("#9ca3af");
    doc.text("NetMonitor · kiosk latency report", margin, ph - 20);
  }

  return doc;
}

export async function downloadKioskReport(agents: Agent[], hours = 24): Promise<void> {
  const doc = await buildKioskReport(agents, hours);
  const stamp = new Date().toISOString().slice(0, 10);
  doc.save(`netmonitor-kiosks-${stamp}.pdf`);
}
