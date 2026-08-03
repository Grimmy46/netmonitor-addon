"""Read endpoints for sites, their devices, and WAN metric history."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models import Device, IspMetric, Site
from app.schemas import DeviceOut, MetricPoint, SiteOut

router = APIRouter(prefix="/sites", tags=["sites"])


async def _latest_metrics_by_site(db: AsyncSession) -> dict:
    """Most-recent IspMetric per site (small fleet → fetch recent and reduce)."""
    rows = (
        await db.execute(select(IspMetric).order_by(IspMetric.ts.desc()).limit(2000))
    ).scalars()
    latest: dict = {}
    for m in rows:
        if m.site_id not in latest:
            latest[m.site_id] = m
    return latest


@router.get("", response_model=list[SiteOut])
async def list_sites(db: AsyncSession = Depends(get_db)) -> list[SiteOut]:
    total = func.count(Device.id)
    online = func.count(Device.id).filter(Device.is_online.is_(True))
    stmt = (
        select(Site, total, online)
        .outerjoin(Device, Device.site_id == Site.id)
        .group_by(Site.id)
        .order_by(Site.name)
    )
    rows = (await db.execute(stmt)).all()
    latest = await _latest_metrics_by_site(db)

    out: list[SiteOut] = []
    for site, n_total, n_online in rows:
        m = latest.get(site.id)
        # UniFi's own per-site counts are authoritative (they cover every
        # adopted device, not just the ones the /devices sync attached).
        # Fall back to the joined device rows if counts weren't captured.
        total = site.device_total or n_total
        online = (
            (site.device_total - site.device_offline)
            if site.device_total
            else n_online
        )
        out.append(
            SiteOut(
                id=site.id,
                name=site.name,
                isp_name=site.isp_name,
                status=site.status,
                device_count=total,
                online_device_count=online,
                latency_ms=m.latency_ms if m else None,
                packet_loss_pct=m.packet_loss_pct if m else None,
                # Prefer the live metric uptime; fall back to the site's
                # reported WAN uptime from the last sync.
                uptime_pct=(m.uptime_pct if m and m.uptime_pct is not None else site.wan_uptime_pct),
                download_mbps=m.download_mbps if m else None,
                upload_mbps=m.upload_mbps if m else None,
                map_x=site.map_x,
                map_y=site.map_y,
            )
        )
    return out


@router.get("/{site_id}/devices", response_model=list[DeviceOut])
async def list_site_devices(site_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[Device]:
    if await db.get(Site, site_id) is None:
        raise HTTPException(status_code=404, detail="Site not found")
    stmt = select(Device).where(Device.site_id == site_id).order_by(Device.name)
    return list((await db.execute(stmt)).scalars())


@router.get("/{site_id}/metrics", response_model=list[MetricPoint])
async def site_metrics(
    site_id: uuid.UUID,
    limit: int = Query(200, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
) -> list[MetricPoint]:
    if await db.get(Site, site_id) is None:
        raise HTTPException(status_code=404, detail="Site not found")
    stmt = (
        select(IspMetric)
        .where(IspMetric.site_id == site_id)
        .order_by(IspMetric.ts.desc())
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars())
    rows.reverse()  # chronological for charting
    return [
        MetricPoint(
            ts=m.ts,
            latency_ms=m.latency_ms,
            packet_loss_pct=m.packet_loss_pct,
            download_mbps=m.download_mbps,
            upload_mbps=m.upload_mbps,
        )
        for m in rows
    ]
