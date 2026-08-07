"""Site-map endpoints: persist node positions (and, later, links + background)."""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
async def save_positions(body: PositionsIn, db: AsyncSession = Depends(get_db)) -> None:
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
import base64  # noqa: E402
from fastapi import Header, HTTPException, Request, Response  # noqa: E402

from app.api.routes.adminpin import require_admin_pin  # noqa: E402
from app.models import Device, SitePlan  # noqa: E402

MAX_PLAN_BYTES = 2 * 1024 * 1024
MAX_AERIAL_BYTES = 8 * 1024 * 1024


class PlanIn(BaseModel):
    name: str = "Site plan"
    schema_version: int = 4
    data: dict


@router.get("/plans")
async def list_plans(db: AsyncSession = Depends(get_db)) -> list[dict]:
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
async def get_plan(site_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
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
    x_admin_pin: str | None = Header(default=None),
) -> None:
    await require_admin_pin(db, x_admin_pin)
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
async def get_aerial(site_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Response:
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
    x_admin_pin: str | None = Header(default=None),
) -> None:
    await require_admin_pin(db, x_admin_pin)
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
async def live_feed(site_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
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
