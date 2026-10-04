"""Tapo cameras: list/config for the dashboard, the relay kiosk's plan + video
ingest, and the live-view WebSocket.

Video path (no new ports, everything over HTTPS 443):
  camera --RTSP--> go2rtc on the relay kiosk (localhost only)
         --MPEG-TS over HTTPS POST /cams/ingest/<slot>--> this API
         --> go2rtc container (internal) --MSE over WS /cams/ws--> browser
A camera only streams while someone is watching it ("wanted").
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.agents import _agent_from_token
from app.core.auth import _user_from_request, require_admin
from app.core.config import get_settings
from app.core.db import SessionLocal, get_db
from app.core.security import decrypt, encrypt
from app.models import Agent, Camera, CameraConfig, GeoMap, User

logger = logging.getLogger("netmonitor.cams")
router = APIRouter(prefix="/cams", tags=["cameras"])

G2R_VERSION = "1.9.14"
_G2R_ZIP = f"https://github.com/AlexxIT/go2rtc/releases/download/v{G2R_VERSION}/go2rtc_win64.zip"
_EXE_CACHE = Path("/tmp") / f"go2rtc-win64-{G2R_VERSION}.exe"
_WANT_S = 20          # a camera stays wanted this long after the last viewer ping
_ONLINE_S = 150       # relay kiosk counts as up if seen this recently

_wanted: dict[int, float] = {}      # slot -> last time a viewer wanted it
_relay_seen: dict[str, float] = {}  # agent id -> last relay-plan poll
_exe_sha: str | None = None


def _g2r() -> str:
    return get_settings().go2rtc_url.rstrip("/")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _config(db: AsyncSession) -> CameraConfig:
    cfg = (await db.execute(select(CameraConfig).limit(1))).scalars().first()
    if cfg is None:
        cfg = CameraConfig()
        db.add(cfg)
        await db.flush()
    return cfg


def _online(a: Agent | None) -> bool:
    if a is None or a.powered_off_at is not None or a.last_seen_at is None:
        return False
    try:
        seen = datetime.fromisoformat(str(a.last_seen_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return (_now() - seen).total_seconds() <= _ONLINE_S


async def _effective_relay(db: AsyncSession, cfg: CameraConfig) -> Agent | None:
    """The chosen relay kiosk; if it's down, the first online kiosk at the same
    site (by name) takes over so a single dead PC doesn't blank the cameras."""
    pref = await db.get(Agent, cfg.relay_agent_id) if cfg.relay_agent_id else None
    if pref is None:
        return None
    if _online(pref):
        return pref
    others = [a for a in (await db.execute(select(Agent))).scalars()
              if a.id != pref.id and a.site_id == pref.site_id and _online(a)]
    others.sort(key=lambda a: a.name or "")
    return others[0] if others else None


async def _producers() -> dict[str, bool]:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{_g2r()}/api/streams")
            data = r.json() or {}
    except Exception:  # noqa: BLE001
        return {}
    return {k: bool((v or {}).get("producers")) for k, v in data.items()}


async def _viewer_ok(db: AsyncSession, request_like, share: str | None) -> bool:
    if await _user_from_request(request_like, db) is not None:
        return True
    if share and len(share) >= 16:
        g = (await db.execute(select(GeoMap).where(GeoMap.share_token == share))).scalars().first()
        return g is not None
    return False


# ── dashboard ──────────────────────────────────────────────────────────────
@router.get("")
async def list_cams(request: Request, share: str | None = None, db: AsyncSession = Depends(get_db)) -> dict:
    if not await _viewer_ok(db, request, share):
        raise HTTPException(status_code=401, detail="Sign in required.")
    cams = list((await db.execute(select(Camera).order_by(Camera.slot))).scalars())
    live = await _producers()
    cfg = await _config(db)
    relay = await _effective_relay(db, cfg)
    is_user = await _user_from_request(request, db) is not None
    out = []
    for c in cams:
        if not is_user and not c.enabled:
            continue
        row = {"id": str(c.id), "slot": c.slot, "name": c.name, "enabled": c.enabled,
               "live": live.get(f"cam{c.slot:02d}", False)}
        if is_user:
            row.update({"model": c.model, "ip": c.ip, "ap_name": c.ap_name, "mac": c.mac,
                        "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None})
        out.append(row)
    res = {"cameras": out}
    if is_user:
        last = _relay_seen.get(str(relay.id)) if relay else None
        res["relay"] = {
            "agent_id": str(relay.id) if relay else None,
            "name": relay.name if relay else None,
            "preferred_id": str(cfg.relay_agent_id) if cfg.relay_agent_id else None,
            "polling": bool(last and time.time() - last < 90),
            "login_set": bool(cfg.username and cfg.password_enc),
            "username": cfg.username,
        }
    await db.commit()
    return res


class CamUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None


@router.put("/{cam_id}")
async def update_cam(cam_id: uuid.UUID, body: CamUpdate, _: User = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)) -> dict:
    c = await db.get(Camera, cam_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Camera not found.")
    if body.name is not None:
        c.name = body.name.strip()[:60] or c.name
    if body.enabled is not None:
        c.enabled = body.enabled
    await db.commit()
    return {"ok": True}


class CamConfigIn(BaseModel):
    username: str | None = None
    password: str | None = None        # write-only; blank = keep
    relay_agent_id: uuid.UUID | None = None


@router.put("/config/settings")
async def put_config(body: CamConfigIn, _: User = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)) -> dict:
    cfg = await _config(db)
    if body.username is not None:
        cfg.username = body.username.strip() or None
    if body.password:
        cfg.password_enc = encrypt(body.password)
    if body.relay_agent_id is not None:
        if await db.get(Agent, body.relay_agent_id) is None:
            raise HTTPException(status_code=404, detail="Kiosk not found.")
        cfg.relay_agent_id = body.relay_agent_id
    await db.commit()
    return {"ok": True}


# ── relay kiosk ────────────────────────────────────────────────────────────
@router.get("/relay-plan")
async def relay_plan(x_agent_token: str | None = Header(default=None),
                     db: AsyncSession = Depends(get_db)) -> dict:
    agent = await _agent_from_token(db, x_agent_token)
    cfg = await _config(db)
    relay = await _effective_relay(db, cfg)
    await db.commit()
    if relay is None or relay.id != agent.id or not (cfg.username and cfg.password_enc):
        return {"relay": False, "poll": 60}
    _relay_seen[str(agent.id)] = time.time()
    pw = decrypt(cfg.password_enc)
    cams = [c for c in (await db.execute(select(Camera).order_by(Camera.slot))).scalars()
            if c.enabled and c.ip and c.site_id == agent.site_id]
    now = time.time()
    slots = {c.slot for c in cams}
    return {
        "relay": True, "poll": 3, "sha256": await _exe_sha256(),
        "cams": [{"slot": c.slot, "rtsp": f"rtsp://{cfg.username}:{pw}@{c.ip}:554/stream2"} for c in cams],
        "wanted": sorted(s for s, t in _wanted.items() if s in slots and now - t < _WANT_S),
    }


async def _exe_sha256() -> str:
    global _exe_sha
    if _exe_sha is None:
        await _ensure_exe()
    return _exe_sha or ""


async def _ensure_exe() -> Path:
    global _exe_sha
    if not _EXE_CACHE.exists():
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
            r = await c.get(_G2R_ZIP)
            r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".exe"))
            tmp = _EXE_CACHE.with_suffix(".part")
            tmp.write_bytes(z.read(name))
            tmp.replace(_EXE_CACHE)
    if _exe_sha is None:
        _exe_sha = hashlib.sha256(_EXE_CACHE.read_bytes()).hexdigest()
    return _EXE_CACHE


