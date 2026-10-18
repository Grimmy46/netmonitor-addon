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
    """Height (px) of the paper roll inside box (x, y, w, h); 0 = no roll seen,
    None = can't tell. The white paper strip running down into the printer is
    removed (morphological opening), so only the round roll is measured."""
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
    n, lab, st, _cen = cv2.connectedComponentsWithStats(m)
    Hm, Wm = m.shape
    cx = Wm / 2
    cand = [k for k in range(1, n) if st[k, 4] >= 0.03 * Hm * Wm
            and st[k, 0] <= cx + Wm * 0.3 and st[k, 0] + st[k, 2] >= cx - Wm * 0.3]
    if not cand:
        return 0
    i = max(cand, key=lambda k: st[k, 4])
    c = (lab == i).astype(np.uint8)
    cs, _ = cv2.findContours(c, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    c = np.zeros_like(c)
    cv2.drawContours(c, cs, -1, 1, -1)          # fill the core / spindle hole
    k = max(3, int(st[i, 2] * 0.45)) | 1
    o = cv2.morphologyEx(c, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n2, _l2, s2, _ = cv2.connectedComponentsWithStats(o)
    if n2 < 2:
        return 0
    j = 1 + int(np.argmax(s2[1:, 4]))
    return int(s2[j, 3])


def level_from(h: int, full_h: float) -> float:
    """Paper left, % of a full roll. Paper amount goes with the roll's AREA
    (R² − core²), so this matches the printer's own cm counters."""
    core = CORE_RATIO * full_h
    if full_h <= core or h <= core:
        return 0.0
    return max(0.0, min(100.0, (h * h - core * core) / (full_h * full_h - core * core) * 100.0))


ALG = 2  # bump when measure() changes: older learned full_h values are dropped


def full_ref(roi: dict, rois: list[dict], aid: str) -> float | None:
    """Height of a FULL roll for this box: its own calibration if it has one,
    else the biggest calibrated / seen roll on the same camera."""
    if roi.get("alg") == ALG and roi.get("full_h"):
        return float(roi["full_h"])
    best = None
    for r in rois:
        if r.get("alg") == ALG and r.get("full_h"):
            v = float(r["full_h"])
        else:
            v = (latest.get(str(r.get("agent_id"))) or {}).get("h")
        if v and (best is None or v > best):
            best = float(v)
    return best


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
                    if med <= 0:
                        latest[aid] = {**latest.get(aid, {}), "h": 0}
                    a = await db.get(Agent, uuid.UUID(aid))
                    # Printer just reported a new roll → this IS a full roll.
                    rs = a.printer_roll_start_at if a is not None else None
                    used = ((a.printer_paper_cm or 0) - (a.printer_roll_start_cm or 0)) if a is not None else None
                    if (rs is not None and med > 0 and timedelta(minutes=3) < now - rs < timedelta(hours=3)
                            and used is not None and 0 <= used < 500
                            and roi.get("cal_at", "") < rs.isoformat()):
                        roi.update(full_h=float(med), alg=ALG, cal="printer", cal_at=now.isoformat())
                        dirty = True
                    full = full_ref(roi, rois, aid) or float(med)
                    if roi.get("alg") == ALG and roi.get("full_h") and med > full * 1.08:
                        roi["full_h"] = full = float(med)  # clearly bigger than "full" → learn it
                        dirty = True
                    if med <= 0:
                        lvl = 0.0
                    else:
                        lvl = round(level_from(med, max(full, med)), 0)
                    latest[aid] = {"level": lvl, "h": med, "at": now.isoformat(), "camera": cam.name,
                                   "calibrated": bool(roi.get("alg") == ALG and roi.get("full_h"))}
                    prev = _stable.get(aid)
                    if prev is not None and lvl - prev >= CHANGE_JUMP:
                        db.add(PaperVision(agent_id=uuid.UUID(aid), camera_id=cam.id, kind="changed",
                                           level=lvl, prev_level=prev, h_px=med,
                                           detail=f"Roll changed: ~{prev:.0f}% → ~{lvl:.0f}%",
                                           image=_crop_jpeg(img, b)))
                        logger.info("paper vision: %s roll changed %.0f%% -> %.0f%%", aid, prev, lvl)
                        roi.update(full_h=float(med), alg=ALG, cal="camera", cal_at=now.isoformat())
                        dirty = True
                    _stable[aid] = lvl
                    if t - _stored_at.get(aid, 0) >= STORE_EVERY_S:
                        _stored_at[aid] = t
                        db.add(PaperVision(agent_id=uuid.UUID(aid), camera_id=cam.id, kind="reading",
                                           level=lvl, h_px=med))
                    # Jam check: printer says out, camera still sees paper.
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
