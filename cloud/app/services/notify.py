"""Web Push delivery: VAPID keypair management + sending notifications.

The account's VAPID keypair is generated lazily on first use and stored on the
accounts row (base64url raw EC P-256 values — the format browsers and pywebpush
both speak). Sending is done with pywebpush, which is synchronous — callers run
it via asyncio.to_thread so the event loop never blocks.

A push whose subscription the push service reports gone (404/410) is pruned.
"""
import asyncio
import base64
import json
import logging

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import PushSubscription
from app.services.sync import get_or_create_account

logger = logging.getLogger("netmonitor.notify")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


async def get_vapid_keys(db: AsyncSession) -> tuple[str, str]:
    """Return (public, private) base64url raw keys, generating once if absent."""
    account = await get_or_create_account(db)
    if account.vapid_public_key and account.vapid_private_key:
        return account.vapid_public_key, account.vapid_private_key
    key = ec.generate_private_key(ec.SECP256R1())
    public = _b64url(
        key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    private = _b64url(key.private_numbers().private_value.to_bytes(32, "big"))
    account.vapid_public_key = public
    account.vapid_private_key = private
    await db.commit()
    logger.info("Generated VAPID keypair for account %s", account.id)
    return public, private


def _send_one(sub: dict, payload: str, private_key: str) -> None:
    webpush(
        subscription_info=sub,
        data=payload,
        vapid_private_key=private_key,
        vapid_claims={"sub": get_settings().vapid_subject},
        timeout=10,
    )


async def send_push(
    db: AsyncSession,
    payload: dict,
    *,
    only_user_id=None,
) -> int:
    """Send `payload` (title/body/tag/url) to every stored subscription (or one
    user's). Returns how many pushes were accepted. Dead subscriptions are
    pruned; transient failures are counted and logged, never raised."""
    stmt = select(PushSubscription)
    if only_user_id is not None:
        stmt = stmt.where(PushSubscription.user_id == only_user_id)
    subs = list((await db.execute(stmt)).scalars())
    if not subs:
        return 0
    _, private_key = await get_vapid_keys(db)
    body = json.dumps(payload)
    sent = 0
    dead: list = []
    for s in subs:
        info = {
            "endpoint": s.endpoint,
            "keys": {"p256dh": s.p256dh, "auth": s.auth},
        }
        try:
            await asyncio.to_thread(_send_one, info, body, private_key)
            sent += 1
            s.failures = 0
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                dead.append(s.id)
                logger.info("Pruning gone push subscription (%s)", status)
            else:
                s.failures += 1
                logger.warning("Push failed (%s): %s", status, exc)
        except Exception as exc:  # noqa: BLE001 — a push must never kill the sweep
            s.failures += 1
            logger.warning("Push failed: %s", exc)
    if dead:
        await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(dead)))
    await db.commit()
    return sent
