"""NETBOT: closing-early triggers.

1. Group commands in the alert group (RCS-IT): "closed" / "closing early"
   pause alerts until the next gate opening; "open" undoes it; "no" during an
   early-close check-in means "it's an outage" (alerts back on right away);
   "status" posts the opening-style report now.
2. Early-close check-in: on a fair day, 5:00–10:30 PM, a mass kiosk drop
   (≥40%) pauses alerts and asks the group to confirm — "no" restores alerts.
3. Weather heads-up: National Weather Service alerts for the fairgrounds that
   commonly close a fair early (wind, dust, storms, lightning, flooding).
4. Phone shortcut: a secret link (iPhone Shortcut / NFC tag) that does the same
   as "closed" / "open" — see routes/network.py.
"""
from __future__ import annotations

import base64
import logging
import re
import secrets
from datetime import datetime, time, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Agent
from app.services import closure, route

logger = logging.getLogger("netmonitor.netbot")

EARLY_FROM = time(17, 0)
SHARE = 0.40
_STALE_DAYS = 7

_CLOSE = re.compile(r"^(we('| a)?re |we )?(clos(ed|ing)( early| now| for the night)?|close)[.! ]*$", re.I)
_OPEN = re.compile(r"^(we('| a)?re |we )?(re)?open(ed)?( again| now)?[.! ]*$", re.I)
_NO = re.compile(r"^(no|nope|not closed|outage|it'?s an outage)[.! ]*$", re.I)
_STATUS = re.compile(r"^(netbot|status|netbot status)[?.! ]*$", re.I)

WEATHER_EVENTS = (
    "Tornado Warning", "Tornado Watch", "Severe Thunderstorm Warning", "Severe Thunderstorm Watch",
    "High Wind Warning", "Wind Advisory", "Extreme Wind Warning", "Dust Storm Warning",
    "Blowing Dust Advisory", "Blowing Dust Warning", "Dust Advisory", "Flash Flood Warning",
    "Special Weather Statement", "Excessive Heat Warning", "Extreme Heat Warning",
)
_UA = {"User-Agent": "NetMonitor (rcs-fleet-mon.duckdns.org)", "Accept": "application/geo+json"}


async def _acc(db: AsyncSession) -> Account | None:
    return (await db.execute(select(Account).limit(1))).scalars().first()


def _state(acc: Account) -> dict:
    return dict(acc.netbot_state or {})


async def _save(db: AsyncSession, acc: Account, st: dict) -> None:
    acc.netbot_state = st
    await db.commit()


