"""UniFi fleet sync: pull sites, devices, and ISP metrics; upsert into the DB.

Shared by the manual /integrations/unifi/sync endpoint and the background poller.
Identity is always UniFi's own id/MAC — never the IP — so DHCP churn is a non-issue.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt
from app.models import Account, Device, IspMetric, Site, UnifiConsole, UnifiCredential
from app.services.unifi import UnifiError, UnifiSiteManagerClient
from app.services.unifi_console import UnifiConsoleClient, UnifiConsoleError


async def get_or_create_account(db: AsyncSession) -> Account:
    account = (await db.execute(select(Account))).scalars().first()
    if account is None:
        account = Account()
        db.add(account)
        await db.flush()
    return account


def _device_type(model: str | None, product_line: str | None) -> str | None:
    """Classify by model. The Site Manager API gives terse codes (USW-…, U6-…)
    while the console Network API gives friendly names ("AC Mesh", "USW Pro
    Max 24 PoE", "U7 Pro Outdoor"), so match both prefixes AND keywords."""
    m = (model or "").upper()
    if not m:
        return product_line or None
    # Switches first (USW / US- codes, or "SWITCH" in a friendly name).
    if m.startswith(("USW", "US-")) or "SWITCH" in m:
        return "switch"
    # Gateways / routers / NVRs.
    if m.startswith(("UXG", "UDM", "UCG", "UGW", "USG", "UDR", "UNVR")) or "GATEWAY" in m:
        return "gateway"
    # Access points: U6/U7/UAP codes, or "AP"/"MESH"/"ACCESS" in a friendly name.
    if (
        m.startswith(("U6", "U7", "UAP", "UWB"))
        or "MESH" in m
        or "ACCESS POINT" in m
        or m.endswith(" AP")
        or " AP " in m
    ):
        return "ap"
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


def _norm_mac(v) -> str:
    """Normalize a MAC to 12 uppercase hex chars (strip ':', '-', spaces)."""
    return "".join(c for c in str(v or "").upper() if c in "0123456789ABCDEF")


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
    site_by_gwmac: dict[str, Site] = {}
    for rs in raw_sites:
        uid = str(rs.get("siteId") or rs.get("id") or "")
        if not uid:
            continue
        meta = rs.get("meta") or {}
        stats = (rs.get("statistics") or rs.get("siteStatistics") or {})
        counts = stats.get("counts") or {}
        site = existing_sites.get(uid) or Site(account_id=account.id, unifi_site_id=uid)
        # `meta.desc` is the human name ("Main"); `meta.name` is only a slug
        # ("default"). Prefer desc, fall back to the slug, then host name.
        site.name = (
            meta.get("desc") or meta.get("name") or rs.get("name") or site.name or uid
        )
        site.unifi_host_id = str(rs.get("hostId") or "") or site.unifi_host_id
        site.gateway_mac = _norm_mac(meta.get("gatewayMac")) or site.gateway_mac
        # ISP name lives under statistics.ispInfo.name on the Site Manager API.
        isp = (stats.get("ispInfo") or {}).get("name")
        site.isp_name = isp or meta.get("ispName") or site.isp_name
        # WAN uptime % is reported directly per site.
        uptime = (stats.get("percentages") or {}).get("wanUptime")
        site.wan_uptime_pct = _num(uptime) if uptime is not None else site.wan_uptime_pct
        total = counts.get("totalDevice")
        offline = counts.get("offlineDevice")
        if total is not None:
            site.device_total = int(total)
        if offline is not None:
            site.device_offline = int(offline)
        if total is not None and offline is not None:
            site.status = "online" if offline == 0 else ("offline" if offline >= total else "degraded")
        db.add(site)
        site_by_uid[uid] = site
        if site.gateway_mac:
            site_by_gwmac[site.gateway_mac] = site
    await db.flush()

    # ── devices ──────────────────────────────────────────────────────────────
    existing_devices = {
        d.unifi_device_id: d
        for d in (await db.execute(select(Device))).scalars()
        if d.unifi_device_id
    }
    # Which host each site lives on. A host with exactly one site owns all of
    # that host's devices; a host shared by many sites (a multi-site controller)
    # does NOT tag devices per site, so only the gateways there are placeable.
    host_sites: dict[str, list[Site]] = {}
    for s in site_by_uid.values():
        if s.unifi_host_id:
            host_sites.setdefault(s.unifi_host_id, []).append(s)

    device_count = 0
    for rd in raw_devices:
        did = str(rd.get("id") or rd.get("mac") or "")
        if not did:
            continue
        # Devices are grouped by host and carry NO siteId. Attribute each one:
        #   1. its MAC equals a site's gatewayMac  -> it's that site's gateway
        #   2. its host maps to exactly one site   -> single-site host, all its
        #      devices belong to that site
        # Devices on a shared multi-site controller (neither case) can't be
        # placed per-site from the cloud API and are skipped — the site cards
        # still show authoritative counts from UniFi's own statistics.
        dev_mac = _norm_mac(rd.get("mac"))
        site = site_by_gwmac.get(dev_mac)
        if site is None:
            hs = host_sites.get(str(rd.get("hostId") or ""))
            if hs and len(hs) == 1:
                site = hs[0]
        if site is None:
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


def _online_from_state(state) -> bool | None:
    """UniFi console `state` is a string like ONLINE / OFFLINE / PENDING_ADOPTION.
    Only 'ONLINE' counts as up; empty/unknown maps to None (leave untouched)."""
    s = str(state or "").strip().upper()
    if not s:
        return None
    return s == "ONLINE"


async def sync_unifi_console(db: AsyncSession, console: UnifiConsole) -> dict:
    """Pull every site + its devices from ONE console's Network Integration API,
    and upsert them. Device up/down comes straight from each device's `state`,
    and per-site counts are derived from the devices actually returned (the
    Network API doesn't ship the Site-Manager-style statistics block)."""
    client = UnifiConsoleClient(
        console.base_url,
        decrypt(console.encrypted_api_key),
        verify_tls=console.verify_tls,
    )
    account = await get_or_create_account(db)

    raw_sites = await client.list_sites()

    # Sites already known for THIS console, keyed by the console's own site id.
    existing_sites = {
        s.unifi_site_id: s
        for s in (
            await db.execute(select(Site).where(Site.console_id == console.id))
        ).scalars()
        if s.unifi_site_id
    }

    n_sites = 0
    total_devices = 0
    for rs in raw_sites:
        sid = str(rs.get("id") or rs.get("_id") or rs.get("siteId") or "")
        if not sid:
            continue
        # Network Integration API reports the human name under `name`
        # (and sometimes `desc`/`meta.desc`).
        meta = rs.get("meta") or {}
        name = rs.get("name") or rs.get("desc") or meta.get("desc") or sid
        site = existing_sites.get(sid) or Site(
            account_id=account.id, console_id=console.id, unifi_site_id=sid
        )
        site.account_id = account.id
        site.console_id = console.id
        site.name = name
        db.add(site)
        await db.flush()  # ensure site.id for device FKs

        raw_devices = await client.list_devices(sid)
        existing_devices = {
            d.unifi_device_id: d
            for d in (
                await db.execute(select(Device).where(Device.site_id == site.id))
            ).scalars()
            if d.unifi_device_id
        }

        online = 0
        for rd in raw_devices:
            did = str(rd.get("id") or rd.get("macAddress") or rd.get("mac") or "")
            if not did:
                continue
            dev = existing_devices.get(did) or Device(unifi_device_id=did)
            dev.site_id = site.id
            dev.name = rd.get("name") or dev.name or did
            dev.model = rd.get("model") or dev.model
            dev.mac = rd.get("macAddress") or rd.get("mac") or dev.mac
            dev.ip = rd.get("ipAddress") or rd.get("ip") or dev.ip
            dev.device_type = _device_type(rd.get("model"), None) or dev.device_type
            is_online = _online_from_state(rd.get("state"))
            if is_online is not None:
                dev.is_online = is_online
            if dev.is_online:
                online += 1
            db.add(dev)
            total_devices += 1

        count = len(raw_devices)
        site.device_total = count
        site.device_offline = max(0, count - online)
        if count == 0:
            site.status = "unknown"
        elif site.device_offline == 0:
            site.status = "online"
        elif online == 0:
            site.status = "offline"
        else:
            site.status = "degraded"
        n_sites += 1

    console.last_synced_at = datetime.now(tz=timezone.utc).isoformat()
    console.last_error = None
    await db.commit()
    return {"sites": n_sites, "devices": total_devices, "metrics": 0}


async def sync_all_consoles(db: AsyncSession) -> dict:
    """Sync every connected console. One console failing doesn't stop the rest;
    its error is recorded on the console row for the UI to surface."""
    consoles = (await db.execute(select(UnifiConsole))).scalars().all()
    agg = {"consoles": 0, "sites": 0, "devices": 0, "errors": []}
    for console in consoles:
        try:
            r = await sync_unifi_console(db, console)
            agg["consoles"] += 1
            agg["sites"] += r["sites"]
            agg["devices"] += r["devices"]
        except (UnifiConsoleError, Exception) as exc:  # noqa: BLE001 — isolate failures
            await db.rollback()
            console.last_error = str(exc)[:500]
            await db.commit()
            agg["errors"].append({"console": console.label, "error": str(exc)})
    return agg


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
            # Real shape: period.data.<wan-interface>.{avgLatency, packetLoss, ...}
            # The metric fields live one level under a WAN key (e.g. "wan",
            # "wan2"), NOT directly under data. Pick the primary WAN, falling
            # back to a flat layout if a future response ever provides one.
            body = period.get("data") or period
            wan_key, d = _primary_wan(body)
            db.add(
                IspMetric(
                    site_id=site.id,
                    ts=ts,
                    wan=wan_key,
                    latency_ms=_num(d.get("avgLatency")),
                    packet_loss_pct=_num(d.get("packetLoss")),
                    download_mbps=_kbps_to_mbps(d.get("download_kbps")),
                    upload_mbps=_kbps_to_mbps(d.get("upload_kbps")),
                    uptime_pct=_num(d.get("uptime")),
                )
            )
            inserted += 1
    return inserted


def _primary_wan(body: dict) -> tuple[str, dict]:
    """UniFi nests ISP metrics under a WAN-interface key inside `data`
    (e.g. {"wan": {...}, "wan2": {...}}). Return (label, metrics) for the
    primary WAN. If the metrics look flat (already have avgLatency), use them
    as-is so we stay resilient to response-shape changes."""
    if not isinstance(body, dict):
        return "primary", {}
    if "avgLatency" in body or "packetLoss" in body:
        return "primary", body
    wan_dicts = {k: v for k, v in body.items() if isinstance(v, dict)}
    if not wan_dicts:
        return "primary", {}
    # Prefer a key literally named "wan"; otherwise take the first.
    for pref in ("wan", "wan1", "WAN"):
        if pref in wan_dicts:
            return pref, wan_dicts[pref]
    k = next(iter(wan_dicts))
    return k, wan_dicts[k]


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
