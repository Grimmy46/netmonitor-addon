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

_LABEL = {"setup": "Setup", "open": "Open", "teardown": "Teardown", "offseason": "Off-season"}
_POSTS = {"setup": "setup progress", "open": "outages", "teardown": "teardown progress",
          "offseason": "none"}


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def effects(mode: str) -> dict:
    """What runs in a mode (the plan's effects table). Pure."""
    return {
        "alerts": mode == "open",
        "ap_restart": mode == "open",
        "bot": "deploy" if mode == "setup" else "return" if mode == "teardown" else None,
        "group_posts": _POSTS.get(mode, "none"),
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
          until: datetime | None = None, by: str | None = None,
          event_name: str | None = None, venue: str | None = None,
          set_name: bool = False, set_venue: bool = False) -> str:
    """Switch the account to `mode` and drive the underlying mechanisms.
    Pure on the ORM object (no I/O); returns the previous mode."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    now = now or _now()
    old = current(acc)
    was_season = old == "offseason" or _season_window(acc, now)

    if mode in PAUSED:
        _pause_on(acc, now, until)
        if was_season:
            _end_closure(acc, now, reopen=False)   # no reopen report: setup/teardown has its own
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
        new = "open"                       # auto-expired, or old toggle switched off
    elif old == "offseason" and not _closure_running(acc, now):
        new = "open"                       # off-season closure ended / was cleared
    elif old == "open" and _season_window(acc, now):
        new = "offseason"                  # a long closure set via /network/closure
    elif old == "open" and acc.teardown_mode:
        new = "teardown"                   # old /agents/teardown toggle
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


def out(acc: Account | None) -> dict:
    """The GET/PUT /network/mode response (shared contract)."""
    mode = current(acc)

    def iso(x):
        return x.isoformat() if x else None
    return {
        "mode": mode,
        "since": iso(getattr(acc, "event_mode_since", None)),
        "event_name": getattr(acc, "event_name", None),
        "venue": getattr(acc, "event_venue", None),
        "until": iso(getattr(acc, "event_mode_until", None)),
        "changed_by": getattr(acc, "event_mode_by", None),
        "effects": effects(mode),
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
                   until: datetime | None = None, event_name: str | None = None,
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
