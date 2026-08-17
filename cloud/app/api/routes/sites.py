"""Read endpoints for sites, their devices, and WAN metric history."""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.auth import current_user, require_admin
from app.core.db import get_db
from app.models import Device, IspMetric, Site
from app.schemas import DeviceOut, DormantDeviceOut, MetricPoint, SiteOut, WanMetricSeries
from app.services.sync import PRIMARY_WAN_LABELS, is_primary_wan

router = APIRouter(prefix="/sites", tags=["sites"])


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _dormant_cutoff() -> datetime:
    """A device whose offline_since is at or before this is dormant."""
    return _now() - timedelta(days=get_settings().dormant_after_days)


def _dormant_sql(cutoff: datetime):
    """SQL predicate for effective dormancy: manually parked OR auto-aged out."""
    return or_(
        Device.manual_dormant.is_(True),
        and_(Device.offline_since.is_not(None), Device.offline_since <= cutoff),
    )


async def _latest_metrics_by_site(db: AsyncSession) -> dict:
    """Most-recent PRIMARY-WAN IspMetric per site — the number shown on the site
    card. Now that secondary WANs are stored too, prefer a primary-WAN row and
    only fall back to any WAN if a site has no primary reading."""
    rows = list(
        (await db.execute(select(IspMetric).order_by(IspMetric.ts.desc()).limit(4000))).scalars()
    )
    latest: dict = {}
    fallback: dict = {}
    for m in rows:
        fallback.setdefault(m.site_id, m)
        if is_primary_wan(m.wan) and m.site_id not in latest:
            latest[m.site_id] = m
    for sid, m in fallback.items():
        latest.setdefault(sid, m)
    return latest


def _build_site_out(site: Site, n_total: int, n_online: int, n_dormant: int, m) -> SiteOut:
    # UniFi's own per-site counts are authoritative (they cover every adopted
    # device, not just the ones the sync attached). Fall back to joined rows.
    total = site.device_total or n_total
    online = (
        (site.device_total - site.device_offline) if site.device_total else n_online
    )
    return SiteOut(
        id=site.id,
        name=site.name,
        isp_name=site.isp_name,
        status=site.status,
        device_count=total,
        online_device_count=online,
        dormant_device_count=int(n_dormant or 0),
        latency_ms=m.latency_ms if m else None,
        packet_loss_pct=m.packet_loss_pct if m else None,
        # Prefer the live metric uptime; fall back to the site's reported WAN uptime.
        uptime_pct=(m.uptime_pct if m and m.uptime_pct is not None else site.wan_uptime_pct),
        download_mbps=m.download_mbps if m else None,
        upload_mbps=m.upload_mbps if m else None,
        map_x=site.map_x,
        map_y=site.map_y,
        teardown_active=bool(site.teardown_active),
        teardown_scheduled_at=site.teardown_scheduled_at,
        teardown_since=site.teardown_since,
        teardown_auto_off_at=site.teardown_auto_off_at,
        keep_monitored=bool(site.keep_monitored),
    )


def _device_out(dev: Device, now: datetime, cutoff: datetime) -> DeviceOut:
    down_seconds = (
        int((now - dev.offline_since).total_seconds()) if dev.offline_since else None
    )
    dormant = dev.manual_dormant or (
        dev.offline_since is not None and dev.offline_since <= cutoff
    )
    return DeviceOut(
        id=dev.id,
        name=dev.name,
        model=dev.model,
        device_type=dev.device_type,
        ip=dev.ip,
        mac=dev.mac,
        is_online=dev.is_online,
        offline_since=dev.offline_since,
        last_online_at=dev.last_online_at,
        down_seconds=down_seconds,
        dormant=dormant,
        manual_dormant=dev.manual_dormant,
        keep_monitored=bool(dev.keep_monitored),
        local_reachable=dev.local_reachable,
        local_rtt_ms=dev.local_rtt_ms,
        local_checked_at=dev.local_checked_at,
    )


@router.get("", response_model=list[SiteOut])
async def list_sites(db: AsyncSession = Depends(get_db), _user=Depends(current_user)) -> list[SiteOut]:
    cutoff = _dormant_cutoff()
    total = func.count(Device.id)
    online = func.count(Device.id).filter(Device.is_online.is_(True))
    dormant = func.count(Device.id).filter(_dormant_sql(cutoff))
    stmt = (
        select(Site, total, online, dormant)
        .outerjoin(Device, Device.site_id == Site.id)
        .group_by(Site.id)
        .order_by(Site.name)
    )
    rows = (await db.execute(stmt)).all()
    latest = await _latest_metrics_by_site(db)
    return [
        _build_site_out(site, n_total, n_online, n_dormant, latest.get(site.id))
        for site, n_total, n_online, n_dormant in rows
    ]


