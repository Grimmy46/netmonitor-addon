"""Nightly auto-close (show-local time), two triggers — whichever comes first:

1. Kiosk drop: between 10:30 PM and 6:00 AM, ≥40% of kiosks off = we closed.
2. Scheduled: on a fair night, at the auto-close time (default 1:00 AM) — if
   nobody said "closed", close anyway (kiosks often stay on for hours).

Either way alerts pause until the next gate opening (route.py) and admins get
one private notice. A manual "open"/Reopen sticks for the rest of the night."""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Agent
from app.services import closure, route

logger = logging.getLogger("netmonitor.nightly")

WINDOW = (time(22, 30), time(6, 0))
DEFAULT_AT = "01:00"   # scheduled auto-close; "off" disables
SHARE = 0.40          # ≥40% of active kiosks offline / shut down
GRACE_MIN = 30        # alerts resume 30 min after gates open
_STALE_DAYS = 7


def _in_window(t: time) -> bool:
    a, b = WINDOW
    return t >= a or t < b


def autoclose_at(acc: Account | None) -> time | None:
    """The scheduled auto-close time, or None when switched off."""
    v = str(((acc.netbot_state if acc else None) or {}).get("autoclose_at") or DEFAULT_AT)
    if v == "off":
        return None
    try:
        hh, mm = (int(x) for x in v.split(":"))
        return time(hh, mm)
    except Exception:  # noqa: BLE001
        return time(1, 0)


def _past(t: time, at: time) -> bool:
    """Is it at/after tonight's auto-close time? `at` may be evening (e.g. 23:30)
    or after midnight (e.g. 01:00); the night ends at 6 AM either way."""
    if at < WINDOW[1]:                     # after-midnight time, e.g. 01:00
        return at <= t < WINDOW[1]
    return t < WINDOW[1] or t >= at        # evening time, e.g. 23:30


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
    acc = (await db.execute(select(Account).limit(1))).scalars().first()
    at = autoclose_at(acc)
    scheduled = at is not None and _past(local.time(), at)
    if not (_in_window(local.time()) or scheduled):
        return
    key = _night_key(local)
    if acc is None or acc.autoclose_last == key:
        return  # once per night (a manual Reopen sticks)
    kiosks = [a for a in (await db.execute(select(Agent).where(Agent.station_group == "kiosk"))).scalars()
              if a.claimed_at and (_iso(a.last_seen_at) or now - timedelta(days=99)) > now - timedelta(days=_STALE_DAYS)]
    off = [a for a in kiosks if a.status != "online" or a.powered_off_at is not None]
    dropped = _in_window(local.time()) and len(kiosks) >= 5 and len(off) / len(kiosks) >= SHARE
    # Scheduled close only on nights the fair was actually open.
    fair_night = route.opening_on(datetime.fromisoformat(key).date()) is not None
    if not dropped and not (scheduled and fair_night):
        return
    acc.autoclose_last = key
    end = await close_until_open(db, acc, now, "Auto: closed for the night" if dropped
                                 else "Auto: closed for the night (scheduled)")
    when = end.astimezone(tz).strftime("%a %-I:%M %p")
    if dropped:
        text = (f"🌙 NETBOT: looks like we've closed for the night — {len(off)}/{len(kiosks)} kiosks off at "
                f"{local.strftime('%-I:%M %p')}. Alerts paused until {when}. "
                "If that's wrong, tap Reopen on the NetMonitor Live tab.")
    else:
        text = (f"🌙 NETBOT: nobody said we're closed, so I closed for the night at "
                f"{local.strftime('%-I:%M %p')} ({len(kiosks) - len(off)}/{len(kiosks)} kiosks still on). "
                f"Alerts paused until {when}. If we're still open, send \"open\" in RCS-IT or tap Reopen.")
    logger.info("Auto-close: %s", text)
    try:
        from app.services import netbot
        await netbot.dm(db, text, "netbot-night")
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto-close notice failed: %s", exc)
