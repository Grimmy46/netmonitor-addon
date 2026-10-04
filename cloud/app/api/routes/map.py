"""Site-map endpoints: persist node positions (and, later, links + background)."""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_user, require_admin
from app.core.db import get_db
from app.models import Site

router = APIRouter(prefix="/map", tags=["map"])


class Position(BaseModel):
    site_id: uuid.UUID
    x: float
    y: float


class PositionsIn(BaseModel):
    positions: list[Position]


@router.put("/positions", status_code=204)
async def save_positions(body: PositionsIn, db: AsyncSession = Depends(get_db), _admin=Depends(require_admin)) -> None:
    """Save fleet-map node positions. Sent on drag-drop; idempotent upsert."""
    ids = [p.site_id for p in body.positions]
    if not ids:
        return
    rows = (await db.execute(select(Site).where(Site.id.in_(ids)))).scalars().all()
    by_id = {s.id: s for s in rows}
    for p in body.positions:
        site = by_id.get(p.site_id)
        if site is not None:
            site.map_x = p.x
            site.map_y = p.y
    await db.commit()


# ── SitePlanner cloud storage + live feed ────────────────────────────────────
# The Planner tab embeds SitePlanner (single-file app) same-origin; these
# endpoints give it per-site cloud save/load and the live 5-state device feed
# it already knows how to consume (netcheck-compatible shape).
from fastapi import HTTPException, Request, Response  # noqa: E402

from app.models import Device, SitePlan  # noqa: E402

MAX_PLAN_BYTES = 2 * 1024 * 1024
MAX_AERIAL_BYTES = 8 * 1024 * 1024


class PlanIn(BaseModel):
    name: str = "Site plan"
    schema_version: int = 4
    data: dict


