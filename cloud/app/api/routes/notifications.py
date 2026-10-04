"""Web-push notification endpoints: VAPID key handout, subscription
management, and a self-test push.

Any signed-in user (admin or viewer) may enable alerts on their own devices —
subscriptions are per-user, so removing a user cascades their subscriptions
away. The alert sweep (workers/alerts.py) fans out to every subscription.
"""
import asyncio

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_user
from app.core.db import SessionLocal, get_db
from app.models import NotificationLog, PushSubscription
from app.models.user import User
from app.services.notify import get_vapid_keys, send_push

router = APIRouter(prefix="/notifications", tags=["notifications"])


class VapidOut(BaseModel):
    public_key: str


class SubKeys(BaseModel):
    p256dh: str
    auth: str


class SubscribeIn(BaseModel):
    endpoint: str
    keys: SubKeys


class UnsubscribeIn(BaseModel):
    endpoint: str


class StatusOut(BaseModel):
    subscription_count: int  # across all users — "is anyone listening"
    mine: int
    # Set when the caller passes ?endpoint=…: is THIS browser's subscription
    # actually saved server-side? (A phone can hold a local subscription the
    # server never received — the UI must not claim it's on.)
    this_device: bool = False


@router.get("/vapid", response_model=VapidOut)
async def vapid_public_key(
    db: AsyncSession = Depends(get_db), _user: User = Depends(current_user)
) -> VapidOut:
    public, _ = await get_vapid_keys(db)
    return VapidOut(public_key=public)


@router.get("/status", response_model=StatusOut)
async def status(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
    endpoint: str | None = None,
) -> StatusOut:
    total = (await db.execute(select(func.count(PushSubscription.id)))).scalar() or 0
    mine = (
        await db.execute(
            select(func.count(PushSubscription.id)).where(
                PushSubscription.user_id == user.id
            )
        )
    ).scalar() or 0
    this_device = False
    if endpoint:
        this_device = (
            await db.execute(
                select(PushSubscription.id).where(PushSubscription.endpoint == endpoint)
            )
        ).first() is not None
    return StatusOut(subscription_count=int(total), mine=int(mine), this_device=this_device)


@router.post("/subscribe", response_model=StatusOut)
async def subscribe(
    body: SubscribeIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
    user_agent: str | None = Header(default=None),
) -> StatusOut:
    existing = (
        await db.execute(
            select(PushSubscription).where(PushSubscription.endpoint == body.endpoint)
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            PushSubscription(
                user_id=user.id,
                endpoint=body.endpoint,
                p256dh=body.keys.p256dh,
                auth=body.keys.auth,
                ua=user_agent,
            )
        )
    else:  # browser re-subscribed (keys can rotate) — refresh in place
        existing.user_id = user.id
        existing.p256dh = body.keys.p256dh
        existing.auth = body.keys.auth
        existing.ua = user_agent
        existing.failures = 0
    await db.commit()
    return await status(db, user)


@router.post("/unsubscribe", response_model=StatusOut)
async def unsubscribe(
    body: UnsubscribeIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> StatusOut:
    await db.execute(
        delete(PushSubscription).where(PushSubscription.endpoint == body.endpoint)
    )
    await db.commit()
    return await status(db, user)


_pending_tests: set = set()   # keep task refs so delayed tests aren't GC'd


@router.post("/test")
async def test_push(
    delay: int = 0,
    db: AsyncSession = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    """Send a test notification to the caller's own subscribed devices.
    delay (0-120 s) lets you lock the phone first and prove background
    delivery — the case that matters when you're out on the lot."""
    delay = max(0, min(120, delay))
    payload = {
        "title": "🔔 NetMonitor test",
        "body": ("Background delivery works — you'll get alerts with the phone locked."
                 if delay else "Push notifications are working on this device."),
        "tag": "test",
        "url": "/",
    }
    if not delay:
        return {"sent": await send_push(db, payload, only_user_id=user.id), "delay": 0}
    n = (await db.execute(
        select(func.count()).select_from(PushSubscription).where(PushSubscription.user_id == user.id)
    )).scalar_one()
    uid = user.id

    async def later() -> None:
        await asyncio.sleep(delay)
        async with SessionLocal() as s2:
            await send_push(s2, payload, only_user_id=uid)

    t = asyncio.create_task(later())
    _pending_tests.add(t)
    t.add_done_callback(_pending_tests.discard)
    return {"sent": int(n), "delay": delay}


@router.get("/recent")
async def recent(db: AsyncSession = Depends(get_db), _user: User = Depends(current_user)) -> list[dict]:
    """Last 20 pushes the server tried to send, with delivery counts."""
    rows = (await db.execute(
        select(NotificationLog).order_by(NotificationLog.created_at.desc()).limit(20)
    )).scalars()
    return [{"at": r.created_at.isoformat(), "title": r.title, "body": r.body, "url": r.url,
             "devices": r.devices, "delivered": r.delivered, "error": r.error} for r in rows]
