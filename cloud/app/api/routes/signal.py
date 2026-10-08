"""Signal bot admin + archive. Mounted under /integrations/signal (Caddy
already proxies /integrations*)."""
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_user, require_admin
from app.core.db import get_db
from app.models import SignalMessage
from app.services import signal as sig

router = APIRouter(prefix="/integrations/signal", tags=["signal"])


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db), _a=Depends(require_admin)) -> dict:
    up = await sig.available()
    num = await sig.linked_number(db) if up else None
    cfg = await sig.get_config(db)
    counts = dict((await db.execute(
        select(SignalMessage.group_id, func.count()).group_by(SignalMessage.group_id))).all())
    await db.commit()
    return {
        "service": up, "number": num, "watched": cfg.watched or {}, "alert_group_id": cfg.alert_group_id,
        "last_receive_at": cfg.last_receive_at.isoformat() if cfg.last_receive_at else None,
        "last_error": cfg.last_error, "counts": counts,
    }


@router.get("/link-qr")
async def link_qr(_a=Depends(require_admin)) -> Response:
    """QR to scan in Signal → Settings → Linked devices → Link new device."""
    try:
        r = await sig.api_get("/v1/qrcodelink", timeout=40, device_name="NetMonitor")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Signal service not reachable: {exc}") from exc
    return Response(content=r.content, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/groups")
async def list_groups(db: AsyncSession = Depends(get_db), _a=Depends(require_admin)) -> list[dict]:
    num = await sig.linked_number(db)
    if not num:
        raise HTTPException(status_code=409, detail="Link Signal first.")
    try:
        return await sig.groups(num)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Couldn't list groups: {exc}") from exc


class WatchBody(BaseModel):
    watched: dict          # {group_id: {name, role}}
    alert_group_id: str | None = None


@router.put("/watch")
async def set_watch(body: WatchBody, db: AsyncSession = Depends(get_db), _a=Depends(require_admin)) -> dict:
    cfg = await sig.get_config(db)
    clean = {}
    for gid, v in (body.watched or {}).items():
        role = (v or {}).get("role", "other")
        clean[str(gid)] = {"name": str((v or {}).get("name", ""))[:120],
                           "role": role if role in ("deploy", "hardware", "other") else "other"}
    cfg.watched = clean
    cfg.alert_group_id = body.alert_group_id or None
    await db.commit()
    return {"watched": cfg.watched, "alert_group_id": cfg.alert_group_id}


@router.post("/test")
async def test_send(db: AsyncSession = Depends(get_db), _a=Depends(require_admin)) -> dict:
    cfg = await sig.get_config(db)
    if not (cfg.number and cfg.alert_group_id):
        raise HTTPException(status_code=409, detail="Pick an alerts group first.")
    try:
        await sig.send_group(cfg.number, cfg.alert_group_id, "✅ NetMonitor test — alerts will post here.")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Send failed: {exc}") from exc
    return {"ok": True}


@router.post("/import")
async def import_history(file: UploadFile = File(...), group_name: str = Form(""),
                         db: AsyncSession = Depends(get_db), _a=Depends(require_admin)) -> dict:
    data = await file.read()
    if len(data) > 80 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (80 MB max).")
    try:
        res = await sig.import_history(db, file.filename or "chat.txt", data, group_name.strip())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Couldn't read that file: {exc}") from exc
    if not res["parsed"]:
        raise HTTPException(status_code=422, detail="No messages found — expected lines like “[2025-03-12 14:22] Name: text”.")
    return res


@router.get("/messages")
async def messages(q: str = "", group: str = "", before: str = "", limit: int = Query(100, le=500),
                   db: AsyncSession = Depends(get_db), _u=Depends(current_user)) -> dict:
    stmt = select(SignalMessage)
    if group:
        stmt = stmt.where(SignalMessage.group_id == group)
    if q.strip():
        words = [w for w in q.split() if w][:6]
        for w in words:
            stmt = stmt.where(or_(SignalMessage.body.ilike(f"%{w}%"), SignalMessage.sender.ilike(f"%{w}%")))
    if before:
        stmt = stmt.where(SignalMessage.sent_at < datetime.fromisoformat(before))
    rows = list((await db.execute(stmt.order_by(SignalMessage.sent_at.desc()).limit(limit))).scalars())
    return {"messages": [{"id": str(m.id), "group_id": m.group_id, "group": m.group_name, "sender": m.sender,
                          "at": m.sent_at.isoformat(), "body": m.body, "attachments": m.attachments,
                          "source": m.source} for m in rows]}


@router.get("/name-suggestions")
async def name_suggestions(db: AsyncSession = Depends(get_db), _a=Depends(require_admin)) -> list[dict]:
    return await sig.name_suggestions(db)


# ── NETBOT daily opening report ────────────────────────────────────────────
class MorningIn(BaseModel):
    enabled: bool | None = None
    time: str | None = None
    tz: str | None = None
    speedtest: bool | None = None
    min_down: int | None = None
    min_up: int | None = None
    max_latency: int | None = None
    follow_route: bool | None = None
    after_open_min: int | None = None


@router.get("/morning")
async def morning_get(db: AsyncSession = Depends(get_db), _=Depends(current_user)) -> dict:
    from app.models import Account
    from app.services import morning
    acc = (await db.execute(select(Account).limit(1))).scalars().first()
    from datetime import datetime, timezone
    from app.services import route
    now = datetime.now(timezone.utc)
    cfg = morning.config(acc)
    due = morning.due_today(cfg, now)
    return {"config": cfg, "last": acc.morning_report_last if acc else None,
            "route": route.today_info(now), "due_today": due.isoformat() if due else None,
            "preview": await morning.build(db)}


@router.put("/morning")
async def morning_put(body: MorningIn, db: AsyncSession = Depends(get_db), _=Depends(require_admin)) -> dict:
    import re
    from zoneinfo import ZoneInfo
    from app.models import Account
    from app.services import morning
    acc = (await db.execute(select(Account).limit(1))).scalars().first()
    if acc is None:
        raise HTTPException(status_code=404, detail="No account.")
    cfg = morning.config(acc)
    upd = body.model_dump(exclude_none=True)
    if "time" in upd and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", upd["time"]):
        raise HTTPException(status_code=400, detail="Time must be HH:MM.")
    if "tz" in upd:
        try:
            ZoneInfo(upd["tz"])
        except Exception:
            raise HTTPException(status_code=400, detail="Unknown time zone.")
    cfg.update(upd)
    acc.morning_report = cfg
    await db.commit()
    return {"config": cfg}


@router.post("/morning/send-now")
async def morning_send_now(db: AsyncSession = Depends(get_db), _=Depends(require_admin)) -> dict:
    from app.services import morning
    text = await morning.build(db)
    try:
        await morning.send(db, text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Signal send failed: {exc}")
    return {"sent": True, "text": text}
