"""Fast heads-up pushes to admins, from the 20-second UniFi refresh and the
kiosk ping verdicts — after ONE quick re-check (~25 s). The normal (confirmed) alerts still go to the
RCS-IT Signal group a few minutes later; these only go to admins' phones as
app pushes (Signal can't buzz you for messages sent from your own account).

Covers switch / gateway down + back up, and WAN link down + back up.

Confirm step (2026-10-11): a single bad reading used to buzz every phone —
e.g. one UniFi answer for the Main HA gateway pair showing WAN1 AND WAN2 down
for 27 s, or a 4 s kiosk-ping hiccup flagging ERC + Grand Stand + Kiosk 9 AP.
Now a "down" is held for _CONFIRM and re-read from the DB; it is only pushed
if the device / WAN link is still down. Blips that clear inside the window
send nothing (and so no "back up" push either). When EVERY WAN link at a site
drops at once, the push is also held while that site's kiosks are still
checking in with the cloud — they reach us over that same internet, so the
internet is plainly up and the reading is bad data, not an outage.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Device, Site, User
from app.services import closure

logger = logging.getLogger("netmonitor.fast")

_COOLDOWN = timedelta(minutes=10)
_CONFIRM = timedelta(seconds=25)       # one more UniFi refresh (20 s) + slack
_sent_down: dict[str, datetime] = {}   # key → when we pushed "down"
_pending: dict[str, datetime] = {}     # key → when the unconfirmed "down" was seen
_tasks: set[asyncio.Task] = set()      # keep refs so confirm tasks aren't GC'd


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


def _spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def _cooled(key: str, now: datetime) -> bool:
    return now - _sent_down.get(key, now - _COOLDOWN * 2) >= _COOLDOWN


async def notify(db: AsyncSession, site: Site, went_down: list[Device], came_up: list[Device],
                 wan_down: list[str], wan_up: list[str], now: datetime) -> None:
    went_down = [d for d in went_down if d.device_type in ("switch", "gateway")]
    came_up = [d for d in came_up if d.device_type in ("switch", "gateway")]
    if not (went_down or came_up or wan_down or wan_up):
        return
    # Recoveries first: a blip that clears inside the confirm window is
    # dropped silently; a confirmed (pushed) outage gets its "back up".
    ups = []
    for d in came_up:
        k = str(d.id)
        if _pending.pop(k, None) is None and k in _sent_down:
            _sent_down.pop(k, None)
            ups.append(d)
    wan_ups = []
    for k in wan_up:
        key = f"wan:{site.id}:{k}"
        if _pending.pop(key, None) is None and _sent_down.pop(key, None):
            wan_ups.append(k)
    if ups:
        names = sorted((_label(d) for d in ups), key=len)
        await _push_admins(db, f"✅ {names[0]}{f' +{len(names) - 1}' if len(names) > 1 else ''} back online",
                           f"{site.name}: {', '.join(names[:6])}", f"fast-{ups[0].id}")
    for k in wan_ups:
        key = f"wan:{site.id}:{k}"
        await _push_admins(db, f"✅ {k} back up", f"{site.name}: {k} restored.", f"fast-{key}")
    if not (went_down or wan_down):
        return
    if await _quiet(db, site, now):
        return
    from app.services import mute
    muted, _ = await mute.muted_sets(db, now)
    if muted:
        went_down = [d for d in went_down if d.id not in muted]
    # Downs: hold, then re-check (one task per batch — a dead parent takes its children with it).
    fresh = [d for d in went_down if str(d.id) not in _pending and _cooled(str(d.id), now)]
    if fresh:
        for d in fresh:
            _pending[str(d.id)] = now
        logger.info("Fast alert held for re-check: down %s", [_label(d) for d in fresh])
        _spawn(_confirm_devices(site.id, [d.id for d in fresh], now))
    wan_fresh = [k for k in wan_down
                 if f"wan:{site.id}:{k}" not in _pending and _cooled(f"wan:{site.id}:{k}", now)]
    if wan_fresh:
        n_links = len((site.wan_status or {}).get("links") or [])
        all_down = n_links > 0 and not any(x.get("up") for x in (site.wan_status or {}).get("links") or [])
        for k in wan_fresh:
            _pending[f"wan:{site.id}:{k}"] = now
        logger.info("Fast WAN alert held for re-check: %s (all links down: %s)", wan_fresh, all_down)
        _spawn(_confirm_wan(site.id, wan_fresh, all_down, now))


async def _confirm_devices(site_id, ids: list, seen: datetime) -> None:
    try:
        await asyncio.sleep(_CONFIRM.total_seconds())
        keys = [str(i) for i in ids]
        live = [k for k in keys if _pending.get(k) == seen]
        if not live:
            return
        from app.core.db import SessionLocal
        async with SessionLocal() as db:
            site = await db.get(Site, site_id)
            devs = (await db.execute(select(Device).where(Device.id.in_(ids)))).scalars().all()
            still = [d for d in devs if str(d.id) in live and d.is_online is False]
            for k in live:
                _pending.pop(k, None)
            now = datetime.now(tz=timezone.utc)
            if not still or site is None:
                logger.info("Fast alert dropped — back up within %ss", int(_CONFIRM.total_seconds()))
                return
            if await _quiet(db, site, now):
                return
            for d in still:
                _sent_down[str(d.id)] = now
            names = sorted((_label(d) for d in still), key=len)
            head = names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1} more"
            from app.services import linkhealth
            if all(linkhealth.unifi_alive(d, now) or d.link_status == "degraded" for d in still):
                await _push_admins(db, f"🟠 {head} not answering pings",
                                   f"{site.name}: {', '.join(names[:6])}. UniFi still sees it up and powered — "
                                   "likely a bad or slow cable, not power.", f"fast-{still[0].id}")
                logger.info("Fast push (link trouble): %s", names)
                return
            await _push_admins(db, f"⚡ {head} went offline",
                               f"{site.name}: {', '.join(names[:6])}. Still down on re-check — "
                               "the group alert follows if it stays down.",
                               f"fast-{still[0].id}")
            logger.info("Fast push (confirmed): down %s", names)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fast alert confirm failed: %s", exc)


async def _kiosks_checking_in(db: AsyncSession, site_id, since: datetime) -> int:
    from app.models import Agent
    rows = (await db.execute(select(Agent.last_seen_at).where(Agent.site_id == site_id))).scalars().all()
    n = 0
    for v in rows:
        try:
            ts = datetime.fromisoformat(v) if v else None
        except ValueError:
            ts = None
        if ts is not None:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts > since:
                n += 1
    return n


async def _confirm_wan(site_id, links: list[str], all_down: bool, seen: datetime) -> None:
    try:
        await asyncio.sleep(_CONFIRM.total_seconds())
        keys = {k: f"wan:{site_id}:{k}" for k in links}
        live = [k for k, key in keys.items() if _pending.get(key) == seen]
        if not live:
            return
        from app.core.db import SessionLocal
        async with SessionLocal() as db:
            site = await db.get(Site, site_id)
            for k in live:
                _pending.pop(keys[k], None)
            if site is None:
                return
            now = datetime.now(tz=timezone.utc)
            state = {x.get("key"): x.get("up") for x in ((site.wan_status or {}).get("links") or [])}
            still = [k for k in live if state.get(k) is False]
            if not still:
                logger.info("Fast WAN alert dropped — %s back up within %ss", live, int(_CONFIRM.total_seconds()))
                return
            if all_down and not any(state.values()):
                n = await _kiosks_checking_in(db, site_id, seen)
                if n:
                    logger.info("Fast WAN alert held — all links read down but %d kiosk(s) at %s "
                                "checked in over the internet since; treating as bad UniFi data",
                                n, site.name)
                    return
            if await _quiet(db, site, now):
                return
            for k in still:
                _sent_down[keys[k]] = now
                await _push_admins(db, f"⚡ {k} went down",
                                   f"{site.name}: {k} lost — still down on re-check.", f"fast-{keys[k]}")
            logger.info("Fast WAN push (confirmed): %s", still)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fast WAN confirm failed: %s", exc)
