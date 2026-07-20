"""Read endpoints for sites and their devices."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models import Device, Site
from app.schemas import DeviceOut, SiteOut

router = APIRouter(prefix="/sites", tags=["sites"])


@router.get("", response_model=list[SiteOut])
async def list_sites(db: AsyncSession = Depends(get_db)) -> list[SiteOut]:
    count = func.count(Device.id)
    stmt = (
        select(Site, count)
        .outerjoin(Device, Device.site_id == Site.id)
        .group_by(Site.id)
        .order_by(Site.name)
    )
    rows = (await db.execute(stmt)).all()
    return [
        SiteOut(
            id=site.id,
            name=site.name,
            isp_name=site.isp_name,
            status=site.status,
            device_count=n,
        )
        for site, n in rows
    ]


@router.get("/{site_id}/devices", response_model=list[DeviceOut])
async def list_site_devices(
    site_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[Device]:
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")
    stmt = select(Device).where(Device.site_id == site_id).order_by(Device.name)
    return list((await db.execute(stmt)).scalars())
