"""Dashboard admin PIN.

The dashboard sits behind one shared basic-auth password; this adds a second
gate so people with view access can't change or delete anything. Mutating admin
endpoints call `require_admin_pin` and expect the PIN in the `X-Admin-Pin`
header. Until a PIN is created everything stays open (bootstrap), so the owner
can set the first PIN from Settings right after deploying.

Mounted under /integrations/pin so it rides the existing Caddy proxy paths —
no Caddyfile change needed (and it stays behind basic-auth, unlike /agents/*
agent endpoints).
"""
import re
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.services.sync import get_or_create_account

router = APIRouter(prefix="/integrations/pin", tags=["admin-pin"])

_PIN_RE = re.compile(r"^\d{4,8}$")


async def require_admin_pin(db: AsyncSession, x_admin_pin: str | None) -> None:
    """Raise 401 unless the request carries the correct admin PIN. No-op while
    no PIN has been created yet (bootstrap mode)."""
    account = await get_or_create_account(db)
    if not account.admin_pin:
        return
    if not x_admin_pin or not secrets.compare_digest(x_admin_pin.strip(), account.admin_pin):
        raise HTTPException(status_code=401, detail="Admin PIN required.")


class PinStatusOut(BaseModel):
    set: bool


class PinSetIn(BaseModel):
    pin: str
    current: str | None = None  # required when changing an existing PIN


class PinVerifyIn(BaseModel):
    pin: str


@router.get("/status", response_model=PinStatusOut)
async def pin_status(db: AsyncSession = Depends(get_db)) -> PinStatusOut:
    account = await get_or_create_account(db)
    return PinStatusOut(set=bool(account.admin_pin))


@router.post("/verify", response_model=PinStatusOut)
async def pin_verify(body: PinVerifyIn, db: AsyncSession = Depends(get_db)) -> PinStatusOut:
    """Check a PIN (used by the unlock prompt). 401 on mismatch."""
    account = await get_or_create_account(db)
    if not account.admin_pin:
        return PinStatusOut(set=False)
    if not secrets.compare_digest(body.pin.strip(), account.admin_pin):
        raise HTTPException(status_code=401, detail="Wrong PIN.")
    return PinStatusOut(set=True)


@router.post("", response_model=PinStatusOut)
async def pin_set(body: PinSetIn, db: AsyncSession = Depends(get_db)) -> PinStatusOut:
    """Create the PIN (open while none exists) or change it (requires current)."""
    pin = (body.pin or "").strip()
    if not _PIN_RE.fullmatch(pin):
        raise HTTPException(status_code=400, detail="PIN must be 4–8 digits.")
    account = await get_or_create_account(db)
    if account.admin_pin:
        current = (body.current or "").strip()
        if not current or not secrets.compare_digest(current, account.admin_pin):
            raise HTTPException(status_code=401, detail="Current PIN is wrong.")
    account.admin_pin = pin
    await db.commit()
    return PinStatusOut(set=True)