@router.get("/dormant-devices", response_model=list[DormantDeviceOut])
async def dormant_devices(db: AsyncSession = Depends(get_db), _user=Depends(current_user)) -> list[DormantDeviceOut]:
    """Fleet-wide list of dormant devices (offline past the threshold), each
    carrying its site — this powers the Dormant tab. Longest-dead first."""
    now = _now()
    cutoff = _dormant_cutoff()
    stmt = (
        select(Device, Site.name)
        .join(Site, Site.id == Device.site_id)
        .where(_dormant_sql(cutoff))
        .order_by(Device.offline_since.asc().nulls_last(), Device.name)
    )
    rows = (await db.execute(stmt)).all()
    return [
        DormantDeviceOut(
            id=d.id,
            name=d.name,
            model=d.model,
            device_type=d.device_type,
            ip=d.ip,
            mac=d.mac,
            site_id=d.site_id,
            site_name=site_name,
            offline_since=d.offline_since,
            down_seconds=int((now - d.offline_since).total_seconds()) if d.offline_since else None,
            is_online=d.is_online,
            manual_dormant=d.manual_dormant,
        )
        for d, site_name in rows
    ]


class SetDormantIn(BaseModel):
    dormant: bool


@router.post("/{site_id}/devices/{device_id}/dormant", response_model=DeviceOut)
async def set_device_dormant(
    site_id: uuid.UUID,
    device_id: uuid.UUID,
    body: SetDormantIn,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
) -> DeviceOut:
    """Manually park a device in Dormant (or bring it back). Parking overrides
    the age rule; restoring only clears the manual flag — a device offline past
    the threshold stays dormant until it reports in again."""
    dev = await db.get(Device, device_id)
    if dev is None or dev.site_id != site_id:
        raise HTTPException(status_code=404, detail="Device not found")
    dev.manual_dormant = body.dormant
    await db.commit()
    await db.refresh(dev)
    return _device_out(dev, _now(), _dormant_cutoff())


class SetKeepMonitoredIn(BaseModel):
    keep: bool


class SiteTeardownIn(BaseModel):
    enabled: bool
    hours: float | None = 18.0   # safety auto-off window


class SiteTeardownScheduleIn(BaseModel):
    at: datetime | None = None    # one-off scheduled teardown start; null cancels
    hours: float | None = 18.0    # auto-off window after it fires


async def _site_out_for(db: AsyncSession, site: Site) -> SiteOut:
    cutoff = _dormant_cutoff()
    total = func.count(Device.id)
    online = func.count(Device.id).filter(Device.is_online.is_(True))
    dormant = func.count(Device.id).filter(_dormant_sql(cutoff))
    row = (await db.execute(
        select(total, online, dormant).where(Device.site_id == site.id)
    )).one()
    latest = await _latest_metrics_by_site(db)
    return _build_site_out(site, row[0], row[1], row[2], latest.get(site.id))


@router.post("/{site_id}/keep-monitored", response_model=SiteOut)
async def set_site_keep_monitored(
    site_id: uuid.UUID, body: SetKeepMonitoredIn,
    db: AsyncSession = Depends(get_db), _admin=Depends(require_admin),
) -> SiteOut:
    """Flag a site critical (Safety, Main office …): it keeps alerting through any
    teardown, monitored via the UniFi API. Flagging it also clears any teardown."""
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")
    site.keep_monitored = body.keep
    if body.keep:
        site.teardown_active = False
        site.teardown_scheduled_at = None
        site.teardown_since = site.teardown_auto_off_at = None
    await db.commit()
    await db.refresh(site)
    return await _site_out_for(db, site)


@router.post("/{site_id}/teardown", response_model=SiteOut)
async def set_site_teardown(
    site_id: uuid.UUID, body: SiteTeardownIn,
    db: AsyncSession = Depends(get_db), _admin=Depends(require_admin),
) -> SiteOut:
    """Turn a site's teardown on/off right now (manual)."""
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")
    now = _now()
    if body.enabled:
        site.teardown_active = True
        site.teardown_since = now
        site.teardown_auto_off_at = (
            now + timedelta(hours=body.hours) if body.hours and body.hours > 0 else None
        )
        site.teardown_scheduled_at = None
    else:
        site.teardown_active = False
        site.teardown_since = site.teardown_auto_off_at = None
    await db.commit()
    await db.refresh(site)
    return await _site_out_for(db, site)


@router.post("/{site_id}/teardown/schedule", response_model=SiteOut)
async def schedule_site_teardown(
    site_id: uuid.UUID, body: SiteTeardownScheduleIn,
    db: AsyncSession = Depends(get_db), _admin=Depends(require_admin),
) -> SiteOut:
    """Arm a one-off teardown for a site at a future time (or cancel with at=null).
    The auto-off window is computed from `hours` when it fires."""
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")
    if site.keep_monitored and body.at is not None:
        raise HTTPException(status_code=422, detail="Site is keep-monitored (critical) — unflag it first")
    site.teardown_scheduled_at = body.at
    site.teardown_auto_off_at = (
        body.at + timedelta(hours=body.hours) if body.at and body.hours and body.hours > 0 else None
    )
    await db.commit()
    await db.refresh(site)
    return await _site_out_for(db, site)


