"""Network map + at-a-glance health.

GET /network/overview                — the landing strip: Main's WAN1/WAN2,
                                        switches/APs online, outages with cause.
GET /network/sites/{site_id}/topology — the self-building map for one site:
                                        every device, what it plugs into (and on
                                        which port), and outages grouped by root.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.models import Device, Site
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
    }


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
