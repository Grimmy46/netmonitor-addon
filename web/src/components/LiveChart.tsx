import { useEffect, useRef } from "react";
import type { LiveSample } from "../api/client";

/**
 * Continuously-scrolling live latency chart (canvas).
 *
 * Smoothness design: the x-axis is TIME, and the right edge is always
 * `now - LAG` — recomputed every animation frame — so the trace glides left at
 * a perfectly constant speed regardless of when data batches arrive. New
 * samples simply materialize just right of the previous newest point and
 * scroll into view. No jumps, no easing hacks; 60fps comes from drawing a
 * couple hundred line segments on a DPR-scaled canvas, which is trivial work.
 *
 * Chart conventions (dataviz): single series (the card title names it — no
 * legend), 2px line, recessive gridlines, failed probes as short critical
 * ticks on the baseline, text in ink tokens, hover crosshair with a readout.
 */
export function LiveChart({
  samples,
  windowSec = 300,
  height = 90,
}: {
  samples: LiveSample[];
  windowSec?: number;
  height?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const samplesRef = useRef<LiveSample[]>(samples);
  const hoverRef = useRef<number | null>(null); // css-px x within canvas
  samplesRef.current = samples;

  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const cv: HTMLCanvasElement = el;
    const maybeCtx = cv.getContext("2d");
    if (!maybeCtx) return;
    const g: CanvasRenderingContext2D = maybeCtx;
    let raf = 0;
    const LAG = 3; // draw slightly behind now so fresh points slide in

    const css = (name: string) =>
      getComputedStyle(document.documentElement).getPropertyValue(name).trim();

    function draw() {
      raf = requestAnimationFrame(draw);
      const dpr = window.devicePixelRatio || 1;
      const w = cv.clientWidth;
      const h = cv.clientHeight;
      if (w === 0) return;
      if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
        cv.width = Math.round(w * dpr);
        cv.height = Math.round(h * dpr);
      }
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, w, h);

      const accent = css("--accent") || "#2a78d6";
      const grid = css("--grid") || "#e1e0d9";
      const muted = css("--ink-muted") || "#898781";
      const critical = css("--critical") || "#d03b3b";

      const now = Date.now() / 1000 - LAG;
      const t0 = now - windowSec;
      const pts = samplesRef.current.filter((s) => s.ts >= t0 - 10);

      // y-scale: nice headroom over the window's max; min scale 10ms.
      let maxMs = 10;
      for (const p of pts) if (p.ms != null && p.ms > maxMs) maxMs = p.ms;
      maxMs *= 1.15;

      const padL = 30;
      const padB = 14;
      const plotW = w - padL - 4;
      const plotH = h - padB - 6;
      const x = (ts: number) => padL + ((ts - t0) / windowSec) * plotW;
      const y = (ms: number) => 6 + plotH - (ms / maxMs) * plotH;

      // Recessive grid: 3 horizontal lines + y labels.
      g.strokeStyle = grid;
      g.lineWidth = 1;
      g.fillStyle = muted;
      g.font = "10px " + (css("--font") || "system-ui");
      g.textAlign = "right";
      for (const frac of [0, 0.5, 1]) {
        const ms = maxMs * frac;
        const yy = y(ms);
        g.globalAlpha = 0.55;
        g.beginPath();
        g.moveTo(padL, yy);
        g.lineTo(w - 4, yy);
        g.stroke();
        g.globalAlpha = 1;
        g.fillText(ms >= 100 ? String(Math.round(ms)) : ms.toFixed(0), padL - 4, yy + 3);
      }
      // x time labels: left + right edge.
      g.textAlign = "left";
      const fmt = (t: number) =>
        new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      g.fillText(fmt(t0), padL, h - 2);
      g.textAlign = "right";
      g.fillText("now", w - 4, h - 2);

      // The trace: line through successful samples, gaps where data is absent,
      // critical baseline ticks where a probe failed.
      g.strokeStyle = accent;
      g.lineWidth = 2;
      g.lineJoin = "round";
      g.beginPath();
      let pen = false;
      let prevTs = 0;
      for (const p of pts) {
        if (p.ms == null) {
          pen = false;
          continue;
        }
        const px = x(p.ts);
        if (px > w) break;
        // break the line across real gaps (> 3× typical cadence)
        if (pen && p.ts - prevTs > 25) pen = false;
        if (!pen) {
          g.moveTo(px, y(p.ms));
          pen = true;
        } else {
          g.lineTo(px, y(p.ms));
        }
        prevTs = p.ts;
      }
      g.stroke();

      g.strokeStyle = critical;
      g.lineWidth = 2;
      for (const p of pts) {
        if (p.ms != null) continue;
        const px = x(p.ts);
        if (px < padL || px > w) continue;
        g.beginPath();
        g.moveTo(px, 6 + plotH - 5);
        g.lineTo(px, 6 + plotH);
        g.stroke();
      }

      // Hover crosshair + readout of the nearest sample.
      const hx = hoverRef.current;
      if (hx != null && hx >= padL && pts.length) {
        const ht = t0 + ((hx - padL) / plotW) * windowSec;
        let best: LiveSample | null = null;
        for (const p of pts) {
          if (best === null || Math.abs(p.ts - ht) < Math.abs(best.ts - ht)) best = p;
        }
        if (best && Math.abs(best.ts - ht) < 30) {
          const bx = x(best.ts);
          g.strokeStyle = muted;
          g.lineWidth = 1;
          g.globalAlpha = 0.7;
          g.beginPath();
          g.moveTo(bx, 6);
          g.lineTo(bx, 6 + plotH);
          g.stroke();
          g.globalAlpha = 1;
          if (best.ms != null) {
            g.fillStyle = accent;
            g.beginPath();
            g.arc(bx, y(best.ms), 3.5, 0, Math.PI * 2);
            g.fill();
          }
          const label = `${new Date(best.ts * 1000).toLocaleTimeString()} · ${
            best.ms != null ? `${best.ms.toFixed(1)} ms` : "no reply"
          }`;
          g.font = "11px " + (css("--font") || "system-ui");
          const tw = g.measureText(label).width + 12;
          const lx = Math.min(Math.max(bx - tw / 2, padL), w - tw - 4);
          g.fillStyle = css("--surface-1") || "#fff";
          g.strokeStyle = grid;
          g.globalAlpha = 0.95;
          g.beginPath();
          g.roundRect(lx, 8, tw, 18, 4);
          g.fill();
          g.stroke();
          g.globalAlpha = 1;
          g.fillStyle = best.ms != null ? (css("--ink-primary") || "#111") : critical;
          g.textAlign = "left";
          g.fillText(label, lx + 6, 21);
        }
      }
    }

    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [windowSec]);

  return (
    <canvas
      ref={canvasRef}
      style={{ width: "100%", height, display: "block", cursor: "crosshair" }}
      onMouseMove={(e) => {
        const r = (e.target as HTMLCanvasElement).getBoundingClientRect();
        hoverRef.current = e.clientX - r.left;
      }}
      onMouseLeave={() => {
        hoverRef.current = null;
      }}
    />
  );
}
