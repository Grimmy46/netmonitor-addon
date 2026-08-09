"""Web-push notification endpoints: VAPID key handout, subscription
management, and a self-test push.

Any signed-in user (admin or viewer) may enable alerts on their own devices —
subscriptions are per-user, so removing a user cascades their subscriptions
away. The alert sweep (workers/alerts.py) fans out to every subscription.
"""
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_user
from app.core.db import get_db
from app.models import PushSubscription
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


@router.get("/vapid", response_model=VapidOut)
async def vapid_public_key(
    db: AsyncSession = Depends(get_db), _user: User = Depends(current_user)
) -> VapidOut:
    public, _ = await get_vapid_keys(db)
    return VapidOut(public_key=public)


@router.get("/status", response_model=StatusOut)
async def status(
    db: AsyncSession = Depends(get_db), user: User = Depends(current_user)
) -> StatusOut:
    total = (await db.execute(select(func.count(PushSubscription.id)))).scalar() or 0
    mine = (
        await db.execute(
            select(func.count(PushSubscription.id)).where(
                PushSubscription.user_id == user.id
            )
        )
    ).scalar() or 0
    return StatusOut(subscription_count=int(total), mine=int(mine))


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


@router.post("/test")
async def test_push(
    db: AsyncSession = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    """Send a test notification to the caller's own subscribed devices."""
    sent = await send_push(
        db,
        {
            "title": "🔔 NetMonitor test",
            "body": "Push notifications are working on this device.",
            "tag": "test",
            "url": "/",
        },
        only_user_id=user.id,
    )
    return {"sent": sent}
