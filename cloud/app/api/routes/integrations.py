"""UniFi Site Manager integration: store the API key, check status, run a sync.

Phase 0 wires the full path end-to-end (save encrypted key -> verify -> pull
sites/devices -> upsert). Phase 1 fleshes out ISP-metric ingestion and scheduling.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import decrypt, encrypt
from app.models import Account, Device, Site, UnifiCredential
from app.schemas import UnifiKeyIn, UnifiKeyStatus, UnifiSyncResult
from app.services.unifi import UnifiError, UnifiSiteManagerClient

router = APIRouter(prefix="/integrations/unifi", tags=["integrations"])


async def _bootstrap_account(db: AsyncSession) -> Account:
    """Single-account mode: get or create the one account."""
    account = (await db.execute(select(Account))).scalars().first()
    if account is None:
        account = Account()
        db.add(account)
        await db.flush()
    return account


@router.get("/status", response_model=UnifiKeyStatus)
async def unifi_status(db: AsyncSession = Depends(get_db)) -> UnifiKeyStatus:
    cred = (await db.execute(select(UnifiCredential))).scalars().first()
    if cred is None:
        return UnifiKeyStatus(configured=False)
    return UnifiKeyStatus(
        configured=True,
        label=cred.label,
        key_hint=cred.key_hint,
        last_synced_at=cred.last_synced_at,
    )


@router.put("/key", response_model=UnifiKeyStatus)
async def set_unifi_key(payload: UnifiKeyIn, db: AsyncSession = Depends(get_db)) -> UnifiKeyStatus:
    # Verify the key before persisting it.
    client = UnifiSiteManagerClient(payload.api_key)
    try:
        await client.verify()
    except UnifiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    account = await _bootstrap_account(db)
    cred = (await db.execute(select(UnifiCredential))).scalars().first()
    if cred is None:
        cred = UnifiCredential(account_id=account.id)
        db.add(cred)
    cred.encrypted_api_key = encrypt(payload.api_key)
    cred.key_hint = "…" + payload.api_key[-4:]
    cred.label = payload.label
    await db.commit()
    return UnifiKeyStatus(configured=True, label=cred.label, key_hint=cred.key_hint)


@router.post("/sync", response_model=UnifiSyncResult)
async def unifi_sync(db: AsyncSession = Depends(get_db)) -> UnifiSyncResult:
    cred = (await db.execute(select(UnifiCredential))).scalars().first()
    if cred is None:
        raise HTTPException(status_code=400, detail="No UniFi API key configured")

    api_key = decrypt(cred.encrypted_api_key)
    client = UnifiSiteManagerClient(api_key)
    account = await _bootstrap_account(db)

    try:
        raw_sites = await client.list_sites()
        raw_devices = await client.list_devices()
    except UnifiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Upsert sites keyed by UniFi site id.
    existing_sites = {
        s.unifi_site_id: s
        for s in (await db.execute(select(Site))).scalars()
        if s.unifi_site_id
    }
    site_by_unifi_id: dict[str, Site] = {}
    for rs in raw_sites:
        uid = str(rs.get("siteId") or rs.get("id") or "")
        if not uid:
            continue
        site = existing_sites.get(uid) or Site(account_id=account.id, unifi_site_id=uid)
        site.name = rs.get("name") or rs.get("desc") or site.name or uid
        site.unifi_host_id = str(rs.get("hostId") or rs.get("host_id") or "") or site.unifi_host_id
        site.isp_name = rs.get("ispName") or site.isp_name
        db.add(site)
        site_by_unifi_id[uid] = site
    await db.flush()

    # Upsert devices keyed by UniFi device id; identity is id/MAC, never IP.
    existing_devices = {
        d.unifi_device_id: d
        for d in (await db.execute(select(Device))).scalars()
        if d.unifi_device_id
    }
    device_count = 0
    for rd in raw_devices:
        did = str(rd.get("id") or rd.get("deviceId") or rd.get("mac") or "")
        if not did:
            continue
        site_uid = str(rd.get("siteId") or rd.get("site_id") or "")
        site = site_by_unifi_id.get(site_uid)
        if site is None:
            continue
        dev = existing_devices.get(did) or Device(unifi_device_id=did)
        dev.site_id = site.id
        dev.name = rd.get("name") or dev.name or did
        dev.model = rd.get("model") or dev.model
        dev.mac = rd.get("mac") or dev.mac
        dev.ip = rd.get("ip") or rd.get("lanIp") or dev.ip  # current lease
        dev.device_type = rd.get("type") or rd.get("productType") or dev.device_type
        status = rd.get("status") or rd.get("state")
        dev.is_online = (status == "online") if status is not None else dev.is_online
        db.add(dev)
        device_count += 1

    await db.commit()
    return UnifiSyncResult(sites=len(site_by_unifi_id), devices=device_count, metrics=0)
