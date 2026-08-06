"""UniFi integrations: the account-wide Site Manager key AND per-console
Network Integration API connections. Both feed the same sites/devices tables."""
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import encrypt
from app.models import Device, Site, UnifiConsole, UnifiCredential
from app.schemas import (
    UnifiConsoleIn,
    UnifiConsoleOut,
    UnifiConsoleSyncResult,
    UnifiKeyIn,
    UnifiKeyStatus,
    UnifiSyncResult,
)
from app.api.routes.adminpin import require_admin_pin
from app.services.sync import get_or_create_account, sync_all_consoles, sync_unifi
from app.services.unifi import UnifiError, UnifiSiteManagerClient
from app.services.unifi_console import (
    UnifiConsoleClient,
    UnifiConsoleError,
    normalize_base_url,
)

router = APIRouter(prefix="/integrations/unifi", tags=["integrations"])


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
async def set_unifi_key(
    payload: UnifiKeyIn,
    db: AsyncSession = Depends(get_db),
    x_admin_pin: str | None = Header(default=None),
) -> UnifiKeyStatus:
    await require_admin_pin(db, x_admin_pin)
    # Verify the key against UniFi before persisting it.
    try:
        await UnifiSiteManagerClient(payload.api_key).verify()
    except UnifiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    account = await get_or_create_account(db)
    cred = (await db.execute(select(UnifiCredential))).scalars().first()
    if cred is None:
        cred = UnifiCredential(account_id=account.id)
        db.add(cred)
    cred.encrypted_api_key = encrypt(payload.api_key)
    cred.key_hint = "…" + payload.api_key[-4:]
    cred.label = payload.label
    await db.commit()
    return UnifiKeyStatus(configured=True, label=cred.label, key_hint=cred.key_hint)


@router.delete("/key", status_code=204)
async def delete_unifi_key(
    db: AsyncSession = Depends(get_db),
    x_admin_pin: str | None = Header(default=None),
) -> None:
    await require_admin_pin(db, x_admin_pin)
    cred = (await db.execute(select(UnifiCredential))).scalars().first()
    if cred is not None:
        await db.delete(cred)
        await db.commit()


@router.post("/sync", response_model=UnifiSyncResult)
async def unifi_sync(db: AsyncSession = Depends(get_db)) -> UnifiSyncResult:
    try:
        result = await sync_unifi(db)
    except UnifiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UnifiSyncResult(**result)


# ── UniFi console connections (Network Integration API) ──────────────────────
async def _console_out(db: AsyncSession, console: UnifiConsole) -> UnifiConsoleOut:
    n = (
        await db.execute(
            select(func.count(Site.id)).where(Site.console_id == console.id)
        )
    ).scalar_one()
    return UnifiConsoleOut(
        id=console.id,
        label=console.label,
        base_url=console.base_url,
        key_hint=console.key_hint,
        verify_tls=console.verify_tls,
        last_synced_at=console.last_synced_at,
        last_error=console.last_error,
        site_count=int(n or 0),
    )


@router.get("/consoles", response_model=list[UnifiConsoleOut])
async def list_consoles(db: AsyncSession = Depends(get_db)) -> list[UnifiConsoleOut]:
    consoles = (
        await db.execute(select(UnifiConsole).order_by(UnifiConsole.label))
    ).scalars().all()
    return [await _console_out(db, c) for c in consoles]


@router.post("/consoles", response_model=UnifiConsoleOut)
async def add_console(
    payload: UnifiConsoleIn,
    db: AsyncSession = Depends(get_db),
    x_admin_pin: str | None = Header(default=None),
) -> UnifiConsoleOut:
    await require_admin_pin(db, x_admin_pin)
    # Normalize + verify the key against the console before persisting.
    try:
        base = normalize_base_url(payload.base_url)
        await UnifiConsoleClient(base, payload.api_key, verify_tls=payload.verify_tls).verify()
    except UnifiConsoleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    account = await get_or_create_account(db)
    # Re-use an existing console with the same URL instead of duplicating.
    console = (
        await db.execute(select(UnifiConsole).where(UnifiConsole.base_url == base))
    ).scalars().first()
    if console is None:
        console = UnifiConsole(account_id=account.id, base_url=base)
        db.add(console)
    console.label = payload.label
    console.encrypted_api_key = encrypt(payload.api_key)
    console.key_hint = "…" + payload.api_key[-4:]
    console.verify_tls = payload.verify_tls
    console.last_error = None
    await db.commit()
    await db.refresh(console)
    return await _console_out(db, console)


@router.delete("/consoles/{console_id}", status_code=204)
async def delete_console(
    console_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    x_admin_pin: str | None = Header(default=None),
) -> None:
    await require_admin_pin(db, x_admin_pin)
    console = await db.get(UnifiConsole, console_id)
    if console is None:
        return
    # Remove the console's sites (and their devices) so no FK dangles.
    site_ids = (
        await db.execute(select(Site.id).where(Site.console_id == console_id))
    ).scalars().all()
    if site_ids:
        await db.execute(Device.__table__.delete().where(Device.site_id.in_(site_ids)))
        await db.execute(Site.__table__.delete().where(Site.id.in_(site_ids)))
    await db.delete(console)
    await db.commit()


@router.post("/consoles/sync", response_model=UnifiConsoleSyncResult)
async def sync_consoles(db: AsyncSession = Depends(get_db)) -> UnifiConsoleSyncResult:
    result = await sync_all_consoles(db)
    return UnifiConsoleSyncResult(**result)
