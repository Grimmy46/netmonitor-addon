"""Camera paper vision (OpenCV, runs on the server — no paid AI).

Each kiosk camera can have one box per ticket printer drawn around its paper
roll (cameras.paper_rois, set on the Cameras page). Every minute while the
fair is open we grab a still from go2rtc, find the white roll inside each box
and measure its height in pixels. That height, against the biggest height seen
for that roll (a full roll — learned, only ever grows), gives a rough level.

Uses:
  * level      — "camera sees ~60%" next to the printer's own counters
  * changed    — a sudden jump from low to full = roll swapped (logged + still)
  * jam_suspect — the printer says OUT OF PAPER but the camera still sees a
                  decent roll → the alert says "likely a jam / misfeed"

Readings are rough (±10–15 %): people block the view, glare, small rolls at
640×360. Frames that don't look like a roll are skipped, and a single odd
reading never counts — we use the median of the last few.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal

logger = logging.getLogger("netmonitor.papervision")

INTERVAL_S = 60
STORE_EVERY_S = 300        # one stored reading per roll every 5 min
CORE_RATIO = 0.2           # empty core height ≈ 20 % of a full roll
CHANGE_JUMP = 35.0         # level rise (points) that means a new roll
JAM_MIN_LEVEL = 20.0       # camera sees ≥ this while printer says out → jam?

# agent id -> recent valid heights [(t, h)]
_hist: dict[str, deque] = {}
# agent id -> last stable level (for change detection) and last stored time
_stable: dict[str, float] = {}
_stored_at: dict[str, float] = {}
# agent id -> latest {level, h, at, conf}
latest: dict[str, dict] = {}


def _g2r() -> str:
    return get_settings().go2rtc_url.rstrip("/")


def measure(img, box_px):
    """Height (px) of the white roll inside box (x, y, w, h), or None."""
    import cv2
    import numpy as np
    x, y, w, h = box_px
    H, W = img.shape[:2]
    px, py = int(w * 0.15), int(h * 0.15)
    x0, y0, x1, y1 = max(0, x - px), max(0, y - py), min(W, x + w + px), min(H, y + h + py)
    roi = img[y0:y1, x0:x1]
    if roi.size == 0:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    v = hsv[..., 2].astype(int)
    s = hsv[..., 1].astype(int)
    t = max(150, int(np.percentile(v, 60)) + 15)
    m = ((v >= t) & (s < 60)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, _lab, st, cen = cv2.connectedComponentsWithStats(m)
    cx, cy = x + w / 2 - x0, y + h / 2 - y0
    best = None
    for i in range(1, n):
        bx, by, bw, bh, area = st[i]
        if area < 0.04 * w * h:
            continue
        # must overlap the drawn box's centre column / row band
        if not (bx <= cx + w * 0.35 and bx + bw >= cx - w * 0.35):
            continue
        d = abs(cen[i][0] - cx) + abs(cen[i][1] - cy)
        if best is None or d < best[0]:
            best = (d, i)
    if best is None:
        return 0  # nothing white where the roll should be (empty, or blocked)
    bx, by, bw, bh, area = st[best[1]]
    if area / max(1, bw * bh) < 0.25:
        return None  # stringy / odd shape — not a roll face
    return int(min(bh, (y1 - y0)))


def level_from(h: int, full_h: float) -> float:
    core = CORE_RATIO * full_h
    if full_h <= core:
        return 0.0
    return max(0.0, min(100.0, (h - core) / (full_h - core) * 100.0))


def _crop_jpeg(img, box_px) -> bytes | None:
    import cv2
    x, y, w, h = box_px
    H, W = img.shape[:2]
    p = int(max(w, h) * 0.6)
    c = img[max(0, y - p):min(H, y + h + p), max(0, x - p):min(W, x + w + p)]
    if c.size == 0:
        return None
    c = cv2.resize(c, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    ok, buf = cv2.imencode(".jpg", c, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return bytes(buf) if ok else None


async def frame(client: httpx.AsyncClient, slot: int):
    """Latest still from go2rtc as an OpenCV image (or None)."""
    import cv2
    import numpy as np
    try:
        r = await client.get(f"{_g2r()}/api/frame.jpeg", params={"src": f"cam{slot:02d}"})
        if r.status_code != 200 or len(r.content) < 1000:
            return None
        return cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001
        return None


def box_px(roi: dict, img) -> tuple[int, int, int, int]:
    H, W = img.shape[:2]
    return (int(roi["x"] * W), int(roi["y"] * H), max(4, int(roi["w"] * W)), max(4, int(roi["h"] * H)))


async def sweep_once() -> None:
    from app.models import Agent, Camera, PaperVision
    from app.services import closure
    now = datetime.now(timezone.utc)
    if closure.phase(now) == "closed":
        return
    async with SessionLocal() as db:
        cams = [c for c in (await db.execute(select(Camera))).scalars() if c.enabled and c.paper_rois]
        if not cams:
            return
        async with httpx.AsyncClient(timeout=15) as client:
            for cam in cams:
                img = await frame(client, cam.slot)
                if img is None:
                    continue
                rois = [dict(r) for r in cam.paper_rois]
                dirty = False
                for roi in rois:
                    aid = str(roi.get("agent_id") or "")
                    if not aid:
                        continue
                    b = box_px(roi, img)
                    h = await asyncio.to_thread(measure, img, b)
                    if h is None:
                        continue
                    t = time.time()
                    hist = _hist.setdefault(aid, deque(maxlen=7))
                    hist.append((t, h))
                    recent = sorted(x[1] for x in hist if t - x[0] < 8 * 60)
                    if len(recent) < 3:
                        continue
                    med = recent[len(recent) // 2]
                    full = float(roi.get("full_h") or b[3])
                    if med > full:  # learn a full roll's height (only grows)
                        roi["full_h"] = full = float(med)
                        dirty = True
                    lvl = round(level_from(med, full), 0)
                    latest[aid] = {"level": lvl, "h": med, "at": now.isoformat(), "camera": cam.name}
                    prev = _stable.get(aid)
                    if prev is not None and lvl - prev >= CHANGE_JUMP:
                        db.add(PaperVision(agent_id=uuid.UUID(aid), camera_id=cam.id, kind="changed",
                                           level=lvl, prev_level=prev, h_px=med,
                                           detail=f"Roll changed: ~{prev:.0f}% → ~{lvl:.0f}%",
                                           image=_crop_jpeg(img, b)))
                        logger.info("paper vision: %s roll changed %.0f%% -> %.0f%%", aid, prev, lvl)
                    _stable[aid] = lvl
                    if t - _stored_at.get(aid, 0) >= STORE_EVERY_S:
                        _stored_at[aid] = t
                        db.add(PaperVision(agent_id=uuid.UUID(aid), camera_id=cam.id, kind="reading",
                                           level=lvl, h_px=med))
                    # Jam check: printer says out, camera still sees paper.
                    a = await db.get(Agent, uuid.UUID(aid))
                    if (a is not None and a.printer_status == "paper_out" and a.printer_out_since
                            and lvl >= JAM_MIN_LEVEL and now - a.printer_out_since < timedelta(minutes=30)):
                        last = (await db.execute(
                            select(PaperVision).where(PaperVision.agent_id == a.id, PaperVision.kind == "jam_suspect",
                                                      PaperVision.created_at >= a.printer_out_since).limit(1)
                        )).scalars().first()
                        if last is None:
                            db.add(PaperVision(agent_id=a.id, camera_id=cam.id, kind="jam_suspect", level=lvl,
                                               h_px=med, detail=f"Printer says out of paper, camera sees ~{lvl:.0f}% on the roll",
                                               image=_crop_jpeg(img, b)))
                if dirty:
                    cam.paper_rois = rois
        await db.commit()


def hint(agent_id) -> str | None:
    """Text for the paper-out alert, if the camera disagrees with the printer."""
    r = latest.get(str(agent_id))
    if not r:
        return None
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(r["at"])).total_seconds()
    except Exception:  # noqa: BLE001
        return None
    if age > 15 * 60:
        return None
    if r["level"] >= JAM_MIN_LEVEL:
        return f"Camera still sees ~{r['level']:.0f}% paper on the roll — likely a JAM or misfeed, not an empty roll."
    return f"Camera agrees: roll looks empty (~{r['level']:.0f}%)."


async def run_paper_vision() -> None:
    await asyncio.sleep(30)
    while True:
        try:
            await sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("paper vision sweep failed: %s", exc)
        await asyncio.sleep(INTERVAL_S)