async def say(db: AsyncSession, text: str) -> None:
    from app.services import morning
    try:
        await morning.send(db, text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("netbot say failed: %s", exc)


def _local(now: datetime) -> datetime:
    return now.astimezone(route.tz_now(now))


def _when(dt: datetime, now: datetime) -> str:
    return dt.astimezone(route.tz_now(now)).strftime("%a %-I:%M %p")


def _night_key(now: datetime) -> str:
    loc = _local(now)
    d = loc.date() - timedelta(days=1) if loc.time() < time(6, 0) else loc.date()
    return d.isoformat()


# ── actions shared by commands and the phone shortcut ───────────────────────
async def close_now(db: AsyncSession, now: datetime, who: str, note: str = "Closed early") -> str:
    from app.services import nightly
    acc = await _acc(db)
    if acc is None:
        return "No account."
    if closure.phase(now) == "closed":
        return f"🌙 NETBOT: already closed — alerts paused until {_when(acc.closure_end, now)}."
    end = await nightly.close_until_open(db, acc, now, f"{note} ({who})")
    st = _state(acc)
    st["early_key"] = _night_key(now)   # no early-close check-in tonight
    acc.autoclose_last = _night_key(now)  # and no nightly auto-close post either
    st.pop("pending", None)
    await _save(db, acc, st)
    return f"🌙 NETBOT: got it, we're closed ({who}). Alerts paused until {_when(end, now)}. Send \"open\" to undo."


async def open_now(db: AsyncSession, now: datetime, who: str) -> str:
    acc = await _acc(db)
    if acc is None:
        return "No account."
    if closure.phase(now) not in ("closed", "scheduled", "reopening"):
        return "☀️ NETBOT: we're already open — alerts are on."
    # Straight back to live alerts (no grace): someone says we're open.
    acc.closure_start = acc.closure_end = acc.closure_note = None
    acc.closure_grace_min = None
    from app.services import nightly
    if nightly._in_window(_local(now).time()):
        acc.autoclose_last = _night_key(now)  # don't auto-close again tonight
    st = _state(acc)
    st["early_key"] = _night_key(now)
    st.pop("pending", None)
    await _save(db, acc, st)
    await closure.refresh(db)
    return f"☀️ NETBOT: we're open ({who}). Alerts are back on."


# ── 1. group commands ───────────────────────────────────────────────────────
def is_alert_group(gid: str, alert_gid: str | None) -> bool:
    if not gid or not alert_gid:
        return False
    if gid == alert_gid:
        return True
    return alert_gid == "group." + base64.b64encode(gid.encode()).decode()


async def handle_message(db: AsyncSession, body: str, sender: str, sent_at: datetime) -> None:
    now = datetime.now(timezone.utc)
    if now - sent_at > timedelta(minutes=10):
        return  # old backlog — never act on stale commands
    text = (body or "").strip()
    if not text or len(text) > 40 or text.startswith(("☀", "🌙", "⛈", "🤖", "⚠", "🧪")):
        return
    who = "you" if sender == "me" else sender
    acc = await _acc(db)
    pending = bool(acc and (acc.netbot_state or {}).get("pending"))
    if _CLOSE.match(text) or (pending and re.match(r"^(yes|yep|yeah|confirm(ed)?)[.! ]*$", text, re.I)):
        if pending:
            st = _state(acc)
            st.pop("pending", None)
            await _save(db, acc, st)
            await say(db, f"🌙 NETBOT: thanks {who} — confirmed closed. Alerts stay paused until {_when(acc.closure_end, now)}.")
            return
        await say(db, await close_now(db, now, who))
    elif _NO.match(text) and pending:
        await open_now(db, now, who)
        await say(db, f"⚠️ NETBOT: understood {who} — treating it as an outage. Alerts are back on now.")
    elif _OPEN.match(text):
        await say(db, await open_now(db, now, who))
    elif _STATUS.match(text):
        from app.services import morning
        await say(db, await morning.build(db, now))


# ── 2. early-close check-in ─────────────────────────────────────────────────
async def maybe_early_close(db: AsyncSession, now: datetime) -> None:
    from app.services import nightly
    if closure.phase(now) in ("closed", "scheduled"):
        return
    loc = _local(now)
    if not (EARLY_FROM <= loc.time() < nightly.WINDOW[0]):
        return
    opening = route.opening_on(loc.date())
    if opening is None or now < opening + timedelta(hours=2):
        return  # not a fair day, or too soon after opening (still booting)
    acc = await _acc(db)
    if acc is None:
        return
    st = _state(acc)
    if st.get("early_key") == _night_key(now):
        return
    kiosks = [a for a in (await db.execute(select(Agent).where(Agent.station_group == "kiosk"))).scalars()
              if a.claimed_at and (nightly._iso(a.last_seen_at) or now - timedelta(days=99)) > now - timedelta(days=_STALE_DAYS)]
    if len(kiosks) < 5:
        return
    off = [a for a in kiosks if a.status != "online" or a.powered_off_at is not None]
    if len(off) / len(kiosks) < SHARE:
        return
    end = await nightly.close_until_open(db, acc, now, "Auto: closing early?")
    acc = await _acc(db)
    st = _state(acc)
    st["early_key"] = _night_key(now)
    st["pending"] = now.isoformat()
    acc.autoclose_last = _night_key(now)
    await _save(db, acc, st)
    await say(db, (f"🌙 NETBOT: looks like we're closing early — {len(off)}/{len(kiosks)} kiosks off at "
                   f"{loc.strftime('%-I:%M %p')}. I've paused alerts until {_when(end, now)}.\n"
                   "Reply \"closed\" to confirm, or \"no\" if this is an outage and I'll turn alerts back on."))


# ── 3. weather heads-up ─────────────────────────────────────────────────────
async def maybe_weather(db: AsyncSession, now: datetime) -> None:
    acc = await _acc(db)
    if acc is None or closure.phase(now) == "closed":
        return
    st = _state(acc)
    last = st.get("wx_checked")
    if last and now - datetime.fromisoformat(last) < timedelta(minutes=10):
        return
    loc = _local(now)
    opening = route.opening_on(loc.date())
    ev = route.current_event(now)
    # Only on fair days, from 2 h before opening until 11 PM.
    if opening is None or ev is None or not (opening - timedelta(hours=2) <= now and loc.time() < time(23, 0)):
        return
    st["wx_checked"] = now.isoformat()
    lat, lon = ev["latlon"]
    try:
        async with httpx.AsyncClient(timeout=20, headers=_UA) as c:
            r = await c.get("https://api.weather.gov/alerts/active", params={"point": f"{lat},{lon}"})
            feats = r.json().get("features", []) if r.status_code == 200 else []
    except Exception as exc:  # noqa: BLE001
        logger.debug("weather fetch failed: %s", exc)
        feats = []
    sent = dict(st.get("wx_sent") or {})
    # forget ids older than 2 days
    sent = {k: v for k, v in sent.items() if now - datetime.fromisoformat(v) < timedelta(days=2)}
    for f in feats:
        p = f.get("properties") or {}
        event = p.get("event") or ""
        aid = p.get("id") or f.get("id")
        if event not in WEATHER_EVENTS or not aid or aid in sent:
            continue
        if event == "Special Weather Statement" and not re.search(
                r"thunder|lightning|wind|gust|dust|hail", (p.get("headline") or "") + (p.get("description") or ""), re.I):
            continue
        sent[aid] = now.isoformat()
        until = p.get("ends") or p.get("expires")
        until_s = ""
        if until:
            try:
                until_s = " until " + datetime.fromisoformat(until).astimezone(route.tz_now(now)).strftime("%-I:%M %p")
            except ValueError:
                pass
        head = (p.get("headline") or "").strip()
        await say(db, (f"⛈️ NETBOT weather heads-up for {ev['name']}: {event}{until_s}.\n"
                       f"{head[:220]}\nAn early close is possible — if we shut down, just send \"closed\"."))
    st["wx_sent"] = sent
    await _save(db, acc, st)


# ── 4. phone shortcut token ─────────────────────────────────────────────────
async def shortcut_token(db: AsyncSession, rotate: bool = False) -> str:
    acc = await _acc(db)
    st = _state(acc)
    if rotate or not st.get("shortcut_token"):
        st["shortcut_token"] = secrets.token_urlsafe(24)
        await _save(db, acc, st)
    return st["shortcut_token"]


async def check_token(db: AsyncSession, token: str) -> bool:
    acc = await _acc(db)
    tok = (acc.netbot_state or {}).get("shortcut_token") if acc else None
    return bool(tok) and secrets.compare_digest(tok, token or "")
