import { useEffect, useRef, useState } from "react";
import { api, type Site } from "../api/client";

const NODE_W = 190;
const NODE_H = 70;

type XY = { x: number; y: number };

/**
 * Fleet site map: each site is a draggable node, colored by live status.
 * Positions persist to the backend on drop, so the layout sticks across reloads.
 */
export function SiteMap({ sites }: { sites: Site[] }) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1000);
  const [pos, setPos] = useState<Record<string, XY>>({});
  const drag = useRef<{ id: string; offX: number; offY: number; x: number; y: number } | null>(null);

  // Track canvas width so auto-layout wraps sensibly.
  useEffect(() => {
    const measure = () => setWidth(canvasRef.current?.clientWidth ?? 1000);
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  // Seed positions: keep any we already have, add newcomers from saved coords
  // or an auto-grid. Never clobbers a node the user has dragged.
  useEffect(() => {
    setPos((prev) => {
      const next = { ...prev };
      const cols = Math.max(1, Math.floor((width - 16) / (NODE_W + 20)));
      let gi = 0;
      for (const s of sites) {
        if (next[s.id]) continue;
        if (s.map_x != null && s.map_y != null) {
          next[s.id] = { x: s.map_x, y: s.map_y };
        } else {
          next[s.id] = { x: 16 + (gi % cols) * (NODE_W + 20), y: 16 + Math.floor(gi / cols) * (NODE_H + 22) };
          gi++;
        }
      }
      return next;
    });
  }, [sites, width]);

  function onPointerDown(e: React.PointerEvent, id: string) {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    drag.current = { id, offX: e.clientX - rect.left, offY: e.clientY - rect.top, x: pos[id]?.x ?? 0, y: pos[id]?.y ?? 0 };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }

  function onPointerMove(e: React.PointerEvent) {
    const d = drag.current;
    if (!d) return;
    const crect = canvasRef.current!.getBoundingClientRect();
    let x = e.clientX - crect.left - d.offX;
    let y = e.clientY - crect.top - d.offY;
    x = Math.max(0, Math.min(x, crect.width - NODE_W));
    y = Math.max(0, Math.min(y, crect.height - NODE_H));
    d.x = x;
    d.y = y;
    setPos((p) => ({ ...p, [d.id]: { x, y } }));
  }

  function onPointerUp() {
    const d = drag.current;
    if (!d) return;
    api.saveMapPositions([{ site_id: d.id, x: Math.round(d.x), y: Math.round(d.y) }]).catch(() => {});
    drag.current = null;
  }

  return (
    <>
      <p className="map-hint">Drag sites to arrange them — your layout is saved automatically. Color shows live status.</p>
      <div className="map-canvas" ref={canvasRef}>
        {sites.map((s) => {
          const p = pos[s.id] ?? { x: 16, y: 16 };
          return (
            <div
              key={s.id}
              className={`map-node ${s.status}`}
              style={{ left: p.x, top: p.y }}
              onPointerDown={(e) => onPointerDown(e, s.id)}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
            >
              <div className="mn-name" title={s.name}>{s.name}</div>
              <div className="mn-sub">
                <span className={`pill ${s.status}`}><span className="dot" />{s.status}</span>
                <span>{s.online_device_count}/{s.device_count}</span>
                {s.latency_ms != null ? <span>{Math.round(s.latency_ms)}ms</span> : null}
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
