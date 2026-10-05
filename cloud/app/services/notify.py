"""Web Push delivery: VAPID keypair management + sending notifications.

The account's VAPID keypair is generated lazily on first use and stored on the
accounts row (base64url raw EC P-256 values — the format browsers and pywebpush
both speak). Sending is done with pywebpush, which is synchronous — callers run
it via asyncio.to_thread so the event loop never blocks.

A push whose subscription the push service reports gone (404/410) is pruned.
"""
import asyncio
import re
import base64
import json
import logging

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import NotificationLog, PushSubscription
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


def _send_one(sub: dict, payload: str, private_key: str, ttl: int, urgency: str) -> None:
    # TTL matters: pywebpush defaults to 0, which tells Apple/Google to DROP
    # the push unless the phone is reachable that very instant — a locked or
    # dozing phone never saw those. Keep faults queued for hours instead, and
    # mark them high urgency so the phone wakes for them.
    webpush(
        subscription_info=sub,
        data=payload,
        vapid_private_key=private_key,
        vapid_claims={"sub": get_settings().vapid_subject},
        timeout=10,
        ttl=ttl,
        headers={"Urgency": urgency},
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
    # Hard stop while the show is closed ("We're closed" / planned closure):
    # nothing goes out except the reopen summary and a user's own test push.
    from app.services import closure
    if only_user_id is None and closure.alerts_paused() and payload.get("tag") != "closure-report":
        logger.info("Push held (closed): %s", payload.get("title"))
        return 0
    if only_user_id is None and not payload.get("no_signal"):
        await _mirror_to_signal(db, payload)
    payload.pop("no_signal", None)
    stmt = select(PushSubscription)
    if only_user_id is not None:
        stmt = stmt.where(PushSubscription.user_id == only_user_id)
    subs = list((await db.execute(stmt)).scalars())
    log = NotificationLog(title=str(payload.get("title", "")), body=str(payload.get("body", "")),
                          tag=payload.get("tag"), url=payload.get("url"), devices=len(subs))
    db.add(log)
    if not subs:
        log.error = "no registered devices"
        logger.warning("Push NOT sent (no registered devices): %s", payload.get("title"))
        await db.commit()
        return 0
    _, private_key = await get_vapid_keys(db)
    ttl = int(payload.pop("ttl", 6 * 3600))
    urgency = str(payload.pop("urgency", "high"))
    body = json.dumps(payload)
    sent = 0
    dead: list = []
    for s in subs:
        info = {
            "endpoint": s.endpoint,
            "keys": {"p256dh": s.p256dh, "auth": s.auth},
        }
        try:
            await asyncio.to_thread(_send_one, info, body, private_key, ttl, urgency)
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
    log.delivered = sent
    if sent < len(subs):
        log.error = f"{len(subs) - sent} failed" + (f", {len(dead)} expired" if dead else "")
    logger.info("Push '%s' -> %d/%d device(s)", payload.get("title"), sent, len(subs))
    if dead:
        await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(dead)))
    await db.commit()
    return sent


async def _mirror_to_signal(db: AsyncSession, payload: dict) -> None:
    """Also post the alert into the chosen Signal group (if one is set)."""
    try:
        from app.services import signal as sig
        cfg = await sig.get_config(db)
        if not cfg.alert_group_id or not cfg.number:
            return
        text = str(payload.get("title", "")).strip()
        # Group gets outages only (no APs, recoveries, tests): switches/gateways,
        # kiosks that stop reporting, and ticket printers out of paper.
        if not re.search(r"(SWITCH|GATEWAY) DOWN|switches down|kiosks? .*stopped reporting|printer: paper out",
                         text, re.I):
            return
        if payload.get("body"):
            text += "\n" + str(payload["body"]).strip()
        await asyncio.wait_for(sig.send_group(cfg.number, cfg.alert_group_id, text), timeout=20)
    except Exception as exc:  # noqa: BLE001 — Signal must never block a push
        logger.warning("Signal mirror failed: %s", exc)
