"""Super-fast heads-up pushes to admins, straight from the 20-second UniFi
refresh — no confirm wait. The normal (confirmed) alerts still go to the
RCS-IT Signal group a few minutes later; these only go to admins' phones as
app pushes (Signal can't buzz you for messages sent from your own account).

Covers switch / gateway down + back up, and WAN link down + back up.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Device, Site, User
from app.services import closure

logger = logging.getLogger("netmonitor.fast")

_COOLDOWN = timedelta(minutes=10)
_sent_down: dict[str, datetime] = {}   # key → when we pushed "down"


def _label(d: Device) -> str:
    from app.services.names import short
    try:
        return short(d.name) or d.name
    except Exception:  # noqa: BLE001
        return d.name


async def _quiet(db: AsyncSession, site: Site, now: datetime) -> bool:
    if closure.alerts_paused(now):
        return True
    if site.keep_monitored:
        return False
    if site.teardown_active:
        return True
    acc = (await db.execute(select(Account).limit(1))).scalars().first()
    return bool(acc and acc.teardown_mode)


async def _push_admins(db: AsyncSession, title: str, body: str, tag: str) -> None:
    from app.services.notify import send_push
    admins = (await db.execute(select(User.id).where(User.role == "admin"))).scalars().all()
    for uid in admins:
        try:
            await send_push(db, {"title": title, "body": body, "tag": tag, "url": "/",
                                 "ttl": 600, "urgency": "high"}, only_user_id=uid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("fast push failed: %s", exc)


async def notify(db: AsyncSession, site: Site, went_down: list[Device], came_up: list[Device],
                 wan_down: list[str], wan_up: list[str], now: datetime) -> None:
    went_down = [d for d in went_down if d.device_type in ("switch", "gateway")]
    came_up = [d for d in came_up if d.device_type in ("switch", "gateway")]
    if not (went_down or came_up or wan_down or wan_up):
        return
    if await _quiet(db, site, now):
        return
    from app.services import mute
    muted, _ = await mute.muted_sets(db, now)
    if muted:
        went_down = [d for d in went_down if d.id not in muted]
        came_up = [d for d in came_up if d.id not in muted]
        if not (went_down or came_up or wan_down or wan_up):
            return
    # Down: one push for the whole batch (a dead parent takes its children with it).
    fresh = [d for d in went_down
             if now - _sent_down.get(str(d.id), now - _COOLDOWN * 2) >= _COOLDOWN]
    if fresh:
        for d in fresh:
            _sent_down[str(d.id)] = now
        names = sorted((_label(d) for d in fresh), key=len)
        head = names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1} more"
        await _push_admins(db, f"⚡ {head} just went offline",
                           f"{site.name}: {', '.join(names[:6])}. Unconfirmed — the group alert follows if it stays down.",
                           f"fast-{fresh[0].id}")
        logger.info("Fast push: down %s", names)
    ups = [d for d in came_up if str(d.id) in _sent_down]
    if ups:
        for d in ups:
            _sent_down.pop(str(d.id), None)
        names = sorted((_label(d) for d in ups), key=len)
        await _push_admins(db, f"✅ {names[0]}{f' +{len(names) - 1}' if len(names) > 1 else ''} back online",
                           f"{site.name}: {', '.join(names[:6])}", f"fast-{ups[0].id}")
    for k in wan_down:
        key = f"wan:{site.id}:{k}"
        if now - _sent_down.get(key, now - _COOLDOWN * 2) >= _COOLDOWN:
            _sent_down[key] = now
            await _push_admins(db, f"⚡ {k} just went down", f"{site.name}: {k} lost — unconfirmed.", f"fast-{key}")
    for k in wan_up:
        key = f"wan:{site.id}:{k}"
        if _sent_down.pop(key, None):
            await _push_admins(db, f"✅ {k} back up", f"{site.name}: {k} restored.", f"fast-{key}")
