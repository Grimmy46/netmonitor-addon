"""Nightly auto-close: between 10:30 PM and 2:00 AM (show-local time), if a
large share of kiosks drop off at once the show has closed — pause alerts
until the next gate opening (app/services/route.py) and tell the group once.
Outside that window the same drop is a real outage and alerts as usual."""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Agent
from app.services import closure, route

logger = logging.getLogger("netmonitor.nightly")

WINDOW = (time(22, 30), time(2, 0))
SHARE = 0.40          # ≥40% of active kiosks offline / shut down
GRACE_MIN = 30        # alerts resume 30 min after gates open
_STALE_DAYS = 7


def _in_window(t: time) -> bool:
    a, b = WINDOW
    return t >= a or t < b


def _night_key(local: datetime) -> str:
    # After midnight still belongs to the evening before.
    d = local.date() - timedelta(days=1) if local.time() < WINDOW[1] else local.date()
    return d.isoformat()


async def close_until_open(db: AsyncSession, acc: Account, now: datetime, note: str) -> datetime:
    end = route.next_opening(now)
    if end is None:  # off-route: fall back to noon tomorrow, show-local
        tz = route.tz_now(now)
        loc = now.astimezone(tz)
        d = loc.date() + timedelta(days=0 if loc.time() < time(6, 0) else 1)
        end = datetime.combine(d, time(12, 0), tzinfo=tz)
    acc.closure_start, acc.closure_end = now, end
    acc.closure_note = note
    acc.closure_report_sent_at = None
    acc.closure_grace_min = GRACE_MIN
    await db.commit()
    await closure.refresh(db)
    return end


def _iso(s):
    from app.services.morning import _iso as f
    return f(s)


async def maybe_autoclose(db: AsyncSession, now: datetime) -> None:
    if closure.phase(now) in ("closed", "scheduled"):
        return
    tz = route.tz_now(now)
    local = now.astimezone(tz)
    if not _in_window(local.time()):
        return
    acc = (await db.execute(select(Account).limit(1))).scalars().first()
    key = _night_key(local)
    if acc is None or acc.autoclose_last == key:
        return  # once per night (a manual Reopen sticks)
    kiosks = [a for a in (await db.execute(select(Agent).where(Agent.station_group == "kiosk"))).scalars()
              if a.claimed_at and (_iso(a.last_seen_at) or now - timedelta(days=99)) > now - timedelta(days=_STALE_DAYS)]
    if len(kiosks) < 5:
        return
    off = [a for a in kiosks if a.status != "online" or a.powered_off_at is not None]
    if len(off) / len(kiosks) < SHARE:
        return
    acc.autoclose_last = key
    end = await close_until_open(db, acc, now, "Auto: closed for the night")
    when = end.astimezone(tz).strftime("%a %-I:%M %p")
    text = (f"🌙 NETBOT: looks like we've closed for the night — {len(off)}/{len(kiosks)} kiosks off at "
            f"{local.strftime('%-I:%M %p')}. Alerts paused until {when}. "
            "If that's wrong, tap Reopen on the NetMonitor Live tab.")
    logger.info("Auto-close: %s", text)
    try:
        from app.services import netbot
        await netbot.dm(db, text, "netbot-night")
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto-close notice failed: %s", exc)