@router.get("/plans")
async def list_plans(db: AsyncSession = Depends(get_db), _user=Depends(current_user)) -> list[dict]:
    plans = (await db.execute(select(SitePlan))).scalars().all()
    sites = {s.id: s.name for s in (await db.execute(select(Site))).scalars()}
    return [
        {
            "site_id": str(p.site_id),
            "site_name": sites.get(p.site_id),
            "name": p.name,
            "schema_version": p.schema_version,
            "has_aerial": p.aerial is not None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in plans
    ]


@router.get("/plans/{site_id}")
async def get_plan(site_id: uuid.UUID, db: AsyncSession = Depends(get_db), _user=Depends(current_user)) -> dict:
    plan = (
        await db.execute(select(SitePlan).where(SitePlan.site_id == site_id))
    ).scalars().first()
    if plan is None:
        raise HTTPException(status_code=404, detail="No plan saved for this site yet.")
    return {
        "name": plan.name,
        "schema_version": plan.schema_version,
        "data": plan.data,
        "has_aerial": plan.aerial is not None,
        "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
    }


@router.put("/plans/{site_id}", status_code=204)
async def save_plan(
    site_id: uuid.UUID,
    body: PlanIn,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
) -> None:
    if await db.get(Site, site_id) is None:
        raise HTTPException(status_code=404, detail="Unknown site.")
    import json as _json
    if len(_json.dumps(body.data)) > MAX_PLAN_BYTES:
        raise HTTPException(status_code=413, detail="Plan too large (strip the aerial image).")
    plan = (
        await db.execute(select(SitePlan).where(SitePlan.site_id == site_id))
    ).scalars().first()
    if plan is None:
        plan = SitePlan(site_id=site_id)
        db.add(plan)
    plan.name = body.name
    plan.schema_version = body.schema_version
    plan.data = body.data
    await db.commit()


@router.get("/plans/{site_id}/aerial")
async def get_aerial(site_id: uuid.UUID, db: AsyncSession = Depends(get_db), _user=Depends(current_user)) -> Response:
    plan = (
        await db.execute(select(SitePlan).where(SitePlan.site_id == site_id))
    ).scalars().first()
    if plan is None or plan.aerial is None:
        raise HTTPException(status_code=404, detail="No aerial for this site.")
    return Response(content=plan.aerial, media_type=plan.aerial_mime or "image/jpeg")


@router.put("/plans/{site_id}/aerial", status_code=204)
async def save_aerial(
    site_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
) -> None:
    if await db.get(Site, site_id) is None:
        raise HTTPException(status_code=404, detail="Unknown site.")
    raw = await request.body()
    if len(raw) > MAX_AERIAL_BYTES:
        raise HTTPException(status_code=413, detail="Aerial image too large (max 8 MB).")
    if not raw:
        raise HTTPException(status_code=400, detail="Empty body.")
    plan = (
        await db.execute(select(SitePlan).where(SitePlan.site_id == site_id))
    ).scalars().first()
    if plan is None:
        plan = SitePlan(site_id=site_id)
        db.add(plan)
    plan.aerial = raw
    plan.aerial_mime = request.headers.get("content-type") or "image/jpeg"
    await db.commit()


@router.get("/live/{site_id}")
async def live_feed(site_id: uuid.UUID, db: AsyncSession = Depends(get_db), _user=Depends(current_user)) -> dict:
    """Live device status in the shape SitePlanner's Monitor mode consumes.
    Status vocabulary matches the dashboard's 5-state exactly."""
    from datetime import datetime, timezone
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Unknown site.")
    devices = (
        await db.execute(select(Device).where(Device.site_id == site_id))
    ).scalars().all()
    out = []
    for d in devices:
        if not d.mac:
            continue
        if d.is_online is True:
            status = "unreachable" if d.local_reachable is False else "online"
        elif d.is_online is False:
            status = "degraded" if d.local_reachable is True else "offline"
        else:
            status = "unknown"
        out.append(
            {
                "mac": d.mac,
                "name": d.name,
                "model": d.model,
                "ip": d.ip,
                "status": status,
                "type": d.device_type,
                "latency": d.local_rtt_ms,
                "unifi_state": ("ONLINE" if d.is_online else "OFFLINE") if d.is_online is not None else None,
            }
        )
    return {
        "meta": {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "source": "netmonitor",
            "site": site.name,
        },
        "devices": out,
    }


# ── Satellite MAP (replaces the fleet map + planner tabs) ─────────────────────
# One GeoMap per site: view (center/zoom), pins keyed by MAC, share token.
# Live status for signed-in users comes from /network/sites/{id}/topology; the
# public share view gets a sanitized copy (no IPs/MACs) from /map/shared/{t}.
import secrets  # noqa: E402
from datetime import datetime as _dt, timezone as _tz  # noqa: E402

from sqlalchemy.orm.attributes import flag_modified  # noqa: E402

from app.models import GeoMap  # noqa: E402


async def _geo(db: AsyncSession, site_id: uuid.UUID, create: bool = False) -> GeoMap | None:
    g = (await db.execute(select(GeoMap).where(GeoMap.site_id == site_id))).scalars().first()
    if g is None and create:
        if await db.get(Site, site_id) is None:
            raise HTTPException(status_code=404, detail="Unknown site.")
        g = GeoMap(site_id=site_id, placements={})
        db.add(g)
        await db.flush()
    return g


def _geo_out(g: GeoMap | None, with_share: bool) -> dict:
    out = {
        "center": [g.center_lat, g.center_lng] if g and g.center_lat is not None else None,
        "zoom": g.zoom if g else None,
        "placements": (g.placements or {}) if g else {},
    }
    if with_share:
        out["share_token"] = g.share_token if g else None
    return out


@router.get("/geo/{site_id}")
async def get_geo(site_id: uuid.UUID, db: AsyncSession = Depends(get_db), _user=Depends(current_user)) -> dict:
    return _geo_out(await _geo(db, site_id), with_share=True)


class GeoView(BaseModel):
    lat: float
    lng: float
    zoom: int


@router.put("/geo/{site_id}/view")
async def set_geo_view(site_id: uuid.UUID, body: GeoView, db: AsyncSession = Depends(get_db),
                       _admin=Depends(require_admin)) -> dict:
    g = await _geo(db, site_id, create=True)
    g.center_lat, g.center_lng, g.zoom = body.lat, body.lng, max(1, min(22, body.zoom))
    await db.commit()
    return _geo_out(g, with_share=True)


class GeoPin(BaseModel):
    mac: str
    lat: float
    lng: float


@router.put("/geo/{site_id}/pin")
async def set_geo_pin(site_id: uuid.UUID, body: GeoPin, db: AsyncSession = Depends(get_db),
                      _admin=Depends(require_admin)) -> dict:
    g = await _geo(db, site_id, create=True)
    pins = dict(g.placements or {})
    pins[body.mac.lower()] = {"lat": body.lat, "lng": body.lng}
    g.placements = pins
    flag_modified(g, "placements")
    await db.commit()
    return {"ok": True}


@router.delete("/geo/{site_id}/pin/{mac}")
async def del_geo_pin(site_id: uuid.UUID, mac: str, db: AsyncSession = Depends(get_db),
                      _admin=Depends(require_admin)) -> dict:
    g = await _geo(db, site_id)
    if g and mac.lower() in (g.placements or {}):
        pins = dict(g.placements)
        pins.pop(mac.lower())
        g.placements = pins
        flag_modified(g, "placements")
        await db.commit()
    return {"ok": True}


@router.post("/geo/{site_id}/share")
async def share_geo(site_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                    _admin=Depends(require_admin)) -> dict:
    """Create (or rotate — the old link stops working) the view-only link."""
    g = await _geo(db, site_id, create=True)
    g.share_token = secrets.token_urlsafe(18)
    g.share_created_at = _dt.now(tz=_tz.utc)
    await db.commit()
    return {"share_token": g.share_token}


@router.delete("/geo/{site_id}/share")
async def unshare_geo(site_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                      _admin=Depends(require_admin)) -> dict:
    g = await _geo(db, site_id)
    if g:
        g.share_token = None
        g.share_created_at = None
        await db.commit()
    return {"share_token": None}


@router.get("/shared/{token}")
async def shared_geo(token: str, db: AsyncSession = Depends(get_db)) -> dict:
    """PUBLIC (no sign-in): the view-only live map behind a share link.
    Sanitized: names, types, status and links only — no IPs or MACs."""
    from app.api.routes.network import _site_view
    if len(token) < 16:
        raise HTTPException(status_code=404, detail="Link not found.")
    g = (await db.execute(select(GeoMap).where(GeoMap.share_token == token))).scalars().first()
    if g is None:
        raise HTTPException(status_code=404, detail="This link was turned off or never existed.")
    site = await db.get(Site, g.site_id)
    view = await _site_view(db, site)
    nodes = view["nodes"]
    mac_of = {n["id"]: (n["mac"] or "").lower() for n in nodes}
    pins = g.placements or {}
    # Only devices that are pinned are exposed, re-keyed by an opaque index.
    keep = [n for n in nodes if mac_of[n["id"]] in pins]
    alias = {n["id"]: f"d{i}" for i, n in enumerate(keep)}
    out_nodes, out_pins = [], {}
    for n in keep:
        k = alias[n["id"]]
        out_pins[k] = pins[mac_of[n["id"]]]
        out_nodes.append({
            "id": k, "name": n["name"], "model": n["model"], "type": n["type"], "mac": k, "ip": None,
            "is_online": n["is_online"], "dormant": n["dormant"], "down_seconds": n["down_seconds"],
            "local_reachable": n["local_reachable"], "parent_id": alias.get(n["parent_id"]),
            "uplink_port": n["uplink_port"], "local_port": None, "uplink_type": n["uplink_type"],
            "uplink_speed_mbps": n["uplink_speed_mbps"], "depth": n["depth"],
            "ports_up": n["ports_up"], "ports_total": n["ports_total"],
            "outage_root_id": alias.get(n["outage_root_id"]), "last_seen": n["last_seen"],
            "seen_age_s": n["seen_age_s"], "closed_down": n.get("closed_down"),
        })
    return {
        "site_name": site.name,
        "center": [g.center_lat, g.center_lng] if g.center_lat is not None else None,
        "zoom": g.zoom,
        "placements": out_pins,
        "nodes": out_nodes,
        "wan": view["wan"] and {"links": [{k: l.get(k) for k in ("key", "up", "active", "latency_ms")}
                                           for l in view["wan"].get("links", [])]},
        "generated_at": _dt.now(tz=_tz.utc).isoformat(),
    }