@router.get("/go2rtc.exe")
async def relay_exe(x_agent_token: str | None = Header(default=None),
                    db: AsyncSession = Depends(get_db)):
    await _agent_from_token(db, x_agent_token)
    path = await _ensure_exe()
    return FileResponse(path, media_type="application/octet-stream", filename="netmon-go2rtc.exe")


@router.post("/ingest/{slot}")
async def ingest(slot: int, request: Request, x_agent_token: str | None = Header(default=None)):
    async with SessionLocal() as db:   # short-lived: the upload can run for hours
        agent = await _agent_from_token(db, x_agent_token)
        cfg = await _config(db)
        relay = await _effective_relay(db, cfg)
        await db.commit()
    if relay is None or relay.id != agent.id:
        raise HTTPException(status_code=403, detail="Not the camera relay.")
    if not 1 <= slot <= 99:
        raise HTTPException(status_code=404, detail="No such camera.")

    async def body():
        async for chunk in request.stream():
            yield chunk
    try:
        timeout = httpx.Timeout(connect=10, read=None, write=None, pool=10)
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(f"{_g2r()}/api/stream.ts?dst=cam{slot:02d}", content=body(),
                             headers={"Content-Type": "video/mp2t"})
        return Response(status_code=r.status_code)
    except Exception as exc:  # noqa: BLE001
        logger.info("cam%02d ingest ended: %s", slot, exc)
        return Response(status_code=204)


# ── viewer ─────────────────────────────────────────────────────────────────
@router.websocket("/ws")
async def cam_ws(ws: WebSocket, cam: str, share: str | None = None):
    import websockets
    async with SessionLocal() as db:
        ok = await _viewer_ok(db, ws, share)
        c = None
        try:
            c = await db.get(Camera, uuid.UUID(cam)) if ok else None
        except ValueError:
            c = None
    if not ok or c is None or (not c.enabled):
        await ws.close(code=4401)
        return
    slot = c.slot
    await ws.accept()
    src = f"cam{slot:02d}"

    async def keep_wanted():
        while True:
            _wanted[slot] = time.time()
            await asyncio.sleep(5)
    keeper = asyncio.create_task(keep_wanted())
    try:
        # Wait (up to ~30 s) for the relay kiosk to start pushing this camera.
        for _ in range(60):
            if (await _producers()).get(src):
                break
            await asyncio.sleep(0.5)
        else:
            await ws.send_json({"type": "error", "value": "camera not reachable (relay kiosk offline?)"})
            await ws.close()
            return
        url = _g2r().replace("http", "ws", 1) + f"/api/ws?src={src}"
        async with websockets.connect(url, max_size=None, open_timeout=10) as up:
            async def down_to_up():
                while True:
                    m = await ws.receive()
                    if m.get("type") == "websocket.disconnect":
                        return
                    if m.get("text") is not None:
                        await up.send(m["text"])
                    elif m.get("bytes") is not None:
                        await up.send(m["bytes"])

            async def up_to_down():
                async for m in up:
                    if isinstance(m, bytes):
                        await ws.send_bytes(m)
                    else:
                        await ws.send_text(m)
            tasks = [asyncio.create_task(down_to_up()), asyncio.create_task(up_to_down())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
    except (WebSocketDisconnect, Exception) as exc:  # noqa: BLE001
        logger.debug("cam ws %s closed: %s", src, exc)
    finally:
        keeper.cancel()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
