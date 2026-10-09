"""Network map + at-a-glance health.

GET /network/overview                — the landing strip: Main's WAN1/WAN2,
                                        switches/APs online, outages with cause.
GET/PUT/DELETE /network/closure     — planned closure (alerts pause, dormant
                                        clock freezes, reopen report). Admin.
GET /network/sites/{site_id}/topology — the self-building map for one site:
                                        every device, what it plugs into (and on
                                        which port), and outages grouped by root.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_user, require_admin
from app.core.config import get_settings
from app.core.db import get_db
from app.models import Account, Device, Site
from app.services import closure
from app.services.topology import analyze, describe_outage, is_dormant

router = APIRouter(prefix="/network", tags=["network"])


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _node(d: Device, parent: Device | None, root_id, now: datetime) -> dict:
    down_s = (now - d.offline_since).total_seconds() if (
        d.is_online is False and d.offline_since) else None
    return {
        "id": str(d.id), "name": d.name, "model": d.model, "type": d.device_type,
        "mac": d.mac, "ip": d.ip, "is_online": d.is_online,
        "dormant": is_dormant(d, now), "down_seconds": down_s,
        "local_reachable": d.local_reachable,
        "parent_id": str(parent.id) if parent else None,
        "uplink_port": d.uplink_port, "local_port": d.local_port,
        "uplink_type": d.uplink_type, "uplink_speed_mbps": d.uplink_speed_mbps,
        "depth": d.uplink_depth, "ports_up": d.ports_up, "ports_total": d.ports_total,
        "outage_root_id": str(root_id) if root_id else None,
        "last_seen": d.unifi_last_seen.isoformat() if d.unifi_last_seen else None,
        "seen_age_s": (now - d.unifi_last_seen).total_seconds() if d.unifi_last_seen else None,
        "closed_down": closure.powered_down_for_closure(d, now),
    }


def _outage(o, now: datetime) -> dict:
    r = o.root
    return {
        "root_id": str(r.id), "root_name": r.name, "root_type": r.device_type,
        "since": r.offline_since.isoformat() if r.offline_since else None,
        "down_seconds": (now - r.offline_since).total_seconds() if r.offline_since else None,
        "affected_count": len(o.affected),
        "affected": [{"id": str(d.id), "name": d.name, "type": d.device_type}
                     for d in o.affected if d.id != r.id],
        "parent_id": str(o.parent.id) if o.parent else None,
        "parent_name": o.parent.name if o.parent else None,
        "parent_online": o.parent.is_online if o.parent else None,
        "uplink_port": r.uplink_port, "uplink_type": r.uplink_type,
        "summary": describe_outage(o),
        "closed_down": closure.powered_down_for_closure(r, now),
    }


def _wan(site: Site, now: datetime) -> dict | None:
    if not site.wan_status:
        return None
    age = (now - site.wan_status_at).total_seconds() if site.wan_status_at else None
    return {**site.wan_status, "age_seconds": age,
            "stale": age is None or age > 5 * get_settings().topology_interval_seconds}


async def _site_view(db: AsyncSession, site: Site) -> dict:
    now = _now()
    devices = (await db.execute(select(Device).where(Device.site_id == site.id))).scalars().all()
    a = analyze(devices, now)
    parent_of, root_of, down = a["parent_of"], a["root_of"], a["down"]
    nodes = [_node(d, parent_of.get(d.id), root_of(d).id if down(d) else None, now)
             for d in devices]
    live = [d for d in devices if not is_dormant(d, now)]

    def count(kind):
        xs = [d for d in live if d.device_type == kind]
        return {"online": sum(1 for d in xs if d.is_online), "total": len(xs)}

    unreachable = [
        {"id": str(d.id), "name": d.name, "type": d.device_type}
        for d in live if d.is_online and d.local_reachable is False
    ]
    return {
        "site_id": str(site.id), "site_name": site.name, "wan": _wan(site, now),
        "counts": {"switch": count("switch"), "ap": count("ap"), "gateway": count("gateway")},
        "outages": [_outage(o, now) for o in a["outages"]],
        "unreachable": unreachable,
        "nodes": nodes,
        "topology_at": max((d.topology_at for d in devices if d.topology_at), default=None),
        "closure": await _closure_out(db, now),
    }


async def _closure_out(db: AsyncSession, now: datetime | None = None) -> dict | None:
    now = now or _now()
    await closure.refresh(db)
    ph = closure.phase(now)
    if ph is None:
        return None
    acc = (await db.execute(select(Account).limit(1))).scalar_one_or_none()
    start, end = closure.window()  # type: ignore[misc]
    return {"phase": ph, "start": start.isoformat(), "end": end.isoformat(),
            "reopen_until": closure.reopen_until().isoformat(),
            "note": acc.closure_note if acc else None}


class ClosureIn(BaseModel):
    start: datetime
    end: datetime
    note: str | None = None


@router.get("/closure")
async def get_closure(db: AsyncSession = Depends(get_db), _user=Depends(current_user)) -> dict:
    return {"closure": await _closure_out(db)}


@router.put("/closure")
async def set_closure(body: ClosureIn, db: AsyncSession = Depends(get_db),
                      _admin=Depends(require_admin)) -> dict:
    start = body.start if body.start.tzinfo else body.start.replace(tzinfo=timezone.utc)
    end = body.end if body.end.tzinfo else body.end.replace(tzinfo=timezone.utc)
    if end <= start:
        raise HTTPException(status_code=422, detail="End must be after start")
    if (end - start).days > 30:
        raise HTTPException(status_code=422, detail="Closures are capped at 30 days")
    acc = (await db.execute(select(Account).limit(1))).scalar_one_or_none()
    if acc is None:
        raise HTTPException(status_code=404, detail="No account")
    acc.closure_start, acc.closure_end = start, end
    acc.closure_note = (body.note or "").strip()[:120] or None
    acc.closure_report_sent_at = None
    acc.closure_grace_min = None
    await db.commit()
    return {"closure": await _closure_out(db)}


@router.post("/closure/tonight")
async def close_tonight(db: AsyncSession = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    """"We're closed": pause alerts now until the next gate opening (from the
    show route), with a short 30-min reopen grace."""
    from app.services import nightly
    acc = (await db.execute(select(Account).limit(1))).scalar_one_or_none()
    if acc is None:
        raise HTTPException(status_code=404, detail="No account")
    await nightly.close_until_open(db, acc, _now(), "Closed for the night")
    return {"closure": await _closure_out(db)}


# Phone shortcut / NFC tag: a secret link, no browser session (like agent
# endpoints). POST /network/closure/shortcut/<token>/closed  (or /open).
@router.post("/closure/shortcut/{token}/{action}")
async def closure_shortcut(token: str, action: str, db: AsyncSession = Depends(get_db)) -> dict:
    from app.services import netbot
    if not await netbot.check_token(db, token):
        raise HTTPException(status_code=404, detail="Not found")
    now = _now()
    if action in ("closed", "close"):
        msg = await netbot.close_now(db, now, "phone shortcut")
    elif action == "open":
        msg = await netbot.open_now(db, now, "phone shortcut")
    else:
        raise HTTPException(status_code=404, detail="Not found")
    await netbot.dm(db, msg, "netbot-shortcut")
    return {"ok": True, "message": msg.replace("NETBOT: ", "")}


@router.get("/closure/shortcut-link")
async def shortcut_link(rotate: bool = False, db: AsyncSession = Depends(get_db),
                        _admin=Depends(require_admin)) -> dict:
    from app.services import netbot
    return {"token": await netbot.shortcut_token(db, rotate)}


@router.delete("/closure")
async def clear_closure(db: AsyncSession = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    """Cancel (or end early). Ending early still gets the reopen report: the
    end moves to now, so the grace window + summary push run as normal."""
    acc = (await db.execute(select(Account).limit(1))).scalar_one_or_none()
    now = _now()
    if acc and acc.closure_start and acc.closure_end:
        if acc.closure_start > now:            # not started yet: just cancel
            acc.closure_start = acc.closure_end = acc.closure_note = None
        elif acc.closure_end > now:            # in progress: reopen now
            acc.closure_end = now
        else:                                  # in the reopen grace: finish it
            acc.closure_start = acc.closure_end = acc.closure_note = None
        await db.commit()
    return {"closure": await _closure_out(db)}


@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db), _user=Depends(current_user)) -> dict:
    st = get_settings()
    name = st.alert_site_name or st.default_probe_site_name
    site = (await db.execute(select(Site).where(Site.name == name))).scalars().first()
    if site is None:
        raise HTTPException(status_code=404, detail=f"Site {name!r} not found")
    view = await _site_view(db, site)
    view.pop("nodes")   # the landing strip doesn't need the whole tree
    return view


@router.get("/sites/{site_id}/topology")
async def site_topology(site_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                        _user=Depends(current_user)) -> dict:
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")
    return await _site_view(db, site)
