"""UniFi Site Manager integration: store the API key, check status, run a sync."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import encrypt
from app.models import UnifiCredential
from app.schemas import UnifiKeyIn, UnifiKeyStatus, UnifiSyncResult
from app.services.sync import get_or_create_account, sync_unifi
from app.services.unifi import UnifiError, UnifiSiteManagerClient

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
async def set_unifi_key(payload: UnifiKeyIn, db: AsyncSession = Depends(get_db)) -> UnifiKeyStatus:
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
async def delete_unifi_key(db: AsyncSession = Depends(get_db)) -> None:
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