@router.post("/{site_id}/devices/{device_id}/keep-monitored", response_model=DeviceOut)
async def set_device_keep_monitored(
    site_id: uuid.UUID, device_id: uuid.UUID, body: SetKeepMonitoredIn,
    db: AsyncSession = Depends(get_db), _admin=Depends(require_admin),
) -> DeviceOut:
    """Flag a device critical — it keeps alerting through teardown (UniFi status)."""
    dev = await db.get(Device, device_id)
    if dev is None or dev.site_id != site_id:
        raise HTTPException(status_code=404, detail="Device not found")
    dev.keep_monitored = body.keep
    await db.commit()
    await db.refresh(dev)
    return _device_out(dev, _now(), _dormant_cutoff())


@router.get("/{site_id}", response_model=SiteOut)
async def get_site(site_id: uuid.UUID, db: AsyncSession = Depends(get_db), _user=Depends(current_user)) -> SiteOut:
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")
    cutoff = _dormant_cutoff()
    total = func.count(Device.id)
    online = func.count(Device.id).filter(Device.is_online.is_(True))
    dormant = func.count(Device.id).filter(_dormant_sql(cutoff))
    row = (
        await db.execute(
            select(total, online, dormant).where(Device.site_id == site_id)
        )
    ).one()
    latest = await _latest_metrics_by_site(db)
    return _build_site_out(site, row[0], row[1], row[2], latest.get(site.id))


@router.get("/{site_id}/devices", response_model=list[DeviceOut])
async def list_site_devices(
    site_id: uuid.UUID,
    status: str = Query("active", pattern="^(active|dormant|offline|online|all)$"),
    db: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
) -> list[DeviceOut]:
    """Devices for a site. `status` filters server-side:
    active (default, excludes dormant), dormant, offline (active offline only),
    online, or all."""
    if await db.get(Site, site_id) is None:
        raise HTTPException(status_code=404, detail="Site not found")
    now = _now()
    cutoff = _dormant_cutoff()
    stmt = select(Device).where(Device.site_id == site_id).order_by(Device.name)
    outs = [_device_out(d, now, cutoff) for d in (await db.execute(stmt)).scalars()]
    if status == "active":
        outs = [o for o in outs if not o.dormant]
    elif status == "dormant":
        outs = [o for o in outs if o.dormant]
    elif status == "offline":
        outs = [o for o in outs if o.is_online is False and not o.dormant]
    elif status == "online":
        outs = [o for o in outs if o.is_online is True]
    return outs


def _metric_point(m: IspMetric) -> MetricPoint:
    return MetricPoint(
        ts=m.ts,
        latency_ms=m.latency_ms,
        packet_loss_pct=m.packet_loss_pct,
        download_mbps=m.download_mbps,
        upload_mbps=m.upload_mbps,
    )


@router.get("/{site_id}/metrics", response_model=list[MetricPoint])
async def site_metrics(
    site_id: uuid.UUID,
    limit: int = Query(200, ge=1, le=2000),
    wan: str | None = Query(None, description="WAN key; default = primary uplink only"),
    db: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
) -> list[MetricPoint]:
    if await db.get(Site, site_id) is None:
        raise HTTPException(status_code=404, detail="Site not found")
    stmt = select(IspMetric).where(IspMetric.site_id == site_id)
    if wan:
        stmt = stmt.where(func.lower(IspMetric.wan) == wan.strip().lower())
    else:
        # Default: the primary uplink only, so the classic single-line chart
        # isn't polluted by secondary-WAN rows now that we store both.
        stmt = stmt.where(func.lower(IspMetric.wan).in_(PRIMARY_WAN_LABELS))
    stmt = stmt.order_by(IspMetric.ts.desc()).limit(limit)
    rows = list((await db.execute(stmt)).scalars())
    rows.reverse()  # chronological for charting
    return [_metric_point(m) for m in rows]


@router.get("/{site_id}/wan-metrics", response_model=list[WanMetricSeries])
async def site_wan_metrics(
    site_id: uuid.UUID,
    limit: int = Query(500, ge=1, le=4000),
    db: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
) -> list[WanMetricSeries]:
    """Per-WAN latency/loss/throughput history for a site — one series per uplink
    (primary + any secondary). Powers the dual-WAN panel and shows failover."""
    if await db.get(Site, site_id) is None:
        raise HTTPException(status_code=404, detail="Site not found")
    stmt = (
        select(IspMetric)
        .where(IspMetric.site_id == site_id)
        .order_by(IspMetric.ts.desc())
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars())
    rows.reverse()  # chronological
    by_wan: dict[str, list[MetricPoint]] = {}
    for m in rows:
        by_wan.setdefault(m.wan or "primary", []).append(_metric_point(m))
    # Primary uplink(s) first, then the rest alphabetically.
    keys = sorted(by_wan, key=lambda w: (not is_primary_wan(w), w.lower()))
    return [
        WanMetricSeries(
            wan=k,
            label=_wan_label(k),
            primary=is_primary_wan(k),
            points=by_wan[k],
        )
        for k in keys
    ]


def _wan_label(wan: str) -> str:
    w = (wan or "").strip().lower()
    if w in ("wan", "wan1", "primary"):
        return "Primary (WAN1)"
    if w in ("wan2",):
        return "Secondary (WAN2)"
    if w.startswith("wan") and w[3:].isdigit():
        return f"WAN{w[3:]}"
    return wan or "WAN"
