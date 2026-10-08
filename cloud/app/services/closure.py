"""Planned closures ("the show is dark until Tuesday").

While a closure is on, three things change:

1. Alerts pause (like teardown, but for a scheduled multi-day window with no
   18 h safety expiry). keep_monitored sites/devices still alert.
2. The dormant clock is FROZEN for the window: time spent inside the closure
   doesn't count toward dormant_after_days / site_dormant_after_hours. A
   switch that's unplugged Friday night and dies over the break will NOT age
   into "dormant" (and vanish from counts/alerts) by Tuesday — its clock only
   starts again at reopening.
3. After reopening there's a short grace window while gear powers back up;
   when it ends, ONE summary push lists what didn't come back, grouped by
   root cause (app/workers/alerts.py).

The window lives on the Account row; it's cached in-process (single uvicorn
process) so the sync dormant helpers used by many queries can read it.
Refreshed at startup, on every alert sweep, and whenever it's edited.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Account, Device

_window: tuple[datetime, datetime] | None = None
_grace_min: int | None = None


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def refresh(db: AsyncSession) -> tuple[datetime, datetime] | None:
    global _window, _grace_min
    acc = (await db.execute(select(Account).limit(1))).scalar_one_or_none()
    _grace_min = acc.closure_grace_min if acc is not None else None
    if acc is not None and acc.closure_start and acc.closure_end and acc.closure_end > acc.closure_start:
        _window = (acc.closure_start, acc.closure_end)
    else:
        _window = None
    return _window


def _grace() -> timedelta:
    if _grace_min is not None:
        return timedelta(minutes=_grace_min)
    return timedelta(hours=get_settings().closure_reopen_grace_hours)


def window() -> tuple[datetime, datetime] | None:
    return _window


def set_window(w: tuple[datetime, datetime] | None) -> None:
    global _window
    _window = w


def phase(now: datetime | None = None) -> str | None:
    """scheduled | closed | reopening | None (no closure / long over)."""
    if _window is None:
        return None
    now = now or _now()
    start, end = _window
    grace = _grace()
    if now < start:
        return "scheduled"
    if now < end:
        return "closed"
    if now < end + grace:
        return "reopening"
    return None


def alerts_paused(now: datetime | None = None) -> bool:
    return phase(now) in ("closed", "reopening")


def reopen_until() -> datetime | None:
    if _window is None:
        return None
    return _window[1] + _grace()


def effective_offline_seconds(since: datetime | None, now: datetime) -> float | None:
    """Offline age with the closure window cut out."""
    if since is None:
        return None
    age = (now - since).total_seconds()
    if _window is None:
        return age
    start, end = _window
    overlap = (min(now, end) - max(since, start)).total_seconds()
    return age - max(0.0, overlap)


def device_dormant(manual: bool, is_online: bool | None, since: datetime | None,
                   now: datetime) -> bool:
    if manual:
        return True
    if is_online is not False and since is None:
        return False
    eff = effective_offline_seconds(since, now)
    return eff is not None and eff >= get_settings().dormant_after_days * 86400


def site_dormant_age_ok(since: datetime | None, now: datetime) -> bool:
    eff = effective_offline_seconds(since, now)
    return eff is not None and eff >= get_settings().site_dormant_after_hours * 3600


def dormant_sql(now: datetime | None = None):
    """SQL predicate equivalent of device_dormant() (closure-aware)."""
    now = now or _now()
    d = timedelta(days=get_settings().dormant_after_days)
    cutoff = now - d
    off = Device.offline_since
    if _window is None:
        aged = and_(off.is_not(None), off <= cutoff)
    else:
        start, end = _window
        e = min(now, end)
        c1 = e - start if e > start else timedelta(0)
        aged = or_(
            and_(off.is_not(None), off < start, off <= cutoff - c1),
            and_(off >= start, off < end) if (now - e) >= d else false(),
            and_(off >= end, off <= cutoff),
        )
    return or_(Device.manual_dormant.is_(True), aged)


def powered_down_for_closure(d: Device, now: datetime | None = None) -> bool:
    """Offline because the show is closed (went down at/after the start)."""
    if phase(now) != "closed" or d.is_online is not False or d.offline_since is None:
        return False
    start, _ = _window  # type: ignore[misc]
    return d.offline_since >= start - timedelta(hours=2)
