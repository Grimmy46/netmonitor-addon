"""UniFi fleet sync: pull sites, devices, and ISP metrics; upsert into the DB.

Shared by the manual /integrations/unifi/sync endpoint and the background poller.
Identity is always UniFi's own id/MAC — never the IP — so DHCP churn is a non-issue.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt
from app.models import Account, Device, IspMetric, Site, UnifiCredential
from app.services.unifi import UnifiError, UnifiSiteManagerClient


async def get_or_create_account(db: AsyncSession) -> Account:
    account = (await db.execute(select(Account))).scalars().first()
    if account is None:
        account = Account()
        db.add(account)
        await db.flush()
    return account


def _device_type(model: str | None, product_line: str | None) -> str | None:
    m = (model or "").upper()
    if m.startswith(("USW", "US-")):
        return "switch"
    if m.startswith(("U6", "U7", "UAP", "UWB", "UAP-")):
        return "ap"
    if m.startswith(("UXG", "UDM", "UCG", "UGW", "USG", "UDR")):
        return "gateway"
    return product_line or None


def _parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Epoch millis or seconds.
        secs = value / 1000 if value > 1e12 else value
        return datetime.fromtimestamp(secs, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _kbps_to_mbps(v) -> float | None:
    try:
        return round(float(v) / 1000.0, 2)
    except (TypeError, ValueError):
        return None


async def sync_unifi(db: AsyncSession) -> dict:
    cred = (await db.execute(select(UnifiCredential))).scalars().first()
    if cred is None:
        raise UnifiError("No UniFi API key configured")

    client = UnifiSiteManagerClient(decrypt(cred.encrypted_api_key))
    account = await get_or_create_account(db)

    raw_sites = await client.list_sites()
    raw_devices = await client.list_devices()

    # ── sites ────────────────────────────────────────────────────────────────
    existing_sites = {
        s.unifi_site_id: s
        for s in (await db.execute(select(Site))).scalars()
        if s.unifi_site_id
    }
    site_by_uid: dict[str, Site] = {}
    for rs in raw_sites:
        uid = str(rs.get("siteId") or rs.get("id") or "")
        if not uid:
            continue
        meta = rs.get("meta") or {}
        stats = (rs.get("statistics") or rs.get("siteStatistics") or {})
        counts = stats.get("counts") or {}
        site = existing_sites.get(uid) or Site(account_id=account.id, unifi_site_id=uid)
        site.name = meta.get("name") or meta.get("desc") or rs.get("name") or site.name or uid
        site.unifi_host_id = str(rs.get("hostId") or "") or site.unifi_host_id
        site.isp_name = meta.get("ispName") or site.isp_name
        total = counts.get("totalDevice")
        offline = counts.get("offlineDevice")
        if total is not None and offline is not None:
            site.status = "online" if offline == 0 else ("offline" if offline >= total else "degraded")
        db.add(site)
        site_by_uid[uid] = site
    await db.flush()

    # ── devices ──────────────────────────────────────────────────────────────
    existing_devices = {
        d.unifi_device_id: d
        for d in (await db.execute(select(Device))).scalars()
        if d.unifi_device_id
    }
    device_count = 0
    for rd in raw_devices:
        did = str(rd.get("id") or rd.get("mac") or "")
        if not did:
            continue
        site = site_by_uid.get(str(rd.get("siteId") or ""))
        if site is None:
            continue
        dev = existing_devices.get(did) or Device(unifi_device_id=did)
        dev.site_id = site.id
        dev.name = rd.get("name") or dev.name or did
        dev.model = rd.get("model") or dev.model
        dev.mac = rd.get("mac") or dev.mac
        dev.ip = rd.get("ip") or dev.ip  # current lease, refreshed each sync
        dev.device_type = _device_type(rd.get("model"), rd.get("productLine")) or dev.device_type
        status = rd.get("status") or rd.get("state")
        dev.is_online = (str(status).lower() == "online") if status is not None else dev.is_online
        db.add(dev)
        device_count += 1

    # ── ISP metrics (best-effort; don't fail the whole sync if unavailable) ──
    metric_count = 0
    try:
        raw_metrics = await client.get_isp_metrics("1h")
        metric_count = await _ingest_isp_metrics(db, raw_metrics, site_by_uid)
    except UnifiError:
        pass

    cred.last_synced_at = datetime.now(tz=timezone.utc).isoformat()
    await db.commit()
    return {"sites": len(site_by_uid), "devices": device_count, "metrics": metric_count}


async def _ingest_isp_metrics(db: AsyncSession, raw_metrics: list[dict], site_by_uid: dict) -> int:
    """Each entry is roughly {siteId|hostId, periods:[{metricTime, data:{...}}]}.
    We insert one IspMetric row per period newer than what we already stored."""
    inserted = 0
    for entry in raw_metrics:
        uid = str(entry.get("siteId") or entry.get("hostId") or "")
        site = site_by_uid.get(uid)
        if site is None:
            continue
        # Latest stored ts for this site, to avoid duplicate rows.
        last = (
            await db.execute(
                select(IspMetric.ts)
                .where(IspMetric.site_id == site.id)
                .order_by(IspMetric.ts.desc())
                .limit(1)
            )
        ).scalars().first()

        for period in entry.get("periods") or []:
            ts = _parse_ts(period.get("metricTime") or period.get("time"))
            if ts is None or (last is not None and ts <= last):
                continue
            d = period.get("data") or period  # fields may be flat or nested
            db.add(
                IspMetric(
                    site_id=site.id,
                    ts=ts,
                    wan=str(d.get("wan") or "primary"),
                    latency_ms=_num(d.get("avgLatency")),
                    packet_loss_pct=_num(d.get("packetLoss")),
                    download_mbps=_kbps_to_mbps(d.get("download_kbps")),
                    upload_mbps=_kbps_to_mbps(d.get("upload_kbps")),
                    uptime_pct=_num(d.get("uptime")),
                )
            )
            inserted += 1
    return inserted


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
