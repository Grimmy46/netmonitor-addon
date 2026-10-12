"""Server tab: host stats, service checks, database, camera relay (signed-in users)."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.core.auth import current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.models import Agent
from app.services import serverstats

router = APIRouter(prefix="/network/server", tags=["server"])  # under /network: already routed by Caddy


async def _http_check(url: str) -> dict:
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(url)
        return {"ok": r.status_code < 500, "ms": round((time.perf_counter() - t0) * 1000), "code": r.status_code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "ms": None, "error": type(e).__name__}


@router.get("/stats")
async def server_stats(db: AsyncSession = Depends(get_db), _user=Depends(current_user)) -> dict:
    st = get_settings()
    out = serverstats.snapshot()
    out["version"] = __version__

    # Services
    t0 = time.perf_counter()
    await db.execute(text("SELECT 1"))
    db_ms = round((time.perf_counter() - t0) * 1000)
    g2r, sig = await asyncio.gather(
        _http_check(st.go2rtc_url.rstrip("/") + "/api"),
        _http_check(st.signal_api_url.rstrip("/") + "/v1/about"),
    )
    out["services"] = [
        {"key": "app", "name": "NetMonitor app", "ok": True, "ms": 0},
        {"key": "db", "name": "Database", "ok": True, "ms": db_ms},
        {"key": "go2rtc", "name": "Camera video (go2rtc)", **g2r},
        {"key": "signal", "name": "Signal bot", **sig},
    ]

    # Database
    size = (await db.execute(text("SELECT pg_database_size(current_database())"))).scalar()
    tables = (await db.execute(text(
        "SELECT relname, pg_total_relation_size(relid) AS b, n_live_tup FROM pg_stat_user_tables "
        "ORDER BY 2 DESC LIMIT 5"))).all()
    conns = (await db.execute(text(
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"))).scalar()
    out["database"] = {"size": int(size or 0), "connections": int(conns or 0),
                       "tables": [{"name": n, "bytes": int(b), "rows": int(r or 0)} for n, b, r in tables]}

    # Stations
    total = (await db.execute(select(func.count(Agent.id)))).scalar() or 0
    online = (await db.execute(select(func.count(Agent.id)).where(Agent.status == "online"))).scalar() or 0
    out["agents"] = {"total": int(total), "online": int(online)}

    # Camera relay
    relay = None
    try:
        from app.api.routes import cams
        cfg = await cams._config(db)
        a = await cams._effective_relay(db, cfg)
        live = await cams._producers()
        if a:
            last = cams._relay_seen.get(str(a.id))
            relay = {"name": a.name, "status": a.status, "version": a.version, "last_seen_at": a.last_seen_at,
                     "polling": bool(last and time.time() - last < 90),
                     "last_poll_s": int(time.time() - last) if last else None,
                     "streams_live": sum(1 for v in live.values() if v)}
    except Exception:  # noqa: BLE001
        relay = None
    out["relay"] = relay
    out["now"] = datetime.now(timezone.utc).isoformat()
    return out
