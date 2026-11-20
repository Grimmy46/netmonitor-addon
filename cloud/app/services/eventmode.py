"""The one event-mode switch: setup | open | teardown | offseason.

This is a facade over the older, tested mechanisms. Nothing in the alert code
reads event_mode for pausing; setting the mode drives the old fields instead:

    setup      -> account.teardown_mode on (the same fault-alert pause path),
                  daily AP restart off, bot listens and marks devices deployed
    open       -> teardown_mode off, any off-season closure ended
    teardown   -> account.teardown_mode on (no auto-off unless `until` is set),
                  AP restart off, bot listens and marks devices returned
    offseason  -> a long closure window (app/services/closure.py), so
                  season_paused() is true: alerts, AP restart and reports stop

Old toggles still work: reconcile() folds them back into the mode (teardown
toggled on -> teardown; teardown auto-expired -> open; a long closure ->
offseason; that closure over -> open).

Plan: NetMon-3-Upgrade-Plan.md section 1.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account

logger = logging.getLogger("netmonitor.mode")

MODES = ("setup", "open", "teardown", "offseason")
PAUSED = ("setup", "teardown")          # modes that use the teardown_mode pause
OFFSEASON_NOTE = "Off-season"
# Off-season with no end date: keep the closure open this long.
OFFSEASON_DEFAULT = timedelta(days=365)
SEASON_MIN_LEFT = timedelta(hours=24)   # same rule as closure.season_paused()
# Safety end for the pause when no `until` is given, so a forgotten Setup or
# Teardown can't keep alerts quiet at the next venue. Expiry goes back to the
# previous mode (Open, or Off-season while its closure is still running).
DEFAULT_UNTIL = {"teardown": timedelta(hours=18), "setup": timedelta(hours=72)}
_DEFAULT = object()

_LABEL = {"setup": "Setup", "open": "Open", "teardown": "Teardown", "offseason": "Off-season"}
_POSTS = {"setup": "setup progress", "open": "outages", "teardown": "teardown progress",
          "offseason": "none"}


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def effects(mode: str, reopen_grace_until: datetime | None = None) -> dict:
    """What runs in a mode (the plan's effects table). Pure.

    reopen_grace_until: set right after leaving Off-season (or any closure).
    Until then fault alerts stay paused while gear powers back up; at the end
    one "didn't come back" summary is pushed if settings.closure_reopen_report
    is on (the default)."""
    from app.core.config import get_settings
    in_grace = reopen_grace_until is not None
    return {
        "alerts": mode == "open" and not in_grace,
        "ap_restart": mode == "open",
        "bot": "deploy" if mode == "setup" else "return" if mode == "teardown" else None,
        "group_posts": _POSTS.get(mode, "none"),
        "reopen_grace_until": reopen_grace_until.isoformat() if in_grace else None,
        "reopen_report": bool(in_grace and get_settings().closure_reopen_report),
    }


def current(acc: Account | None) -> str:
    m = getattr(acc, "event_mode", None) if acc is not None else None
    return m if m in MODES else "open"


def _season_window(acc: Account, now: datetime) -> bool:
    """A closure is running with more than a day left (closure.season_paused)."""
    s, e = acc.closure_start, acc.closure_end
    return bool(s and e and s <= now < e and (e - now) > SEASON_MIN_LEFT)


def _closure_running(acc: Account, now: datetime) -> bool:
    s, e = acc.closure_start, acc.closure_end
    return bool(s and e and s <= now < e)


def season_closure(acc: Account | None, now: datetime | None = None) -> bool:
    """An off-season closure is running: one set by this switch, or any closure
    with more than a day left. A nightly "We're closed" never counts."""
    if acc is None:
        return False
    now = now or _now()
    return _closure_running(acc, now) and (
        acc.closure_note == OFFSEASON_NOTE or _season_window(acc, now))


def needs_confirm(acc: Account | None, mode: str, now: datetime | None = None) -> bool:
    """Leaving Off-season (or ending its closure by going Open) must be confirmed."""
    if mode == "offseason":
        return False
    return current(acc) == "offseason" or (mode == "open" and season_closure(acc, now))


def _grace(acc: Account) -> timedelta:
    from app.core.config import get_settings
    if acc.closure_grace_min is not None:
        return timedelta(minutes=acc.closure_grace_min)
    return timedelta(hours=get_settings().closure_reopen_grace_hours)


def reopen_grace_until(acc: Account | None, now: datetime | None = None) -> datetime | None:
    """End of the reopen grace after a closure ended (gear powering back up;
    the "didn't come back" report goes out then), or None outside it."""
    if acc is None or not (acc.closure_start and acc.closure_end):
        return None
    now = now or _now()
    until = acc.closure_end + _grace(acc)
    return until if acc.closure_end <= now < until else None


def _end_closure(acc: Account, now: datetime, reopen: bool) -> None:
    """End the off-season closure. reopen=True keeps the window ending now, so
    the usual reopen grace + "what didn't come back" summary still happen
    (same as DELETE /network/closure). reopen=False clears it outright."""
    s, e = acc.closure_start, acc.closure_end
    if not (s and e):
        return
    if not reopen or s > now:              # drop it (or cancel a scheduled one)
        acc.closure_start = acc.closure_end = acc.closure_note = None
        acc.closure_grace_min = None
    elif now < e:                          # running: reopen now
        acc.closure_end = now
    # already over: leave it, so its reopen report still goes out


def _pause_on(acc: Account, now: datetime, until: datetime | None) -> None:
    if not acc.teardown_mode:
        acc.teardown_since = now
    acc.teardown_mode = True
    acc.teardown_auto_off_at = until     # None = no auto-off


def _pause_off(acc: Account) -> None:
    acc.teardown_mode = False
    acc.teardown_since = None
    acc.teardown_auto_off_at = None


def apply(acc: Account, mode: str, now: datetime | None = None, *,
          until=_DEFAULT, by: str | None = None,
          event_name: str | None = None, venue: str | None = None,
          set_name: bool = False, set_venue: bool = False) -> str:
    """Switch the account to `mode` and drive the underlying mechanisms.
    Pure on the ORM object (no I/O); returns the previous mode.

    until: omitted -> the safety default for Setup/Teardown (DEFAULT_UNTIL),
    None -> no auto-off (only the old toggle with hours=null does this).
    Setup/Teardown never delete a running off-season closure: the pause goes
    on top, and when it ends the mode falls back to Off-season. Only Open
    ends the closure (callers must check needs_confirm() first)."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    now = now or _now()
    old = current(acc)
    was_season = old == "offseason" or season_closure(acc, now)
    if until is _DEFAULT:
        until = now + DEFAULT_UNTIL[mode] if mode in DEFAULT_UNTIL else None

    if mode in PAUSED:
        _pause_on(acc, now, until)
    elif mode == "open":
        _pause_off(acc)
        if was_season:
            _end_closure(acc, now, reopen=True)
    else:  # offseason
        _pause_off(acc)
        start = acc.closure_start if _closure_running(acc, now) else now
        acc.closure_start = start
        acc.closure_end = until or (now + OFFSEASON_DEFAULT)
        acc.closure_note = OFFSEASON_NOTE
        acc.closure_grace_min = None
        acc.closure_report_sent_at = None

    if old != mode or acc.event_mode_since is None:
        acc.event_mode_since = now
    acc.event_mode = mode
    acc.event_mode_until = until
    acc.event_mode_by = by
    if set_name:
        acc.event_name = (event_name or "").strip() or None
    if set_venue:
        acc.event_venue = (venue or "").strip() or None
    return old


def reconcile(acc: Account, now: datetime | None = None) -> bool:
    """Fold changes made through the old toggles back into the mode.
    Pure; returns True if the mode changed."""
    now = now or _now()
    old = current(acc)
    new = old
    if old in PAUSED and not acc.teardown_mode:
        # auto-expired, or old toggle switched off: back to the previous mode
        new = "offseason" if season_closure(acc, now) else "open"
    elif old in ("open", "offseason") and acc.teardown_mode:
        new = "teardown"                   # old /agents/teardown toggle (pause on top)
    elif old == "offseason" and not _closure_running(acc, now):
        new = "open"                       # off-season closure ended / was cleared
    elif old == "open" and season_closure(acc, now):
        new = "offseason"                  # a long closure set via /network/closure
    if new == old:
        return False
    acc.event_mode = new
    acc.event_mode_since = now
    acc.event_mode_by = "auto"
    if new == "teardown":
        acc.event_mode_until = acc.teardown_auto_off_at
    elif new == "offseason":
        acc.event_mode_until = acc.closure_end
    else:
        acc.event_mode_until = None
    return True


def out(acc: Account | None, now: datetime | None = None) -> dict:
    """The GET/PUT /network/mode response (shared contract)."""
    mode = current(acc)
    grace = reopen_grace_until(acc, now)

    def iso(x):
        return x.isoformat() if x else None
    return {
        "mode": mode,
        "since": iso(getattr(acc, "event_mode_since", None)),
        "event_name": getattr(acc, "event_name", None),
        "venue": getattr(acc, "event_venue", None),
        "until": iso(getattr(acc, "event_mode_until", None)),
        "changed_by": getattr(acc, "event_mode_by", None),
        "reopen_grace_until": iso(grace),
        "effects": effects(mode, grace),
    }


async def _acc(db: AsyncSession) -> Account | None:
    return (await db.execute(select(Account).limit(1))).scalars().first()


async def sync(db: AsyncSession, now: datetime | None = None) -> Account | None:
    """reconcile() + commit. Called from the alert sweep and GET /network/mode."""
    acc = await _acc(db)
    if acc is None:
        return None
    old = current(acc)
    if reconcile(acc, now):
        await db.commit()
        logger.info("Event mode %s -> %s (from old toggles)", old, acc.event_mode)
        await announce(db, acc, old)
    return acc


async def set_mode(db: AsyncSession, acc: Account, mode: str, *, by: str | None,
                   until=_DEFAULT, event_name: str | None = None,
                   venue: str | None = None, set_name: bool = False,
                   set_venue: bool = False) -> str:
    old = apply(acc, mode, until=until, by=by, event_name=event_name, venue=venue,
                set_name=set_name, set_venue=set_venue)
    await db.commit()
    from app.services import closure
    await closure.refresh(db)
    logger.info("Event mode %s -> %s by %s", old, mode, by)
    if old != mode:
        await announce(db, acc, old)
    return old


async def end_pause(db: AsyncSession, acc: Account, *, by: str | None) -> str:
    """Turn the Setup/Teardown pause off and fall back to the previous mode
    (Off-season while its closure still runs, else Open). Returns the new mode."""
    old = current(acc)
    _pause_off(acc)
    reconcile(acc)
    if current(acc) != old:
        acc.event_mode_by = by
    await db.commit()
    if current(acc) != old:
        await announce(db, acc, old)
    return current(acc)


_CONFIRM_MSG = {
    "setup": "Off-season is on. Switching to Setup turns on the setup pause and ends the "
             "off-season when you go Open. Send confirm: true to go ahead.",
    "teardown": "Off-season is on. Send confirm: true to switch to Teardown.",
    "open": "Off-season is on. Going Open ends it and turns alerts back on. "
            "Send confirm: true to go ahead.",
}


def confirm_message(mode: str) -> str:
    return _CONFIRM_MSG.get(mode, "Send confirm: true to leave Off-season.")


def announce_text(acc: Account, old: str) -> str:
    mode = current(acc)
    where = " @ ".join(x for x in (acc.event_name, acc.event_venue) if x)
    head = f"NETBOT: mode is now {_LABEL[mode]}" + (f" ({where})" if where else "") + "."
    tail = {
        "setup": "Fault alerts are quiet while we set up. Post a photo of each device going in.",
        "open": "Alerts are on.",
        "teardown": "Fault alerts are off. Post a photo of each device you pack up.",
        "offseason": "Everything is paused until next season.",
    }[mode]
    return f"{head} {tail} (was {_LABEL.get(old, old)})"


async def announce(db: AsyncSession, acc: Account, old: str) -> bool:
    """RCS-IT post hook for mode changes. Disabled unless
    settings.mode_announce_rcsit is on. Never raises."""
    from app.core.config import get_settings
    if not get_settings().mode_announce_rcsit:
        return False
    try:
        from app.services.morning import send
        await send(db, announce_text(acc, old))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("mode announce failed: %s", exc)
        return False


async def bot_mode(db: AsyncSession) -> str | None:
    """What the equipment-group bot does right now: "deploy" (setup),
    "return" (teardown, or any site in its own teardown), or None."""
    from app.models import Site
    acc = await _acc(db)
    mode = current(acc)
    if acc is not None and acc.teardown_mode:
        return "deploy" if mode == "setup" else "return"
    if (await db.execute(select(Site.id).where(Site.teardown_active.is_(True)).limit(1))).first():
        return "return"
    return None
